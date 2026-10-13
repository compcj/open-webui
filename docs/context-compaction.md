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
| 原版压缩 / `legacy` | 采用上游 v0.12 历史及工具轮间压缩；保留提示词、保留比例与个人／会话阈值覆盖。Uses upstream v0.12 history and tool-round compaction, retaining prompt, retention and threshold overrides.                |
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

| 界面设置 / Setting                       | API / environment field                          | 默认值 / Default                                       |
| ---------------------------------------- | ------------------------------------------------ | ------------------------------------------------------ |
| 模式 / Mode                              | `CONTEXT_COMPACTION_MODE`                        | 未设置，兼容旧开关 / Unset; legacy flag fallback       |
| 全局缺省上下文长度 / Default window      | `CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW`      | 262144 tokens                                          |
| 有效窗口比例 / Effective window          | `CONTEXT_COMPACTION_V2_EFFECTIVE_WINDOW_PERCENT` | 95%                                                    |
| 自动压缩触发比例 / Trigger               | `CONTEXT_COMPACTION_V2_TRIGGER_PERCENT`          | 90%                                                    |
| 提醒时剩余预算比例 / Reminder distance   | `CONTEXT_COMPACTION_V2_REMINDER_PERCENT`         | 总长度的 10% / 10% of the window                       |
| 近期原文预算 / Recent original context   | `CONTEXT_COMPACTION_V2_RECENT_TOKENS`            | 20000 tokens                                           |
| 摘要模型 / Summary model                 | `CONTEXT_COMPACTION_V2_MODEL`                    | 空，使用当前模型 / Empty: current model                |
| 摘要输出上限 / Summary output limit      | `CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS`        | 空，继承摘要模型配置 / Empty: inherit summary model    |
| 摘要输出比例 / Summary output percentage | `CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT`   | 摘要模型窗口的 5% / 5% of the summary model window     |
| 摘要提示词 / Summary prompt              | `CONTEXT_COMPACTION_V2_SUMMARY_PROMPT`           | 空，使用独立 V2 默认提示词 / Empty: built-in V2 prompt |
| 预算提醒 / Budget reminders              | `CONTEXT_COMPACTION_V2_REMINDER_ENABLED`         | true                                                   |
| 提醒提示词 / Reminder prompt             | `CONTEXT_COMPACTION_V2_REMINDER_PROMPT`          | 空，使用默认值 / Empty: built-in prompt                |
| 摘要超时 / Summary timeout               | `CONTEXT_COMPACTION_V2_TIMEOUT_SECONDS`          | 60 秒 / seconds                                        |
| 工具预算 / Tool budgets                  | `CONTEXT_COMPACTION_V2_TOOL_BUDGET_ENABLED`      | true (V2 saved chats only)                             |
| 单个工具上限 / Individual tool limit     | `CONTEXT_COMPACTION_V2_TOOL_RESULT_PERCENT`      | 10% of resolved `C`                                    |
| 工具批次上限 / Tool batch limit          | `CONTEXT_COMPACTION_V2_TOOL_BATCH_PERCENT`       | 30% of resolved `C`                                    |
| 工具摘要提示词 / Tool summary prompt     | `CONTEXT_COMPACTION_V2_TOOL_SUMMARY_PROMPT`      | Empty: built-in task-aware prompt                      |
| 失败重试次数 / Summary retries           | `CONTEXT_COMPACTION_V2_MAX_RETRIES`              | 1                                                      |

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
摘要 token 配置留空时继承摘要模型的输出配置，再与默认 5% 的百分比额度取较大值；两者都未显式配置时仍使用百分比额度。
实际输出还受模型容量及工具分配额度约束，详见下文“摘要请求预算”。普通聊天的输出预留规则不变。

The V2 summary preserves goals, constraints, decisions, progress, failures, next steps and necessary
references. Custom summary instructions are independent of the legacy template; history is appended
as data. Reminder variables include `{remaining_tokens}`, `{threshold}` and `{input_budget}`.
A reminder is omitted if it would exceed the input budget. An empty summary token setting inherits
the summary model's output setting, then takes the larger of that value and the percentage allowance
(default 5%). Model capacity and tool allocations can lower the actual output. Ordinary chat output
reservations stay unchanged; see Summary request budgets below.

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

工具引用与预算提醒的生命周期不同：引用随对应工具结果保存，回放沿用首次生成的文本；预算提醒只属于本次请求。
两者都计入最终模型输入。V2 对工具结果生成摘要或摘录时保留引用映射，原始结果读取仍返回原文；
实际历史压缩仍可能改变缓存前缀。参见[工具引用修复记录](upstream/v0.11.4-tool-citation-cache-followup.md)
及[验证方法](DEVELOPMENT.md#tool-citation-cache-verification)。

Tool citations persist with their results and replay the originally rendered text; budget reminders
exist only for the current request. Both count toward final model input. V2 tool summaries/excerpts
retain citation mappings while raw result reads still return original text. Actual history compaction
can still change the cached prefix. See the [citation fix record](upstream/v0.11.4-tool-citation-cache-followup.md)
and [verification steps](DEVELOPMENT.md#tool-citation-cache-verification).

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

## 摘要请求预算 / Summary request budgets

此规则仅用于 V2 历史摘要和工具摘要，不改变上面的普通聊天输入预算。管理员在 V2 设置中调整
“摘要输出预算比例”，默认 5%，必须为 1–100 的整数；不增加个人或会话覆盖。
百分比以摘要模型自身解析后的窗口为分母，不能使用当前聊天模型窗口或剩余输入空间替代。

These rules apply only to internal V2 history/tool summaries. The admin-only **Summary output budget (%)**
defaults to 5 and accepts integers 1–100. Its denominator is the resolved summary model window,
including base inheritance, global fallback and a smaller Ollama `num_ctx`, rather than the chat
model window or remaining input. Ordinary chat budgets above are unchanged.

```text
Cs = resolved summary model context window
Es = floor(Cs * effective_window_percent / 100)
P  = floor(Cs * summary_output_percent / 100)
A  = explicit summary token setting, else inherited summary model output setting, else 0
R  = max(P, A)
F  = fixed model/summary instructions + request framing + previous handoff, counted per chunk
O  = min(R, floor((Es - F) / 3))
summary input budget = Es - O
```

百分比额度和绝对值配置取较大值，不是互相取较小值；安全容量约束才会下调实际额度。
工具摘要还必须受本次结果分配额度限制，来源、读取标识及其他包装先从额度中扣除。
实际 `O` 写入供应商对应输出参数；没有固定的 8192 tokens 上限。窗口／固定内容变化时重新计算。

The percentage and absolute settings take the larger value; capacity safeguards may then reduce it.
Tool summaries also respect their actual result allocation after source/read identifiers and envelope
overhead are deducted. The provider receives an explicit output cap `O`; there is no fixed 8192-token
summary ceiling. Each chunk recalculates fixed overhead and available capacity.

以摘要窗口 100000、有效比例 95%、摘要比例 5%，且固定内容未触发进一步下调为例：
For a 100000-token summary window, 95% effective window and 5% output ratio, before any further
capacity/tool restriction:

| 绝对配置 / Absolute setting   | 实际输出额度 / Output allowance | 摘要输入预算 / Summary input budget |
| ----------------------------- | ------------------------------: | ----------------------------------: |
| 未设置或 2000 / Unset or 2000 |                            5000 |                               90000 |
| 8000                          |                            8000 |                               87000 |

摘要在供应商参数应用后仍执行独立的发送前预算检查，同时保留防止递归压缩的内部保护。
输入计数包含完整系统提示、上一段摘要、当前文本片段及多模态额度；成功后才推进原文游标。
供应商明确报上下文超限时，当前可变片段额度减半，从同一位置重新组装请求，并让后续分块沿用收紧后的额度。
次数和超时沿用 V2 重试设置；不会反复发送相同的超限请求，也不会重执行工具。
固定指令、已有摘要或单个不可拆媒体块仍无法容纳时，提示调整模型／配置；取消立即停止，原始内容保持完整。

A separate trusted final-send guard checks summary requests after provider parameters while retaining
recursion protection. Counts include all instructions, previous handoff, source fragments and media
allowances. The source cursor advances only after success. Explicit provider context overflow halves
the current variable fragment allowance, rebuilds from the same cursor, and keeps that smaller limit
for subsequent chunks. Existing V2 retry/timeout settings apply; tools are never executed again.
If fixed instructions, prior handoff or an indivisible media block cannot fit, an actionable error
asks for a model/configuration change. Cancellation stops immediately and preserves original data.

## 工具结果预算 / Tool result budgets

仅在已保存 WebUI 聊天的 V2 模式且工具预算开关开启时生效；关闭、原版压缩以及关闭此开关时保持原行为。
原始结果在下一次发送前完整保存，摘要／摘录用于模型上下文，发送前固定采用版本，不覆盖聊天内容。
单个结果比例及批次比例必须是整数且满足 `1 <= individual <= batch <= 100`，按解析后的模型窗口 `C`
计算：`S = floor(C * individual / 100)`，`Q = floor(C * batch / 100)`。
继承模型窗口和更小的 Ollama `num_ctx` 同样影响 `C`；有效窗口比例和输出额度进一步限制可用输入 `B`。
`C = 100000`、默认比例时，`S = 10000`，`Q = 30000`。

These limits apply to saved WebUI V2 chats when enabled. Off, legacy and a disabled tool budget
retain their previous behavior. Raw originals are saved before the next send; projections never replace
them. Integer percentages satisfy `1 <= individual <= batch <= 100`. Individual `S` and batch `Q`
are percentages of resolved `C`, including base-model inheritance and a smaller Ollama `num_ctx`,
rather than percentages of the remaining input budget. Effective-window and configured-output reserves
still constrain `B`. With `C = 100000`, defaults give `S = 10000` and `Q = 30000`.

先应用有效历史检查点，再恢复已固定采用的工具原文／摘要。历史工具按实际采用文本计入固定输入，
不参与新批次分配；没有采用记录的升级／导入数据以原文为基准，不猜测旧缓存。
只有服务端执行路径登记的新结果参与预算；批准恢复沿用原批次，来源回复不同的同名调用独立分组。
空文本、纯图片结果也保护完整调用组，图片占用另计。

```text
S = floor(C × 单次比例 / 100)
Q = floor(C × 整批比例 / 100)
本批剩余额度 = max(0, Q − 同批已采用文本的实际 tokens)
D = min(本批剩余额度, sum(min(新结果原文 tokens, S)))
```

完整固定历史、运行指令、工具定义、媒体及包装，加上新结果预期占用达到 T 或超过 B 时，先压缩旧历史。
近期原文保留使用同一受控文本成本，保护最新用户输入、本批及未完成调用。之后公平分配新结果，
每次处理当前超额原文最大的结果，并列按调用顺序；得到摘要／摘录后按实际长度回收空间、重新分配。
其他原文能放下就直接保留，不为每个结果预先耗尽分配额度。最终连同引用、重复注入和 JSON 包装重新计数；
若仍超过 B，仅调整尚未固定的新结果，通过替换后的完整请求寻找可用摘录长度。无法容纳必需内容才停止。

例如固定输入 120000、历史已采用摘要 10000、新结果 19000，B 为 223000 且单项／批次上限足够时，
约 149000 的总输入应保留新结果原文。历史原文即使很长，也不会再次争夺工具额度。
历史 70000、新批期望 30000、B 为 95000 时则先压缩历史，再决定工具摘要。

Apply valid history checkpoints and restore fixed tool selections before budgeting. Historical selected
text is fixed input, never a competitor for new allowances. Upgraded/imported data without a selection
uses raw content, not guessed cache entries. Only server-registered new results participate. Approval
resumes retain their batch, separate replies stay isolated, and empty/image-only outputs protect pairing.
`S`/`Q` use C; desired new demand is `min(max(0,Q-alreadySelectedTokens),sum(min(newRawTokens,S)))`.

Measure full fixed history/instructions/tools/media/framing plus desired new content. Reaching T or
exceeding B compacts older history first, with recent retention using the same projected costs. Fairly
allocate new results, reduce the largest over-budget original first (call order breaks ties), then
reclaim its actual savings and reallocate. Keep remaining originals when they fit. The final check counts
citations, repeated injection and JSON framing; only unselected new results may shrink, using complete
request measurements rather than an assumed text-token subtraction. Fail if required content cannot fit.
For fixed input 120000 + selected history 10000 + incoming 19000, B=223000 and sufficient S/Q, the roughly
149000-token input keeps the new result raw. Historical raw sizes do not compete again. With 70000 fixed
history + 30000 desired new tokens and B=95000, compact history first.

工具摘要使用 V2 摘要模型、输出配置、超时及重试设置，并结合当前任务保留必要证据、错误、数值与引用。
独立工具摘要提示词留空使用默认值；较小摘要模型按可容纳的块递进总结。
超时、空摘要、超长或无有效缩减时回退为原文摘录，明确标记不完整并提供原始结果 ID。
原生工具、批准恢复、非原生注入工具、代码执行及保存后的重放共用此预算路径；供应商发送重试不重新执行工具。
候选摘要缓存按模型、分支、任务、策略和额度隔离；数据库事务不跨模型生成。
`chat_tool_result_selection` 区分待处理和已采用版本，原文采用记录仅引用源输出，摘要引用不可变候选记录。
完整请求通过预算检查后、供应商发送前按批次原子固定版本；“已采用”表示本次选择发送的版本，不证明供应商已收到。
只保存原文、生成候选、等待批准和手动历史压缩都不固定版本；固定前取消不产生采用记录。
并发提交重新验证来源、数据库胜出版本及整包／批次预算；不满足则返回可重试冲突，且不固定本次其他新结果。
重试不重新执行工具。

历史回放及 `/status` 使用同一只读解析器。任务、比例或摘要策略变化不改写已采用历史；模型窗口缩小由整体
历史压缩处理。模型间隔离，切回原模型恢复其有效版本。来源／祖先编辑使记录失效；关闭工具预算恢复未被历史检查点
覆盖的原文，重新开启仍可复用有效记录。旧缓存保留，但不会自动视为已采用。

Tool summaries use the V2 summary model, output settings, timeouts and retries with task-aware evidence
instructions. Small summary models use progressive chunks. Empty, oversized, ineffective or failed
summaries fall back to explicitly incomplete exact excerpts with source IDs inside the allowance.
Native, approved, injected and code paths share this logic. Candidate caches are scoped by model,
branch, task, policy and allowance; no database transaction spans generation.

`chat_tool_result_selection` distinguishes pending from prepared versions. Original selections reference
source output without duplicate bodies; derived selections pin immutable candidate rows. Commit a batch
only after validating the complete request and before provider transport. Prepared means selected for
send, not confirmed delivery. Saving raw output, generating a candidate, approval waits and manual
history compaction do not adopt versions. Cancellation before preparation leaves no adopted version.
Concurrent transactions revalidate sources, canonical winners and full/batch budgets before commit;
an unsafe merge returns a retryable conflict without fixing other new members or executing tools again.

Send and status share a read-only resolver. Later tasks, percentages or summary policies do not rewrite
selected history; a smaller model window invokes whole-history compaction. Models remain isolated and
switching back restores valid selections. Source/ancestor edits invalidate them. Disabling tool budgets
restores raw text outside valid history checkpoints; re-enabling can reuse valid versions. Old caches
remain stored without being treated as proof of adoption.

模型的 `read_tool_result` 以及界面“查看原始工具结果”从完整原文读取有限长度的精确片段，可查询及分页。
此类结果只缩短原文页，不再生成摘要；新游标绑定签发分支、原文、查询与位置，允许来源未变的后续分支续读。
旧游标兼容检查当前分支及有效祖先；已返回的精确历史文本不改写。读取必须通过聊天访问权限、当前分支祖先和源内容校验，
不能读取其他聊天／兄弟分支，也不能访问任意文件或 URL。界面按纯文本渲染，并显示加载／错误和下一页状态。
删除聊天会同时清理 `chat_tool_result`、`chat_tool_result_context` 和 `chat_tool_result_selection`。

The auxiliary `read_tool_result` tool and **View original tool result** read exact bounded passages,
with query and paging support. These pages may be resized to fit but are never summarized again.
The HTTP reader defaults to 2048 tokens and accepts at most 8192; native reads include JSON
positions/cursor metadata inside the allowance. New opaque cursors bind their issuance branch and support unchanged-source descendants. Legacy cursors
are verified against eligible current/ancestor branches; historical exact pages remain unchanged. A changed
cursor binding returns 400; denied, missing or invalidated originals return 404. Every read verifies
chat access, current branch
ancestry and unchanged source content; it cannot read a different chat/sibling branch or arbitrary
files/URLs. The UI renders plain text with loading, error and paging states. Chat deletion cleans
`chat_tool_result`, `chat_tool_result_context` and `chat_tool_result_selection`.

This extends the Codex-inspired history compaction design with WebUI's saved raw results and branch
permissions. A single-output truncation limit alone does not reserve room for a parallel batch or
prioritize older history; the individual/batch budgets, history-first order and exact rereads work together.
The 10%/30% ratios are configurable project defaults.

## 查看用量和压缩进度 / Usage and compaction status

在聊天中输入 `/status` 并执行可打开状态面板，查看当前模型的占用、窗口 C、输入预算 B 和触发阈值 T。
占用百分比及 `remaining_tokens` 仍以 T 为基准，`input_remaining_tokens` 表示距离 B 的可用输入空间；
例如面板分母 223k 若是阈值，并不表示模型窗口或输入预算也是 223k。
启用工具预算时还显示后端计算的单个／批次上限及最近批次原始／处理后 token 数。
对话仅提示本轮工具结果新发生的摘要／不完整摘录及必要错误，即使关闭普通工具状态也显示。
保留原文、精确回读及复用缓存不额外产生压缩提示，但仍计入状态面板中的预算和用量统计。
完整统计随采用版本保存，记录来源回复、采用版本、处理时间、当时上限、实际分配及缩减原因（单项上限、整批上限、
剩余输入空间或最终请求校验）。刷新后验证模型、分支、源内容和有效采用版本，历史回放不更新时间；
旧通知或分支混合版本无法还原完整批次时明确标记不完整，不以可见通知冒充完整统计。失败／取消以原操作身份结束进度。
以前的有效压缩记录和“查看原始工具结果”按钮保留在原回复，不在当前回复重复显示；刷新、续调及批准恢复按
操作身份合并进度和完成结果。旧聊天按输出／调用身份识别归属，无法确定身份的旧记录保留原位，不删除历史。
面板不是默认常驻显示。模型的压缩用最大上下文长度设为 `100000`，采用默认 95%／90% 且未配置
输出额度时，应显示窗口 `100000`、阈值 `90000`（输入预算 `95000`）；全局 `262144` 仅在模型及其
基础模型都没有配置长度时使用。OpenAI 兼容连接的模型前缀和预设路由不改变这项优先级。

V2 自动压缩会在对应助手消息上显示“正在压缩上下文”，然后显示成功或失败状态。状态历史随聊天保存，
刷新后仍可查看；即使该模型关闭工具状态更新，压缩状态也会显示。它们是界面状态，不会添加到模型输入或
替换原始消息。若窗口或阈值与预期不同，先核对 `/status` 中的实际值、所选模型及其基础模型、管理员模式，
并区分单次输入估算与累计计费用量。回复下方用量 tooltip 的 `input_tokens` 是该条回复涉及的多次调用的累计输入量，
相同历史重复发送也会重复计入；压缩不会清零已累计的用量。它可以大于模型窗口，不能据此判断单次请求超限。

Run `/status` to inspect usage, window C, input budget B and trigger T separately. Percent used and
`remaining_tokens` remain T-based; `input_remaining_tokens` measures remaining capacity to B. Enabled tool budgets add backend-computed individual/batch limits and the last
batch's original/processed token counts. Only newly summarized/excerpted current-reply tool results
and necessary errors show conversation notices, even with ordinary tool statuses disabled. Retained
originals, exact reads and cache reuse are silent but still counted. Batch statistics persist with selections: source reply, selected version, original decision time, then-effective
limits, actual allocations and reasons (single cap, batch cap, remaining input or final request fit). Restore
after model/branch/source/effective-version validation without refreshing historical timestamps. Partial
legacy or mixed-branch records are labelled incomplete; notices alone never imply complete accounting.
Failed/cancelled operations end their original progress identity. Valid earlier notices and raw-view
buttons stay on their original replies; refresh, continuation and approvals merge progress/completion
by operation identity. Legacy output/call identity identifies misplaced records; unknown legacy
records remain in place and no stored history is deleted.
The panel is not permanently visible by default. A model window of `100000`
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
文本可分片递进处理但不会遗漏原文；固定内容或不可拆媒体块无法容纳时按失败处理。

独立的 `chat_context_checkpoint` 表记录聊天、模型、分支锚点、覆盖消息／输出、内容指纹、摘要与版本。
写入时重新核验源内容并检查版本，编辑或并发修改不允许覆盖有效检查点；原始聊天表保留完整内容。
同一模型不同分支通过祖先和内容校验隔离，不同模型不共用摘要。工具结果在下一次发送前保存，以便续调和刷新核验。
切回原版时只应用原版 `contextSummary`，切为关闭时两者都不应用。删除聊天同时清理 V2 检查点。

V2 keeps complete turns and may compact completed tool groups within a long turn. The latest real
user input and unfinished calls stay intact. Summaries are historical data, not elevated system
instructions. Smaller summary models use progressive chunks without omitting source text; fixed
content or indivisible media that cannot fit is reported as a failure rather than silently truncated. Independent checkpoints validate source
fingerprints and versions when written, isolate models and branches, and preserve raw history.
Chat deletion also removes V2 checkpoints.

- 摘要超时、空结果、没有有效缩减或无法满足最终预算时，原请求仍在 `B` 内则保留原历史继续并提示；
  超出 `B` 则明确报错，需重试或调整模型、附件或配置。
- 普通聊天发送明确超限时，只有响应内容尚未输出才强制压缩并重试发送一次，不重新执行工具。
- 生成摘要的请求明确超限时，按上文缩小源片段重试，次数采用摘要重试配置，不递归触发聊天压缩。
- 取消或并发冲突不破坏原始历史与上一个有效检查点。
- 手动压缩通过聊天输入区 `/compact`，按当前模式执行，仅聊天所有者可触发，生成过程中不可手动压缩。

Timeouts, empty summaries, no effective reduction and an over-budget result are failures. A request
that still fits `B` proceeds unchanged with a warning; otherwise an actionable error asks for a retry
or configuration/input adjustment. Explicit provider context-limit errors may trigger one forced
compaction/send retry only before any content is delivered, without repeating tools. Summary-request
overflow instead shrinks source fragments under the configured summary retry count, without recursive
chat compaction. Cancellation
and conflicting writes preserve raw history and the last valid checkpoint. `/compact` follows the
selected mode, requires chat ownership and is unavailable while a response is active.

## 共享状态与精确工具读取 / Shared state and exact tools

多人续写共用同一聊天、模型及有效分支的 V2 检查点和工具结果选用版本，不按参与者另建状态。
参与者的系统指令、变量、终端说明和连接授权留在自己的请求中并计入预算。共享摘要仅接受已保存
分支的原文和已完成输出前缀；个人提示词和变量不进入可复用摘要。服务端使用
`source_scope=saved_history_v1` 记录这一来源。旧记录没有证明时保留数据库原记录，回放原文。
后续参与者不能因为自己的任务或预算设置重写已选历史版本。

Collaborators share checkpoints and tool selections by chat, model and validated branch. Personal
instructions, variables and terminal context remain actor-local and count toward the request budget.
Reusable shared summaries contain only saved branch facts and completed output prefixes. The server
marks this provenance as `saved_history_v1`; older unproven projections replay raw without modifying
historical selections. Approval and stop actions belong to the reply author; manual compaction is
owner-only. Status and raw-result reads recheck current read access and branch identity. Clones do not
inherit reusable checkpoints, selections or cursor authority; clone-only shares expose their snapshot.

Tool Search 保持默认关闭。开启后，工具清单、完整定义和查询结果都计入最终预算。
服务器识别的 `search_tools` 只按完整工具条目缩小，`view_skill` / `read_skill_file` 按精确字符
分页并返回下一偏移；这些内容不做语义摘要，也不显示摘要通知。最小有效条目仍无法容纳时，
返回增加额度或减少工具／技能内容的错误，不向供应商发送破损 schema。

Tool Search defaults off. Its manifest, definitions and results all count. Registered builtin
`search_tools` results shrink only by complete entries; skill reads use exact text prefixes with a
valid `next_offset`. They never use semantic summaries. An allocation too small for one valid entry
fails with actionable guidance. Off replays raw history, legacy uses upstream round compaction,
and V2 uses final preparation/retry hooks; each request follows exactly one algorithm.

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
