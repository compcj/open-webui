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

See the [regression commands](DEVELOPMENT.md#gemini-cache-replay-verification) and
[implementation record](upstream/v0.13.3-gemini-cache-followup.md). Mocked checks do not establish a
live gateway's cache hit rate or cost reduction.
