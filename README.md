# Open WebUI 👋

![GitHub stars](https://img.shields.io/github/stars/open-webui/open-webui?style=social)
![GitHub forks](https://img.shields.io/github/forks/open-webui/open-webui?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/open-webui/open-webui?style=social)
![GitHub repo size](https://img.shields.io/github/repo-size/open-webui/open-webui)
![GitHub language count](https://img.shields.io/github/languages/count/open-webui/open-webui)
![GitHub top language](https://img.shields.io/github/languages/top/open-webui/open-webui)
![GitHub last commit](https://img.shields.io/github/last-commit/open-webui/open-webui?color=red)
[![Discord](https://img.shields.io/badge/Discord-Open_WebUI-blue?logo=discord&logoColor=white)](https://discord.gg/5rJgQTnV4s)
[![](https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/open-webui)

Open WebUI is **a home for AI**, a self-hosted AI platform that's **[extensible](https://docs.openwebui.com/features/extensibility/plugin/)**, **[feature-rich](https://docs.openwebui.com/features/)**, user-friendly, and built to run **[entirely offline](https://openwebui.com/sovereign-ai)**. With support for **Ollama**, **OpenAI-compatible APIs**, and administrator-configured **native Gemini and Anthropic APIs**, it gives you a powerful, provider-agnostic interface for both local and cloud-based models.

Passionate about open-source AI? [Join our team →](https://careers.openwebui.com/)

![Open WebUI Demo](./demo.png)

> [!TIP]  
> **Looking for an [Enterprise Plan](https://docs.openwebui.com/enterprise)?** – **[Speak with Our Sales Team Today!](https://docs.openwebui.com/enterprise)**

For more information, be sure to check out our [Open WebUI Documentation](https://docs.openwebui.com/).

For development in this checkout, see [CONTRIBUTING.md](CONTRIBUTING.md),
[development commands](docs/DEVELOPMENT.md), and the shared [agent guide](AGENTS.md).

## 与上游的差异 / Differences from upstream

本项目 [compcj/open-webui](https://github.com/compcj/open-webui) 是 Open WebUI 的分支。本次已完整合并官方 **[v0.12.2](https://github.com/open-webui/open-webui/tree/6a18585ff4c2e5f6805f682222cfbc46dc669ef8)**。以下介绍保留的分支能力和已实现的产品策略；验收结果、限制和完整提交基线见[版本设计决策记录](docs/upstream/v0.12.2-decisions.md)。未来合并参阅[上游合并索引](docs/upstream/README.md)。

This fork of Open WebUI integrates the official **[v0.12.2](https://github.com/open-webui/open-webui/tree/6a18585ff4c2e5f6805f682222cfbc46dc669ef8)** release while retaining the capabilities and product policies below. See the [version decision record](docs/upstream/v0.12.2-decisions.md) for implementation status, validation limits, and exact revisions, and the [merge index](docs/upstream/README.md) for future upgrades.

### 保留的功能 / Retained capabilities

| 功能 / Feature                        | 本分支行为 / Fork behavior                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Search1API                            | 管理员可配置 API Key 和 `/search`、`/ask` 接口，默认 `/search`，保存后全局生效；保留结果规范化和域名过滤。<br>Admins can configure the API key and choose `/search` or `/ask`, defaulting to `/search`; saved settings apply globally with normalized results and domain filtering.                                                           |
| Tavily 搜索深度 / Search depth        | 管理员在“设置 → 网络搜索”选择 Tavily 后，可切换 `ultra-fast` / `fast` / `basic` / `advanced`，保存后全局生效，默认 `basic`。<br>Admins can select `ultra-fast` / `fast` / `basic` / `advanced` under Settings → Web Search → Tavily; the saved setting applies globally and defaults to `basic`.                                              |
| 推理强度 / Reasoning effort           | 普通模型使用上游模型控件，兼容旧标准／自定义档位和记忆；直连等场景保留原入口。<br>Server models use upstream controls with legacy/custom choices and preference migration; direct connections retain the compatible selector.                                                                                                                 |
| OpenClaw 技能 / Skills                | 支持原生技能及 `SKILL.md`、ZIP／`.skill`、直接链接、GitHub、skills.sh、ClawHub 导入；保留未知元数据，附件统一保存为 `data.files` 并接入版本历史；多技能归档提供选择器。<br>Native and OpenClaw skills with file/ZIP/`.skill`/URL imports, preserved metadata, canonical `data.files`, version history, and a picker for multi-skill archives. |
| 终端技能运行 / Terminal skill runtime | 实际加载时检查运行条件、同步文件及替换 `{baseDir}`；依赖安装需显式操作，终端失败时降级。<br>Checks requirements, syncs files, and expands `{baseDir}` when loading; dependency installation is explicit and terminal failures degrade gracefully.                                                                                             |
| 工具与偏好 / Tools and preferences    | 搜索、图像、知识、记忆和笔记控制按用户及模型记忆。关闭发现仍可按原权限读取已附加内容；设置按变化字段排队保存。<br>Remembered per-user/model controls for search, images, knowledge, memory, and notes; attached content retains authorized access and settings use queued field updates.                                                      |
| 图像引擎 / Image engines              | 命名的生成与编辑引擎配对、可配置工具说明、编辑开关及 ComfyUI 错误详情；保留图像上下文并避免重复附件。<br>Named generation/editing engine pairs, tool descriptions, edit controls, ComfyUI error details, and deduplicated image context.                                                                                                      |
| 聊天访问 / Chat access                | 临时聊天与 Direct API Chat 有独立开关，默认开启，管理员豁免；详见[访问控制](docs/chat-access-controls.md)。<br>Independent temporary-chat and Direct API Chat switches, enabled by default with admin exemption; see [access controls](docs/chat-access-controls.md).                                                                         |
| 引用证据 / Citation evidence          | 搜索摘要与正文摘录均可编号引用，但区分证据类型；详细事实和引文需抓取正文，空摘要仅作导航。<br>Numbered search snippets and page excerpts retain distinct evidence types; detailed claims and quotations require fetched content, while empty snippets provide navigation only.                                                                |
| 终端文件 / Terminal files             | 签名链接绑定用户、终端和原始上下文，访问仍需授权；保留静态/SVG 隔离，拒绝同源端口预览。<br>Signed delivery links bind the user, terminal, and original context; access still requires authorization, with isolated static/SVG handling and no same-origin port previews.                                                                      |
| 知识库写入 / Knowledge writes         | 独立开关默认关闭，可在已有知识库新增文本、追加或修改检索文本；每次检查范围和写权限，保留原始上传文件。<br>A separate control defaults off; create text files or append/edit retrieval text in existing knowledge bases, with scope and write-access checks while preserving original uploads.                                                 |

### v0.12.2 新增能力 / New capabilities

本次累计合入 v0.12.1 与 v0.12.2：修订比较与 Keep both、多模型消息配对、普通通话头像、Azure Realtime、
按聊天上下文的终端文件操作、Notebook、批量删除和 ZIP 下载。文件比较读取使用同一聊天上下文。

This integration includes v0.12.1 and v0.12.2: revision comparison and Keep both, paired multi-model
messages, Standard call avatars, Azure Realtime, and per-chat terminal file operations including
notebooks, bulk deletion and ZIP downloads. File comparisons read from the same chat context.

共享聊天仅所有者可发起普通或 Realtime 通话，管理员也不能代替其他所有者；切换到他人的聊天会结束通话。
旧转写继续保留作者归属与私人语音元数据过滤，普通语音听写保持原有权限。

Only the chat owner can start Standard or Realtime calls, including when the participant is an admin.
Switching to another owner's chat ends the call. Existing transcripts retain author attribution and
private metadata filtering; ordinary dictation retains its existing permissions.

启用技能工具后，超过 20000 字符的技能首次加载 5000 字符概览，可用搜索及分页继续读取；
未启用技能工具时仍最多注入 100000 字符。概览、搜索和分页使用同一固定版本及运行时准备后的正文，
中文及 `{baseDir}` 替换后的偏移保持一致。搜索结果按完整条目缩减并标明遗漏，不做语义摘要。

With skill tools enabled, skills longer than 20,000 characters initially load a 5,000-character overview;
search and paging expose the remainder. Without skill tools, injection remains capped at 100,000
characters. Overview, search and pages share the pinned version and prepared runtime text, preserving
Unicode offsets and `{baseDir}` substitutions. Search reduction retains whole entries and marks omissions.

Apps 默认关闭。开启后，只读频道成员可进行私有 App 交互；写入或更新公开消息需要当前写权限。
频道功能开关和私有群组成员检查同样适用，App 返回后会重新校验写权限。

Apps default off. Read-only channel members may use private App interactions; public replies and
message updates require current write access. Channel feature permission and private-group membership
also apply, and write access is checked again after the App responds.

### 知识库写入 / Knowledge writes

在聊天输入区的工具菜单中，保持“知识库”读取开启，再开启独立的“知识库写入”。
模型设置中的内置工具也须允许读取和写入；首次使用默认不启用写入，显式选择按用户及模型记忆。
关闭读取会暂停写入并保留选择。可创建 `.md`／`.txt` 文档，或全文替换、局部修改及追加已有文档的检索文本。

有模型、文件夹或当前聊天挂载的知识库时，只能写入这些库；没有挂载时，可选择有写权限的库。
挂载不授予写权限，外部知识库不支持写入。编辑保留原始上传文件，关联同一文件的知识库沿用现有索引同步。
使用示例、文件权限及索引失败恢复见[知识库写入指南](docs/knowledge-write-tools.md)。

In the chat input's tools menu, enable **Knowledge Base** reading, then opt in to **Knowledge Write**.
The model must allow both builtin categories. Writing defaults off and explicit choices are remembered
per user and model; disabling reading pauses writing while retaining the choice. Create `.md`/`.txt`
documents or replace, edit and append to existing retrieval text.

When the model, folder or chat has attached knowledge bases, those collections define the write scope;
otherwise choose a collection you can write to. Attachments do not grant write permission, and external
knowledge bases are unsupported. Edits preserve original uploads and update indices for collections
sharing the file. See the [knowledge write guide](docs/knowledge-write-tools.md) for examples and recovery.

### 原生模型连接 / Native model connections

管理员可在 **设置 → 连接** 明确选择 Chat Completions、Responses、Gemini 或 Anthropic 协议，
连接官方接口或兼容网关。支持文本、图片、工具调用与审批、流式响应、思考签名和用量；
保留原有连接配置与请求标识控制。协议不会按模型名称或 URL 自动判断，个人浏览器直连保持原有范围。
配置、边界和排错见[原生模型连接指南](docs/native-model-connections.md)。

Admins can select Chat Completions, Responses, Gemini or Anthropic explicitly under **Settings → Connections**,
using official APIs or compatible gateways. Native connections support text, images, tool calls and approvals,
streaming, signed thinking and usage, while retaining existing connection storage and request identity controls.
Protocol is never inferred from a model name or URL. See the [native connection guide](docs/native-model-connections.md)
for setup and scope; personal browser-direct connections retain their existing scope.

### Gemini 工具兼容 / Gemini tool compatibility

模型编辑页可手动选择 **Gemini 工具 Schema 兼容性**，用于原生 Gemini 连接或第三方 OpenAI 兼容网关。
转换器处理引用、联合类型、常量和嵌套结构，并在 WebUI 执行工具前按原始定义校验参数。
模型名称不会自动启用转换。支持范围、网关差异和配置说明见 [Gemini 工具兼容指南](docs/gemini-tool-compatibility.md)。

Model settings offer an explicit **Gemini tool schema compatibility** mode for native Gemini connections and OpenAI-compatible
gateways. It projects references, unions, constants and nested schemas, then validates arguments
against the original definition before WebUI executes tools. Model names never enable it automatically.
See the [compatibility guide](docs/gemini-tool-compatibility.md) for supported conversions and limits.

### 工具引用与输入缓存 / Tool citations and input caching

联网工具的新来源随对应结果追加，连续搜索和抓取不再为更新引用而重写已有系统提示或历史消息。
引用编号在当前回复内保持一致，保存、继续回复和工具审批后仍可使用；原始工具结果完整保留。
这减少了引用处理引起的输入前缀变化；供应商缓存是否命中仍取决于模型、路由、缓存有效期和实际压缩等条件，
不保证固定命中率或费用降幅。详见[修复记录与验证范围](docs/upstream/v0.11.4-tool-citation-cache-followup.md)。

New web sources travel with their tool results. Repeated searches and page fetches preserve earlier
instructions and messages while updating citations. Citation IDs remain consistent within each reply,
including saved history, continuation and tool approval; original tool results remain intact.
This reduces prefix changes caused by citation handling. Cache hits still depend on the model,
provider routing, cache lifetime and actual compaction, with no guaranteed hit rate or cost reduction.
See the [fix record and verification scope](docs/upstream/v0.11.4-tool-citation-cache-followup.md).

### 上下文压缩 / Context compaction

管理员在 **设置 → 界面 → 上下文压缩** 选择 **关闭／原版压缩／V2压缩**。
V2 将延续摘要、近期完整原文和 Token 预算提醒结合，默认上下文长度 **262144**、有效窗口 **95%**、
触发比例 **90%**、近期原文预算 **20000 tokens**；可单独选择摘要模型并调整关键参数。
预算提醒附加在消息末尾，尽量保持已有上下文前缀稳定，以利于输入缓存复用。
V2 默认启用工具结果预算：单个结果最多占模型窗口的 **10%**，同一批次最多 **30%**。
仅为新工具结果分配额度，必要时先压缩较早历史；每次摘要释放的空间会重新分配，其余结果能放下时保留原文。
历史已采用的原文／摘要保持稳定，不因下一轮任务或预算设置变化再次参与工具分配；摘要失败时使用明确标记的不完整摘录。
原始结果完整保存，已压缩结果可在所属回复的状态中查看原文并分页查找；模型也可用 `read_tool_result` 读取精确片段。
只有本轮新发生的工具摘要／摘录才显示压缩提示；保留原文、精确回读及缓存复用不额外提示，旧记录留在原回复。
采用版本与完整批次统计一起保存，刷新后的 `/status` 仍包含这些不产生提示的结果，区分窗口 C、输入预算 B、触发阈值 T
以及历史批次的时间、当时上限和缩减原因。旧统计不完整时明确标注。升级／导入后没有采用记录的工具以原文回放。
V2 设置中的“摘要输出预算比例”默认 **5%**，按摘要模型自身窗口计算，与显式或继承的 token 输出配置取较大值，
再受模型容量及工具额度约束。摘要请求会分块并检查发送预算，明确超限时缩小片段重试；普通聊天预算规则不变。
每个工作区模型可设置“压缩用最大上下文长度”，留空继承基础模型或全局值。
升级时旧开关仅映射为关闭或原版，不自动启用 V2；切换模式保留全部原始聊天与各自摘要。
详见[参数、预算与故障处理指南](docs/context-compaction.md)。

Under **Settings → Interface → Context Compaction**, admins choose **Off / Legacy / V2** globally.
V2 combines continuation summaries, complete recent conversation groups and token budget reminders.
Budget reminders are appended after the conversation to preserve the existing prefix for input cache reuse.
V2 enables tool budgets by default: **10%** of the model window per result and **30%** per batch.
Only new results share tool allowances, with older history compacted first when needed. Each summary
releases space for the remaining results, which stay raw when they fit. Selected historical originals
and summaries remain stable across later tasks and settings; failed summaries use explicitly incomplete excerpts. Complete originals stay saved
and are available from the source reply's compaction status and the model's `read_tool_result` tool.
Only new tool summaries/excerpts for the current reply show compaction notices. Retained originals,
exact reads and cached projections are silent; previous notices stay on their original reply.
Selected versions and full batch statistics are saved together. `/status` retains silent results after
reload and distinguishes window C, input budget B and trigger T, plus historical decision times, limits
and reduction reasons. Incomplete legacy statistics are labelled; upgraded/imported tools without a
selection replay raw content.
The V2 **Summary output budget (%)** defaults to **5%** of the summary model's own window. It takes
whichever is larger: percentage tokens or the explicit/inherited token setting, then applies model
capacity and tool allocation limits. Summary requests are budget-checked and chunked; explicit
context errors retry with smaller fragments. Ordinary chat budget rules stay unchanged.
Defaults are a **262144-token** window, **95%** effective window, **90%** trigger and **20000 tokens**
of recent original context. Summary models and key parameters are configurable; each workspace model
can override or inherit its compaction context window. Upgrades keep the previous off/legacy behavior;
switching modes preserves raw history and each algorithm's summaries. See the
[configuration and troubleshooting guide](docs/context-compaction.md).

### 控件、共享聊天与技能升级 / Controls, shared chats and skill upgrades

模型控件按“会话显式选择 → 用户记忆 → 会话高级参数 → 个人高级参数 → 控件／模型默认 → 全局默认”取值。
默认／重置会清除显式选择和旧记忆，显示实际生效值；新格式已有配置优先，不同时维护两份设置。
多人续写使用各参与者自己的模型、工具、终端和凭证授权；审批与停止只作用于自己的回复，`/compact` 仅聊天所有者可用。

Controls resolve chat choices, remembered choices, chat advanced parameters, personal advanced parameters,
control/model defaults, then global defaults. Reset clears explicit choices and obsolete memory. Shared
chat participants use their own permissions and credentials; approval and stop actions belong to the
reply author, and only the owner can run `/compact`.

Legacy 已采用 v0.12.2 轮间检查点和单轮用量；V2 独立处理最终请求和超窗重试，同一请求只走一套算法。
共享 V2 状态按聊天、模型、有效分支共用，摘要只取已保存的共享历史；个人指令、变量及终端上下文仅用于当前请求。
无法证明来自共享历史的旧摘要保留在数据库中，共享时回放原文。Tool Search 定义保留完整条目，技能精确读取按页缩小；连必要条目也放不下时返回可操作错误。

Legacy uses v0.12.2 between-round checkpoints and per-round usage. V2 independently guards final sends and overflow retries.
Participants share V2 state for the same chat/model/valid branch. Shared summaries contain saved history;
private instructions, variables and terminal context stay in the current request. Old summaries without
shared provenance replay raw content. Tool schemas remain whole entries and skill reads use exact pages;
an entry that cannot fit produces an actionable error.

技能编辑以文件树为唯一来源，旧 OpenClaw 附件无损迁入 `data.files`；冲突或无法转换的数据会报告技能 ID／路径并停止迁移。
更新需带 `expected_version_id`，过期版本返回冲突；缺失版本的旧附件编辑请求要求刷新客户端。
URL 导入保留旧下载描述格式，新客户端请求 `response_format=packages`；两者共用上游网络、重定向、归档和大小限制。
管理员与具有 `skills_import` 权限的用户均可导入。

The skill file tree is authoritative. Legacy OpenClaw attachments migrate losslessly to `data.files`;
conversion conflicts stop migration with a skill ID/path. File edits require `expected_version_id`;
stale versions conflict and old versionless attachment clients must refresh. URL imports retain the
legacy descriptor response and accept `response_format=packages` for new clients. Both use the same
network, redirect, archive and size checks; admins and users with `skills_import` may import.

### Search1API 接口 / Search1API endpoint

管理员在“设置 → 网络搜索”选择 Search1API 后，可配置 API Key 并选择 `/search` 或 `/ask`。
`/search` 使用搜索关键词返回结果；`/ask` 根据自然语言查询自动选择搜索源并按相关性排序。
选择保存后同时适用于聊天内置搜索和自动联网检索，刷新页面或切换搜索提供商不会重置已保存的值。

In Admin Settings → Web Search, select Search1API, configure the API key, and choose `/search` or
`/ask`. `/search` retrieves results using search keywords; `/ask` selects sources from a
natural-language query and ranks results by relevance. The saved selection applies globally to
built-in chat search and automatic web retrieval, and survives reloads and provider changes.

`SEARCH1API_API_KEY` 提供密钥默认值；`SEARCH1API_ENDPOINT=search` 提供接口默认值，允许 `search` 和 `ask`。
未配置接口时使用 `/search`。启用持久化配置时，已保存的管理员设置优先于环境默认值。
配置 API 的 `web.SEARCH1API_ENDPOINT` 严格校验枚举；更新省略字段或传入 `null` 时保留原值。

`SEARCH1API_API_KEY` supplies the default key. `SEARCH1API_ENDPOINT=search` supplies the default
endpoint, accepting `search` or `ask`; an unconfigured endpoint uses `/search`. With persistent
configuration enabled, saved admin settings take precedence over environment defaults.
The configuration API validates `web.SEARCH1API_ENDPOINT`; omitted or `null` updates preserve
the saved selection.

`/ask` 仅接收查询文本，查询最长 500 字符，最多返回 10 条结果；本地仍按域名过滤和结果数量设置筛选。
查询不会被自动截断，接口错误沿用现有错误提示，不自动回退到 `/search`。返回内容仍是搜索结果，
详细事实和引文需继续读取来源正文。接口能力、限制和费用以 [Search1API 官方文档](https://s1.dev/docs/basic/ask)为准。

`/ask` accepts only the query, with a maximum of 500 characters and up to ten returned results;
local domain filters and result-count limits still apply. Queries are not truncated and endpoint
errors use the existing error handling without automatic fallback to `/search`. Results remain
search snippets; fetch source pages for detailed facts and quotations. See the
[Search1API documentation](https://s1.dev/docs/basic/ask) for current capabilities, limits and pricing.

### Tavily 搜索深度 / Tavily search depth

`TAVILY_SEARCH_DEPTH=basic` 提供初始默认值，可设为 `ultra-fast`、`fast`、`basic` 或 `advanced`；启用持久化时，已保存的管理员设置优先于环境默认值。此设置同时适用于聊天内置搜索和联网检索，与网页内容提取的 `TAVILY_EXTRACT_DEPTH` 独立。

`TAVILY_SEARCH_DEPTH=basic` supplies the initial default; accepted values are `ultra-fast`, `fast`, `basic`, and `advanced`. With persistent configuration enabled, saved admin settings take precedence over environment defaults. The setting applies to both built-in chat search and web retrieval, independently of the page loader's `TAVILY_EXTRACT_DEPTH`.

深度值严格校验；更新请求省略此字段或传入 `null` 时保留已有设置。服务能力和费用以 [Tavily 官方说明](https://docs.tavily.com/documentation/api-reference/endpoint/search)为准。<br>Depth values are validated strictly; omitted or `null` updates preserve the saved value. See the [Tavily API documentation](https://docs.tavily.com/documentation/api-reference/endpoint/search) for service capabilities and pricing.

### 连接请求标识 / Connection request identity

管理员在 **设置 → 连接 → 编辑连接 → 高级** 中可选择 Claude Code、Codex、Gemini CLI、OpenCode、Pi、OpenClaw、Hermes 的 User-Agent 模板，也可完全自定义。模板是可编辑的示例，版本号不会自动更新；默认保持原有请求行为。这些选项适用于服务端代理的 OpenAI 兼容、原生 Gemini/Anthropic 和 Ollama 连接，不适用于个人浏览器直连。

**发送 Session ID** 默认关闭，开启后以聊天 ID 绑定会话，可选择或自定义 Header 名称（默认 `x-session-id`）。同一聊天的多轮、重试和工具续接复用同一个值；临时聊天使用首条根用户消息的 ID，避免浏览器连接复用导致串会话。连接验证和无聊天上下文的请求不自动附加 ID。指定的 UA 和 Session ID 优先于 Headers 中的同名项；缓存效果取决于服务商对该 Header 的支持。

Under **Settings → Connections → Edit Connection → Advanced**, admins can select editable User-Agent examples for Claude Code, Codex, Gemini CLI, OpenCode, Pi, OpenClaw and Hermes, or enter any custom value. Template versions do not update automatically. Defaults preserve existing behavior. These controls apply to server-proxied OpenAI-compatible, native Gemini/Anthropic and Ollama connections, excluding personal browser-direct connections.

**Send Session ID** is off by default. When enabled, it sends the chat ID under a selectable/custom header name (default `x-session-id`), stable across turns, retries and tool continuations. Temporary chats use their first root user-message ID instead of the shared browser socket ID. Verification and requests without chat context do not automatically add an ID. Explicit UA/session controls override matching entries in Headers; cache benefits depend on provider support.

### v0.12.2 配置默认值 / Configuration defaults

Apps 默认关闭；Tool Search、强制 MFA 和 Realtime 通话采用上游默认，升级不主动开启，也不自动启用 V2。新增知识文件浏览、资源版本历史和独立插件开关来自上游。<br>Apps default off. Tool Search, mandatory MFA and Realtime calls retain upstream defaults; the upgrade does not enable them or V2. Knowledge file browsing, resource history and independent plugin switches are upstream capabilities.

- **技能自动发现默认关闭**：管理员在“设置 → 集成”选择工作区技能白名单；白名单仍受原有访问权限约束，管理员也不豁免。终端按管理员连接单独开启，开启后包含现有和以后新增的技能。手动选择和模型预设保持原有行为。<br>**Automatic skill discovery starts disabled**: admins select workspace skills under Settings → Integrations, subject to existing access rights for every role. Terminal connections opt in separately and include present and future terminal skills. Manual selection and model presets retain their behavior.
- **终端 AGENTS.md 独立加载**：每轮读取所选终端 home 下的工作说明；读取失败继续聊天。<br>**Terminal AGENTS.md loads independently** each turn from the selected terminal's home directory; failed reads do not stop chat.
- **个人直连配置入口默认隐藏**，管理员可以开启，已有连接继续可用；与 Direct API Chat 开关不同。Cookie 转发默认关闭。<br>**Personal direct-integration configuration is hidden by default**, can be enabled by an admin, and does not disable existing connections. It is distinct from Direct API Chat. Cookie forwarding is opt-in.
- **Redis 撤销检查失败时拒绝请求**并提示重试，保留登录 Cookie；未配置 Redis 的部署保持原有行为。<br>**Unavailable Redis revocation checks reject requests with a retryable error**, preserving login cookies. Deployments without Redis retain their existing behavior.
- **标准镜像为主要支持目标**。slim 面向外部 AI 服务、SQLite/PostgreSQL、pgvector、本地或 S3 文件存储，不提供标准镜像的全部本地处理与集成依赖。<br>**The standard image is the primary supported target**. Slim targets external AI services, SQLite/PostgreSQL, pgvector, and local or S3 storage, with fewer local-processing and integration dependencies.

Responses 参数映射、终端 HTTP/WS 身份传递等与上游等价的修复已按共同实现维护，不再列为分支独有修复。Tika 继续保留 JSON 返回协商，并兼容上游版本处理。升级前阅读[依赖、配置和回滚检查](docs/upstream/v0.12.2-decisions.md#升级与回滚)。

Equivalent upstream fixes, including Responses parameter mapping and terminal HTTP/WS identity forwarding, are maintained together rather than advertised as fork-only fixes. Tika retains JSON response negotiation alongside upstream version handling. Read the [upgrade and rollback checks](docs/upstream/v0.12.2-decisions.md#升级与回滚) before upgrading.

> [!NOTE]
> 下方的 `pip install open-webui` 和 `ghcr.io/open-webui/open-webui` 示例安装上游发行物。使用本分支功能需从本仓库源码构建；参见[开发指南](docs/DEVELOPMENT.md)。
>
> The `pip install open-webui` and `ghcr.io/open-webui/open-webui` examples below install upstream releases. Build this repository's source to use the fork's capabilities; see the [development guide](docs/DEVELOPMENT.md).

## Key Features of Open WebUI ⭐

- 🚀 **Effortless Setup**: Install seamlessly via pip, uv, Docker, or Kubernetes (kubectl, kustomize, or helm), with `:ollama` and `:cuda` tagged images available for container deployments.

- 🤝 **Broad Model & API Integration**: Connect OpenAI-compatible APIs, native **Gemini GenerateContent** and **Anthropic Messages**, alongside local Ollama models. Admin Connections supports official APIs and compatible gateways with explicit protocol selection. OpenAI-compatible examples include **LMStudio, GroqCloud, Mistral, OpenRouter and vLLM**. See the [native connection guide](docs/native-model-connections.md) for setup and boundaries.

- 🔐 **Granular RBAC & User Groups**: Administrators define detailed roles, groups, and permissions, giving each user exactly the access they need. Secure by default, with tailored experiences per group.

- 🧩 **Plugin Support**: Extend Open WebUI with **Filters**, **Actions**, **Pipes**, **Tools**, and **Skills**. Connect external services through **MCP**, **MCPO**, and **OpenAPI tool servers**. Build custom integrations, rate limits, approval flows, data connections, and more.

- 🤖 **Models & Agents**: Wrap any base model with custom instructions, tools, and knowledge to build specialized agents. Supports dynamic variables, per-user/group access control, and community preset imports via [Open WebUI Community](https://openwebui.com/).

- ⚡ **Agentic Execution with [Open Terminal](https://github.com/open-webui/open-terminal)**: Give your agents a terminal and filesystem to carry out multi-step tasks. Let them analyze data, run scripts, fix errors, and produce files directly in chat. Scale to teams with **[Terminals (Enterprise)](https://github.com/open-webui/terminals)** for per-user isolated environments, resource limits, and automatic lifecycle management.

- 📝 **Notes**: A dedicated workspace for content outside conversations. Draft with a rich editor, use AI to rewrite selected text, and attach notes to any chat for full-context injection.

- 📢 **Channels**: Real-time shared spaces where your team and AI models collaborate in one timeline. Tag models to draft or critique, with threads, reactions, pins, and access control.

- 🧠 **Persistent Memory**: The AI remembers facts about you across conversations, carrying context from one chat to the next.

- ✅ **Live Workflow & Message Flow**: Watch the AI build and work through checklists in real time. Queue messages while the AI is still responding; they send automatically when it's ready.

- 📅 **Calendar & AI Scheduling**: Built-in personal and shared calendars with month/week/day views, recurring events, color coding, attendees, and reminders. Models manage your schedule conversationally through native function calling.

- ⏱️ **Automations**: Schedule prompts to run on recurring schedules, with runs surfaced on your calendar and each completed run linking back to the chat it produced.

- 📱 **Responsive Design & PWA**: Seamless experience across desktop, laptop, and mobile, with a Progressive Web App for native app-like feel and offline access on localhost.

- ✒️🔢 **Full Markdown and LaTeX Support**: Comprehensive Markdown and LaTeX capabilities for enriched interaction.

- 🎤📹 **Hands-Free Voice/Video Call**: Integrated voice and video calls with multiple Speech-to-Text providers (Local Whisper, OpenAI, Deepgram, Azure) and Text-to-Speech engines (Azure, ElevenLabs, OpenAI, Transformers, WebAPI).

- 💾 **Persistent Artifact Storage**: Built-in key-value storage API for artifacts, enabling journals, trackers, leaderboards, and collaborative tools with personal and shared data scopes.

- 📚 **Local RAG Integration**: Retrieval Augmented Generation backed by 9 vector databases and multiple content-extraction engines (Tika, Docling, Document Intelligence, Mistral OCR, PaddleOCR-vl, external loaders). Supports hybrid search (BM25 + vector) with reranking and full-context mode. Load documents into chat or pull them from your library with the `#` command.

- 🔍 **Web Search for RAG**: Search the web through dozens of providers including `SearXNG`, `Google PSE`, `Brave Search`, `Kagi`, `Mojeek`, `Tavily`, `Perplexity`, `Firecrawl`, `serpstack`, `serper`, `Serply`, `DuckDuckGo`, `SearchApi`, `SerpApi`, `Bing`, `Jina`, `Exa`, `Sougou`, `Azure AI Search`, and `Ollama Cloud`, injecting results directly into the conversation.

- 🌐 **Web Browsing Capability**: Pull websites into chat with the `#` command followed by a URL, or let the model fetch them on its own when needed.

- 🎨 **Image Generation & Editing**: Create and edit images with multiple engines including OpenAI DALL·E, Gemini, ComfyUI (local), and AUTOMATIC1111 (local), supporting both generation and prompt-based editing.

- ⚙️ **Multi-Model Conversations**: Engage several models at once, harnessing their individual strengths in parallel for the best possible responses.

- 📊 **Usage Analytics & Model Evaluation**: Admin dashboards track message volume, token consumption, and cost across users and models. Evaluate models with a built-in arena, A/B testing, and ELO-based leaderboards.

- 🗄️ **Flexible Database & Storage**: Choose SQLite (with optional encryption) or PostgreSQL, and store files locally or on S3, Google Cloud Storage, or Azure Blob Storage.

- 🧬 **Advanced Vector Database Support**: Pick from 9 vector databases: ChromaDB, PGVector, Qdrant, Milvus, Elasticsearch, OpenSearch, Pinecone, S3Vector, and Oracle 23ai.

- 🪪 **Enterprise Authentication & Provisioning**: Full LDAP/Active Directory integration, SSO via trusted headers and OAuth providers, and SCIM 2.0 automated provisioning for identity providers like Okta, Azure AD, and Google Workspace.

- ☁️ **Cloud-Native File Integration**: Native Google Drive and OneDrive/SharePoint file picking for seamless document import from enterprise cloud storage.

- 🔭 **Production Observability**: Built-in OpenTelemetry support for traces, metrics, and logs, plugging into your existing monitoring stack.

- ⚖️ **Horizontal Scalability**: Redis-backed session management and WebSocket support for multi-worker, multi-node deployments behind load balancers.

- 🌐🌍 **Multilingual Support**: Use Open WebUI in your preferred language with i18n support. We're actively seeking contributors to expand language coverage!

- 🌟 **Continuous Updates**: We're committed to improving Open WebUI with regular updates, fixes, and new features.

- 🛡️ **Transparent Security Process**: Security reports are triaged, fixed, and published as open advisories through a documented responsible-disclosure process. See our [Security Policy](https://github.com/open-webui/open-webui/security).

Want to learn more about Open WebUI's features? Check out our [Open WebUI documentation](https://docs.openwebui.com/features) for a comprehensive overview!

## The Open WebUI Ecosystem 🌐

Open WebUI is the core, surrounded by companion apps and infrastructure that extend what your AI can do, where it can reach, and how you run it:

- 💻 **Open WebUI Computer** ([open-webui/computer](https://github.com/open-webui/computer)): A standalone, mobile-first computer and coding agent that runs on the machine you own. Files, terminal, and git in a browser tab, reachable from your phone. Connect it into Open WebUI as a model, or reach it from Telegram, WhatsApp, and more.

- ⚡ **Open Terminal** and **Terminals (Enterprise)** ([open-webui/open-terminal](https://github.com/open-webui/open-terminal) & [open-webui/terminals](https://github.com/open-webui/terminals)): A self-hosted computing environment that plugs into Open WebUI, giving the AI a place to write code, run it, read output, fix errors, and iterate inside the chat. Terminals gives you per-user isolated containers with separate credentials, resource limits, and network rules. Automatic lifecycle management on Docker or Kubernetes.

- 🔄 **oikb** ([open-webui/oikb](https://github.com/open-webui/oikb)): Feed your Knowledge Bases from 45+ sources (GitHub, Confluence, ServiceNow, Salesforce, Jira, Slack, SharePoint, Notion, and more), keeping the tools your team already uses continuously in sync.

- 🖥️ **Native Desktop App** ([open-webui/desktop](https://github.com/open-webui/desktop)): Run Open WebUI as a native app on macOS, Windows, and Linux. System-wide Spotlight chat bar with screenshot capture, push-to-talk voice, and optional fully-local inference via a built-in llama.cpp engine.

Want to learn more? Check out our [Open WebUI documentation](https://docs.openwebui.com) for more details!

---

We are incredibly grateful for the generous support of our sponsors. Their contributions help us to maintain and improve our project, ensuring we can continue to deliver quality work to our community. Thank you!

## How to Install 🚀

### Installation via Python pip 🐍

Open WebUI can be installed using pip, the Python package installer. Before proceeding, ensure you're using **Python 3.11** to avoid compatibility issues.

1. **Install Open WebUI**:
   Open your terminal and run the following command to install Open WebUI:

   ```bash
   pip install open-webui
   ```

2. **Running Open WebUI**:
   After installation, you can start Open WebUI by executing:

   ```bash
   open-webui serve
   ```

This will start the Open WebUI server, which you can access at [http://localhost:8080](http://localhost:8080)

### Quick Start with Docker 🐳

> [!NOTE]  
> Please note that for certain Docker environments, additional configurations might be needed. If you encounter any connection issues, our detailed guide on [Open WebUI Documentation](https://docs.openwebui.com/) is ready to assist you.

> [!WARNING]
> When using Docker to install Open WebUI, make sure to include the `-v open-webui:/app/backend/data` in your Docker command. This step is crucial as it ensures your database is properly mounted and prevents any loss of data.

> [!TIP]  
> If you wish to utilize Open WebUI with Ollama included or CUDA acceleration, we recommend utilizing our official images tagged with either `:cuda` or `:ollama`. To enable CUDA, you must install the [Nvidia CUDA container toolkit](https://docs.nvidia.com/dgx/nvidia-container-runtime-upgrade/) on your Linux/WSL system.

### Installation with Default Configuration

- **If Ollama is on your computer**, use this command:

  ```bash
  docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
  ```

- **If Ollama is on a Different Server**, use this command:

  To connect to Ollama on another server, change the `OLLAMA_BASE_URL` to the server's URL:

  ```bash
  docker run -d -p 3000:8080 -e OLLAMA_BASE_URL=https://example.com -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
  ```

- **To run Open WebUI with Nvidia GPU support**, use this command:

  ```bash
  docker run -d -p 3000:8080 --gpus all --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:cuda
  ```

### Installation for OpenAI API Usage Only

- **If you're only using OpenAI API**, use this command:

  ```bash
  docker run -d -p 3000:8080 -e OPENAI_API_KEY=your_secret_key -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
  ```

### Installing Open WebUI with Bundled Ollama Support

This installation method uses a single container image that bundles Open WebUI with Ollama, allowing for a streamlined setup via a single command. Choose the appropriate command based on your hardware setup:

- **With GPU Support**:
  Utilize GPU resources by running the following command:

  ```bash
  docker run -d -p 3000:8080 --gpus=all -v ollama:/root/.ollama -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:ollama
  ```

- **For CPU Only**:
  If you're not using a GPU, use this command instead:

  ```bash
  docker run -d -p 3000:8080 -v ollama:/root/.ollama -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:ollama
  ```

Both commands facilitate a built-in, hassle-free installation of both Open WebUI and Ollama, ensuring that you can get everything up and running swiftly.

After installation, you can access Open WebUI at [http://localhost:3000](http://localhost:3000). Enjoy! 😄

### Other Installation Methods

We offer various installation alternatives, including non-Docker native installation methods, Docker Compose, Kustomize, and Helm. Visit our [Open WebUI Documentation](https://docs.openwebui.com/getting-started/) or join our [Discord community](https://discord.gg/5rJgQTnV4s) for comprehensive guidance.

### Troubleshooting

Encountering connection issues? Our [Open WebUI Documentation](https://docs.openwebui.com/troubleshooting/) has got you covered. For further assistance and to join our vibrant community, visit the [Open WebUI Discord](https://discord.gg/5rJgQTnV4s).

#### Open WebUI: Server Connection Error

If you're experiencing connection issues, it’s often due to the WebUI docker container not being able to reach the Ollama server at 127.0.0.1:11434 (host.docker.internal:11434) inside the container . Use the `--network=host` flag in your docker command to resolve this. Note that the port changes from 3000 to 8080, resulting in the link: `http://localhost:8080`.

**Example Docker Command**:

```bash
docker run -d --network=host -v open-webui:/app/backend/data -e OLLAMA_BASE_URL=http://127.0.0.1:11434 --name open-webui --restart always ghcr.io/open-webui/open-webui:main
```

### Keeping Your Docker Installation Up-to-Date

Check our Updating Guide available in our [Open WebUI Documentation](https://docs.openwebui.com/getting-started/updating).

### Using the Dev Branch 🌙

> [!WARNING]
> The `:dev` branch contains the latest unstable features and changes. Use it at your own risk as it may have bugs or incomplete features.

If you want to try out the latest bleeding-edge features and are okay with occasional instability, you can use the `:dev` tag like this:

```bash
docker run -d -p 3000:8080 -v open-webui:/app/backend/data --name open-webui --add-host=host.docker.internal:host-gateway --restart always ghcr.io/open-webui/open-webui:dev
```

### Offline Mode

If you are running Open WebUI in an offline environment, you can set the `HF_HUB_OFFLINE` environment variable to `1` to prevent attempts to download models from the internet.

```bash
export HF_HUB_OFFLINE=1
```

## What's Next? 🌟

Discover upcoming features on our roadmap in the [Open WebUI Documentation](https://docs.openwebui.com/roadmap/).

## License 📜

This project contains code under multiple licenses. The current codebase includes components licensed under the Open WebUI License with an additional requirement to preserve the "Open WebUI" branding, as well as prior contributions under their respective original licenses. For a detailed record of license changes and the applicable terms for each section of the code, please refer to [LICENSE_HISTORY](./LICENSE_HISTORY). For complete and updated licensing details, please see the [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY) files.

## Support 💬

If you have any questions, suggestions, or need assistance, please open an issue or join our
[Open WebUI Discord community](https://discord.gg/5rJgQTnV4s) to connect with us! 🤝

## Security 🛡️

If you believe you've found a security vulnerability, or something that shouldn't be disclosed publicly, please [reach out confidentially through our responsible disclosure program on GitHub](https://github.com/open-webui/open-webui/security). We accept reports only through GitHub, not through any other platform. Thank you for helping us keep Open WebUI secure!

## Star History

<a href="https://star-history.com/#open-webui/open-webui&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
  </picture>
</a>

---

Created by [Timothy Jaeryang Baek](https://github.com/tjbck) - Let's make Open WebUI even more amazing together! 💪
