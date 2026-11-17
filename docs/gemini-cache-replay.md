# 工具续轮与 Gemini 签名 / Tool continuations and Gemini signatures

## 适用范围 / Scope

通过 Google 或第三方的 OpenAI Chat Completions 兼容接口使用 Gemini 时，连接协议仍选
**Chat Completions**。网关需要返回并接受 `extra_content.google.thought_signature`。
WebUI 按实际返回字段保存签名，不根据模型名称推测，也不要求开启工具 Schema 兼容模式。
原生 Gemini/Anthropic 连接另有 `reasoning_details` 回放封装，见[原生连接指南](native-model-connections.md)。

For Gemini through a Google or third-party OpenAI-compatible endpoint, select **Chat Completions**.
The endpoint must return and accept `extra_content.google.thought_signature`. WebUI preserves the
actual carrier without model-name inference or a schema compatibility opt-in. Native Gemini/Anthropic
connections retain their separate `reasoning_details` replay envelope.

## 提示词与签名 / Prompts and signatures

- 模型配置的系统提示词在一次聊天请求的首轮发送前解析，写入该回复独立的输入副本。
  同一次请求处理中的工具续轮沿用这份已准备内容，不丢失或重复注入模型提示词。
  新请求仍按其配置和变量重新准备；这不会冻结整段聊天的所有动态上下文。
- 流式工具调用的签名保留在对应 `tool_calls[]` 上，参数完成后才到达的签名也会保存。
  批量分发回放时恢复原始外层调用、参数和签名，保存后加载和审批恢复保留同一签名。
- 消息级签名保留在 assistant 消息上。流式或非流式的签名文本保留空白、空消息和原始响应边界；
  “继续回答”不会把两份独立签名的文本合并。已有工具或代码结果不会因签名处理而再次执行。
- 签名是不可解释的字符串，重复完整值按快照更新，空尾包不能清除已收到的非空签名。
  不把签名当作普通文本片段拼接，也不生成替代签名。既有 `reasoning_details` 路径独立保留。
- 发给其他模型的历史投影会清除旧模型的签名，保存的原始输出保持不变。
  请求过滤器改变目标时也会清理当前发送及工具续轮累计投影。V2 预算和输入指纹包含签名字段。

The configured model system prompt is prepared once on reply-owned input before the first transport
copy. Tool continuations in that request reuse it; a new request still resolves its own settings and
variables. Streamed tool signatures stay on their calls, including late signature-only deltas, batch
wrapper reconstruction, saved replay and approval resumes. Message signatures retain exact text,
whitespace and independently signed Continue responses. Signed text in streams and nonstream
responses is preserved without repeating completed tools or code.

Signatures are opaque complete values. Repeated snapshots replace them; empty deltas do not erase
them. They are neither concatenated as text fragments nor synthesized. Existing `reasoning_details`
remains independent. Model-switch projections remove old signatures without altering stored originals;
request-filter routing changes also clean outgoing and accumulated tool-continuation projections.
V2 counts signature fields and includes them in input fingerprints.

## OpenAI 兼容参数与续轮 / OpenAI-compatible requests

模型编辑页提供两个独立开关；`extra_body` 展开在服务端 OpenAI 兼容传输中处理：

| 设置                                           | 默认 | 行为                                                                                             |
| ---------------------------------------------- | ---- | ------------------------------------------------------------------------------------------------ |
| 回传推理内容 / Replay reasoning content        | 开启 | 保存并回传上游实际返回的 `reasoning_content`，包括空字符串与原始空白；关闭后不回传该字段。       |
| extra_body 兼容模式 / Extra body compatibility | 关闭 | 开启后把 `extra_body` 对象展开到发送请求的顶层；显式顶层参数优先，不做深层合并。非对象返回 400。 |

推理回传不从展示文字、`reasoning` 别名或 `<think>` 标签猜测原始字段，不修复旧历史中已丢失的内容。
已有 `reasoning_details`、原生签名封装和 Ollama `thinking` 保持独立。`extra_body` 展开发生在最终预算检查前；
其中的 `metadata` 不能覆盖 WebUI 的聊天身份。关闭时保持原有嵌套发送方式。
`prompt_cache_key`、`prompt_cache_retention`、`session_id`、`user`、`safety_identifier`、`service_tier`
等已知字符串参数不会因为值看似数字、布尔值或 JSON 而被转换成其他类型；显式 JSON 引号字符串仍可解码。
WebUI 不自动生成缓存键或替中转选择路由。

如果直连 DeepSeek、首次提问就提示缺少 `reasoning_content`，即使已开启回传，也可能来自自动加载的
记忆、技能、终端或知识库上下文。它们不是模型生成的回复，没有对应推理；DeepSeek
[工具调用文档](https://api-docs.deepseek.com/guides/tool_calls#inserting-tool-calls-mid-conversation)
明确区分了客户端插入的调用与模型调用。服务端 Chat Completions 在最终预算检查前将这些已授权的
自动上下文转换成系统上下文，不补造推理内容。实际工具调用仍完整回传上游推理。
该转换只读取服务端冻结上下文的调用身份，不根据工具名称或客户端标记识别，也不修改保存的聊天输出。
这是应在后续上游合并中保留的关键差异 **D04a**；固定上游源码证据、选择系统上下文而非补造空推理的
原因，以及角色规范化和预算边界见[技术决策](upstream/v0.13.3-private-context-followup.md)。

请求过滤器仍在每次请求与续轮运行，并可主动改写前缀或路由模型。每轮输入来自未经过该过滤器加工的副本，
避免累计追加提示词或重复修改工具定义；过滤后注入本次请求已解析的模型提示词。过滤器看到冻结的私有上下文，
审批恢复仍在执行工具前进行过滤校验。不同请求（包括审批恢复）仍有自己的准备阶段。
Stateful Responses 仍只发送新结果及系统上下文，无法改写供应商已持有的历史。

规范化保留全部系统内容块以及已提供的 `cache_control` / `prompt_cache_breakpoint`，新增提示词只注入一次。
工具达到上限或触发重复失败保护时保留工具定义，设置 `tool_choice=none`，把收尾提示放在末尾 user 消息；
供应商继续请求工具也不会触发额外执行。委派工具的模型列表按配置顺序排列，访问权限检查保持不变。
DeepSeek `prompt_cache_hit_tokens` 与其他供应商字段统一统计到 `cache_read_input_tokens`，跨轮只累计一次；
旧历史中仍保存的字段可被分析页读取，已经覆盖丢失的历史总数无法恢复。

**Replay reasoning content** defaults on and replays only the actual upstream field, preserving empty
strings and whitespace. Disabling it leaves `reasoning_details`, native envelopes and Ollama thinking
independent. Old display-only reasoning cannot establish the original field. **Extra body compatibility**
defaults off; enabling it requires an object, expands once before final budgeting, and lets explicit
top-level values win without deep merging. Nested metadata cannot supply WebUI identity. Known cache
and routing string parameters retain their type; no cache keys or routes are generated.

Automatic memory, skill, terminal and knowledge context can otherwise introduce synthetic tool calls
before the first response, triggering DeepSeek's missing-reasoning error even with replay enabled.
Server Chat Completions projects these authorized pairs to system context before final budgeting,
using only the frozen server-side call identities. Actual provider reasoning is never fabricated;
real tool replay, stored outputs, approval snapshots and other protocols retain their existing behavior.
This is retained fork difference **D04a**; see the [technical decision and merge checklist](upstream/v0.13.3-private-context-followup.md).

Request filters still run each round and may deliberately rewrite prefixes or route models. Each sees
a fresh canonical copy, including frozen private context, followed by the request's frozen model prompt.
Approval preflight remains before tool effects. Stateful Responses preserves its reduced continuation
semantics. Existing cache annotations and system blocks survive conversion. Final-answer requests keep
tools with `tool_choice=none` and a trailing user prompt; extra execution remains blocked. Configured
subagent ordering is stable. Cache-read aliases normalize once before multi-call accumulation; missing
measurements remain unknown and overwritten historical totals cannot be recovered.

## 缓存排错 / Cache troubleshooting

这些修复消除 WebUI 在上述路径中丢失模型提示词或签名的问题，不新增显式缓存创建、TTL 或路由控制。
实际缓存命中仍取决于供应商与中转的实现、实际模型/路由、请求间隔和缓存条件。
上下文压缩、工具集合变化、过滤器改写和不同请求间的动态上下文仍可能改变输入前缀。

检查时使用合成对话，对比首轮和工具续轮的实际出站消息、工具定义及模型标识，并核对每次请求的
供应商缓存用量字段；缺失用量字段应记为未知。网关未返回的签名、旧历史中已丢失的签名无法由 WebUI
恢复，不能靠重复执行已经完成的工具来补齐。不要在日志或问题报告中记录真实提示词、签名或密钥。

These changes address lost prompts and signatures in WebUI; they do not introduce explicit cache
creation, TTL or routing controls. Actual hits depend on the provider/gateway, resolved model/route,
timing and cache conditions. Compaction, changed tools, filters and dynamic context across requests
can still change the prefix. Compare synthetic outbound requests and per-request provider usage;
missing cache counters mean unknown. WebUI cannot reconstruct signatures omitted by a gateway or
already lost from old history. Do not repeat completed tools to recover them or log real prompts,
signatures or credentials while investigating.

回归命令见[开发指南](DEVELOPMENT.md#gemini-cache-replay-verification)，原因和本地验收范围见
[修复记录](upstream/v0.13.3-gemini-cache-followup.md)。模拟测试不代表真实中转的缓存命中率或费用降幅。
后续参数、过滤器和统计选择见[输入缓存修复记录](upstream/v0.13.3-input-cache-followup.md)，
回归命令见[兼容性验证](DEVELOPMENT.md#input-cache-compatibility-verification)。

See the [regression commands](DEVELOPMENT.md#gemini-cache-replay-verification) and
[implementation record](upstream/v0.13.3-gemini-cache-followup.md). Mocked checks do not establish a
live gateway's cache hit rate or cost reduction.
