# 上下文压缩 / Context compaction

管理员在 **设置 → 界面 → 上下文压缩** 全局选择模式。工作区模型编辑页的
**压缩用最大上下文长度**（`meta.context_window`）遵循现有模型编辑权限，随模型导入、导出；
留空继承基础模型，再回退到全局缺省值。它是压缩元数据，不会成为供应商推理参数。

Admins select the global mode under **Settings → Interface → Context Compaction**. Set the
**Maximum context length for compaction** in the workspace model editor. This metadata follows
existing model editing permissions and import/export, inherits from the base model when empty,
and is never sent as a provider inference parameter.

## 三种模式 / Modes

| 模式 / Mode         | 行为 / Behavior                                                                                                                                                                                         |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 关闭 / `off`        | 下次请求重新使用完整原始分支历史，不应用任何压缩检查点。Full raw branch history is used on the next request.                                                                                            |
| 原版压缩 / `legacy` | 保留原有摘要算法、提示词、保留比例和个人／会话阈值覆盖；触发阈值额外受模型上下文长度限制。Existing algorithm, prompts, retention and threshold overrides remain, capped by the model context window.    |
| V2压缩 / `v2`       | 在发送请求前按 Token 预算检查，合并延续摘要、近期完整原文与预算提醒。Checks the final request budget and combines a continuation summary with complete recent conversation groups and budget reminders. |

升级时，未设置新模式的安装继续使用旧开关：关闭映射 `off`，开启映射 `legacy`；
不会自动启用 V2。显式模式优先于 `ENABLE_CONTEXT_COMPACTION`。原版和 V2 的配置及检查点彼此独立，
切换不删除原始消息或摘要；正在生成的一轮保持启动时的模式，下一次请求采用新设置。
新的模式与 V2 参数仅供管理员全局配置，没有个人或会话覆盖。

On upgrade, an unset mode maps the old enable flag to `off` or `legacy`; V2 is opt-in. An explicit
mode takes precedence over `ENABLE_CONTEXT_COMPACTION`. Each algorithm keeps its own settings and
checkpoints. Switching preserves messages and summaries and takes effect on the next request.
V2 parameters have no personal or per-chat overrides.

## 参数 / Settings

环境变量同 API 字段名。启用配置持久化时，数据库中已保存的管理员值优先于环境默认值；
修改 `.env` 不会覆盖已保存值。参考 [环境示例](../.env.example)。

Environment variables use the same names as API fields. Saved admin values override environment
defaults when persistent configuration is enabled; editing `.env` does not replace stored settings.

| 界面设置 / Setting                     | API / environment field                          | 默认值 / Default                                       |
| -------------------------------------- | ------------------------------------------------ | ------------------------------------------------------ |
| 模式 / Mode                            | `CONTEXT_COMPACTION_MODE`                        | 未设置，兼容旧开关 / Unset; legacy flag fallback       |
| 全局缺省上下文长度 / Default window    | `CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW`      | 262144 tokens                                          |
| 有效窗口比例 / Effective window        | `CONTEXT_COMPACTION_V2_EFFECTIVE_WINDOW_PERCENT` | 95%                                                    |
| 自动压缩触发比例 / Trigger             | `CONTEXT_COMPACTION_V2_TRIGGER_PERCENT`          | 90%                                                    |
| 提醒时剩余预算比例 / Reminder distance | `CONTEXT_COMPACTION_V2_REMINDER_PERCENT`         | 总长度的 10% / 10% of the window                       |
| 近期原文预算 / Recent original context | `CONTEXT_COMPACTION_V2_RECENT_TOKENS`            | 20000 tokens                                           |
| 摘要模型 / Summary model               | `CONTEXT_COMPACTION_V2_MODEL`                    | 空，使用当前模型 / Empty: current model                |
| 摘要输出上限 / Summary output limit    | `CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS`        | 空，继承摘要模型配置 / Empty: inherit summary model    |
| 摘要提示词 / Summary prompt            | `CONTEXT_COMPACTION_V2_SUMMARY_PROMPT`           | 空，使用独立 V2 默认提示词 / Empty: built-in V2 prompt |
| 预算提醒 / Budget reminders            | `CONTEXT_COMPACTION_V2_REMINDER_ENABLED`         | true                                                   |
| 提醒提示词 / Reminder prompt           | `CONTEXT_COMPACTION_V2_REMINDER_PROMPT`          | 空，使用默认值 / Empty: built-in prompt                |
| 摘要超时 / Summary timeout             | `CONTEXT_COMPACTION_V2_TIMEOUT_SECONDS`          | 60 秒 / seconds                                        |
| 失败重试次数 / Summary retries         | `CONTEXT_COMPACTION_V2_MAX_RETRIES`              | 1                                                      |

长度及输出上限必须为正整数。比例范围为 1–100（提醒可为 0），触发比例不得超过有效窗口比例；
近期原文预算可为 0，但必须小于全局有效窗口。超时为 1–600 秒，重试为 0–5 次。
近期原文预算是软目标：较小模型窗口、输出额度、固定指令／工具定义和实际摘要长度，均可能减少可选的近期保留原文；
最新用户输入和未完成工具组始终受保护。配置合法不代表每次请求都可压缩到预算内。

Lengths and output limits must be positive integers. Effective/trigger percentages are 1–100
(reminder distance may be zero), and trigger cannot exceed the effective percentage. The recent
budget may be zero but must be smaller than the default effective window. Timeout is 1–600 seconds;
retries are 0–5. Recent raw tokens is a soft target: smaller model windows, output limits, fixed
instructions/tools and the actual generated summary can reduce optional retained history. The current
user input and unfinished tool groups remain protected and must still fit with instructions and summary.

V2 默认提示词保留目标、约束、决定、已完成工作、失败、待办及必要文件／工具引用。
自定义摘要提示词与原版独立，历史由后端附加为资料，不需要原版模板变量。
提醒提示词可使用 `{remaining_tokens}`、`{threshold}`、`{input_budget}`；不会因提醒本身使请求超出预算。
摘要输出上限留空时没有额外固定输出预留，单独指定摘要模型时使用该模型的参数和上下文长度。

The V2 summary preserves goals, constraints, decisions, progress, failures, next steps and necessary
references. Custom summary instructions are independent of the legacy template; history is appended
as data. Reminder variables include `{remaining_tokens}`, `{threshold}` and `{input_budget}`.
A reminder is omitted if it would exceed the input budget. An empty summary output limit inherits
the chosen summary model's configured limit, without an additional fixed reserve.

## 预算提醒位置与缓存 / Reminder placement and caching

预算提醒作为临时 `system` 消息追加在本次请求的消息列表末尾，位于已有历史及工具结果之后，
不改变原消息顺序，也不插入工具调用与结果之间。工具续调先移除上一次提醒，再根据最新预算在末尾生成新提醒；
提醒不写入原始聊天历史。使用 Responses API 时，提醒保留在 `input` 末尾，不提升到顶层 `instructions`，
也不覆盖原有系统提示。

提醒中的剩余 tokens 会变化，将它放在尾部可以保持前面的系统提示和历史前缀稳定，减少提醒造成的缓存失配。
这不保证供应商缓存命中；实际压缩替换历史、改变工具定义或系统提示等仍会影响前缀复用。
参见 [OpenAI 前缀缓存说明](https://developers.openai.com/api/docs/guides/prompt-caching)。
提醒计入最终输入占用；若添加后会超过输入预算，或请求使用 `continue_final_message` 续写最后一条助手消息，
则跳过这条可选提醒，以保留预算和续写语义。

Budget reminders are temporary `system` messages appended after the existing conversation and tool
results. They preserve message order and never separate a tool call from its result. A tool
continuation removes the previous reminder and appends an updated one; reminders are not saved in
raw chat history. Responses API conversion keeps the reminder at the end of `input`, leaving the
original top-level `instructions` intact.

Keeping changing token counts at the end preserves the earlier instruction/history prefix and
reduces avoidable cache misses. Cache hits still depend on the provider; compaction and changes to
tool definitions or instructions can change that prefix. The reminder counts toward final input
usage and is omitted if it would exceed the input budget or displace the final assistant message in
a `continue_final_message` prefill request.

## 预算公式与计数 / Budget calculation

1. `C` = 当前模型 `meta.context_window` → 基础模型 → 全局缺省值。
   Ollama 显式配置较小的正 `num_ctx` 时取较小值。
2. `B = floor(C × effective_window_percent / 100) − configured_output_allowance`。
   输出额度按最终请求的 `max_completion_tokens`、`max_tokens` 或 Ollama `num_predict` 解析；
   未设置正输出额度时为 0，依靠有效窗口余量。
3. `T = min(floor(C × trigger_percent / 100), B)`。
4. 占用达到 `T` 时尝试压缩；`T − 占用 < floor(C × reminder_percent / 100)` 时加入提醒。

With defaults and no configured output allowance, `C = 262144`, `B = 249036`, `T = 235929`;
the reminder distance is `26214` tokens. Compression targets complete recent groups within the
20000-token preference while protecting the current user input and unfinished tool calls.

计数发生在模型参数及知识库、技能、附件、系统提示和工具定义完成注入之后；首轮、工具续调、批准恢复、
代码执行续调都经过相同检查。使用 tiktoken 估算文本，并为多模态内容计入保守额度；供应商分词方式与图像计费
可能不同，因此界面标注估算来源。可匹配到同一请求指纹的供应商单次 `prompt_tokens`、`input_tokens` 或
`prompt_eval_count` 用于校准；累计计费 `total_tokens` 不代表上下文占用。刷新后的查询标注为已保存历史估算，
运行中的实际请求用量事件优先。

Counting happens after model parameters and injected system prompts, knowledge, skills, attachments
and tools are resolved, for every initial and continuation request. Text uses tiktoken; multimodal
content has a conservative allowance. Provider tokenizers differ, so the UI labels the source.
Only single-call provider input usage for the same request fingerprint can calibrate an estimate;
cumulative billing totals are never treated as window occupancy. Reload queries estimate saved
history; live request events report the prepared request.

## 查看用量和压缩进度 / Usage and compaction status

在聊天中输入 `/status` 并执行可打开状态面板，查看当前模型的占用、压缩阈值、窗口和剩余预算。
面板不是默认常驻显示。模型的压缩用最大上下文长度设为 `100000`，采用默认 95%／90% 且未配置
输出额度时，应显示窗口 `100000`、阈值 `90000`（输入预算 `95000`）；全局 `262144` 仅在模型及其
基础模型都没有配置长度时使用。OpenAI 兼容连接的模型前缀和预设路由不改变这项优先级。

V2 自动压缩会在对应助手消息上显示“正在压缩上下文”，然后显示成功或失败状态。状态历史随聊天保存，
刷新后仍可查看；即使该模型关闭工具状态更新，压缩状态也会显示。它们是界面状态，不会添加到模型输入或
替换原始消息。若窗口或阈值与预期不同，先核对 `/status` 中的实际值、所选模型及其基础模型、管理员模式，
并区分单次输入估算与累计计费用量。回复下方用量 tooltip 的 `input_tokens` 是该条回复涉及的多次调用的累计输入量，
相同历史重复发送也会重复计入；压缩不会清零已累计的用量。它可以大于模型窗口，不能据此判断单次请求超限。

Run `/status` in the chat to open the status panel with the selected model's usage, trigger, window
and remaining budget. The panel is not permanently visible by default. A model window of `100000`
with default percentages and no output allowance gives a `90000` trigger and `95000` input budget.
OpenAI connection prefixes and preset routing preserve model metadata precedence over the global
fallback.

Automatic V2 compaction shows progress and success/failure in the corresponding assistant message.
These statuses are saved across reloads and remain visible when model tool-status updates are
disabled. They are UI metadata, not model input or replacements for raw messages. To diagnose an
unexpected trigger, check `/status`, the selected/base model and administrator mode, and distinguish
input estimates from cumulative billed tokens. The reply usage tooltip's `input_tokens` sums input
across model calls for that reply, including history resent on each call. Compaction does not reset
that accumulated usage, so it can exceed the model window without any individual request exceeding it.

## 历史、并发与故障 / History, concurrency and failures

V2 按完整轮次保留近期原文；较长工具循环可在完成的调用／结果组之间压缩，保护最新真实用户输入和未完成调用。
不会截断原始消息。压缩后的摘要作为历史资料注入，不提升为系统指令。摘要模型容量较小时，按可容纳的块递进总结。
单个不能容纳的源消息不能静默截断，按失败处理。

独立的 `chat_context_checkpoint` 表记录聊天、模型、分支锚点、覆盖消息／输出、内容指纹、摘要与版本。
写入时重新核验源内容并检查版本，编辑或并发修改不允许覆盖有效检查点；原始聊天表保留完整内容。
同一模型不同分支通过祖先和内容校验隔离，不同模型不共用摘要。工具结果在下一次发送前保存，以便续调和刷新核验。
切回原版时只应用原版 `contextSummary`，切为关闭时两者都不应用。删除聊天同时清理 V2 检查点。

V2 keeps complete turns and may compact completed tool groups within a long turn. The latest real
user input and unfinished calls stay intact. Summaries are historical data, not elevated system
instructions. Smaller summary models use progressive chunks; a source item that cannot fit is
reported as a failure rather than silently truncated. Independent checkpoints validate source
fingerprints and versions when written, isolate models and branches, and preserve raw history.
Chat deletion also removes V2 checkpoints.

- 摘要超时、空结果、没有有效缩减或无法满足最终预算时，原请求仍在 `B` 内则保留原历史继续并提示；
  超出 `B` 则明确报错，需重试或调整模型、附件或配置。
- 供应商明确返回上下文超限时，只有响应内容尚未输出才强制压缩并重试发送一次，不重新执行工具。
- 取消或并发冲突不破坏原始历史与上一个有效检查点。
- 手动压缩通过聊天输入区 `/compact`，按当前模式执行，生成过程中不可手动压缩，访问权限保持不变。

Timeouts, empty summaries, no effective reduction and an over-budget result are failures. A request
that still fits `B` proceeds unchanged with a warning; otherwise an actionable error asks for a retry
or configuration/input adjustment. Explicit provider context-limit errors may trigger one forced
compaction/send retry only before any content is delivered, without repeating tools. Cancellation
and conflicting writes preserve raw history and the last valid checkpoint. `/compact` follows the
selected mode and is unavailable while a response is active.

## 范围与设计参考 / Scope and references

适用于 WebUI 已保存聊天（包括通过项目连接调用的模型）；不扩展临时／无状态裸 API 请求，
也不接管第三方 Pipe 自行管理的内部上下文。浏览器直连模型的手动压缩需要当前用户的活动连接；
用量查询只读取已保存历史，断线时仍可估算。直连模型使用自身元数据或全局长度，不按同名 ID 读取服务器私有模型参数。
实际生效长度由管理员的模型配置决定，
并不是对供应商宣称窗口长度的自动发现。

This applies to saved WebUI chats. Temporary/stateless API requests and contexts managed internally
by third-party Pipes are outside this feature. Manual compaction of browser-direct models requires
the user's own active connection; stored-history usage queries can run while disconnected. Direct
models use their own metadata or the global window and never resolve private server model parameters
by a matching ID. Administrators configure server model windows; this feature does not automatically
discover a provider's supported capacity.

设计固定参考 OpenAI Codex 提交
[`8a0437ff34c920af41dd83af71bc8474c452fb10`](https://github.com/openai/codex/tree/8a0437ff34c920af41dd83af71bc8474c452fb10)：

- [本地压缩与 20000-token 用户历史保留](https://github.com/openai/codex/blob/8a0437ff34c920af41dd83af71bc8474c452fb10/codex-rs/core/src/compact.rs)。
- [模型有效窗口比例与自动压缩上限](https://github.com/openai/codex/blob/8a0437ff34c920af41dd83af71bc8474c452fb10/codex-rs/protocol/src/openai_models.rs)。
- [TokenBudget 提醒](https://github.com/openai/codex/blob/8a0437ff34c920af41dd83af71bc8474c452fb10/codex-rs/core/src/session/token_budget.rs)和
  [TokenBudget 压缩路径](https://github.com/openai/codex/blob/8a0437ff34c920af41dd83af71bc8474c452fb10/codex-rs/core/src/compact_token_budget.rs)。
- [延续摘要提示词](https://github.com/openai/codex/blob/8a0437ff34c920af41dd83af71bc8474c452fb10/codex-rs/prompts/templates/compact/prompt.md)。

本项目采用“本地摘要＋Token 预算提醒”的组合设计，不直接复刻 Codex 的远程压缩接口或仅启动新窗口的路径。
为支持 Open WebUI 工具、批准恢复和持久化分支，近期保留单位扩展为完整用户轮次及工具配对；
摘要通过现有模型连接生成，检查点独立存储。95%／90% 和近期 20000 的启发来自上述代码；
提醒距离 10%、默认 262144、60 秒／1 次重试是本项目可调默认值，不是 Codex 的通用常量。

This adaptation combines local summarization with budget reminders through existing model
connections. It extends retention to complete turns and paired tool groups and persists separate
branch-aware checkpoints for WebUI continuation and approval flows. The reminder distance, default
window, timeout and retry count are project defaults, not universal Codex constants.

开发验证命令及迁移步骤见 [DEVELOPMENT.md](DEVELOPMENT.md#context-compaction-verification)。
