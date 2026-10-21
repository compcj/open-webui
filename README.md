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

本项目 [compcj/open-webui](https://github.com/compcj/open-webui) 是 Open WebUI 的分支，已完整合入官方 **[v0.12.2](https://github.com/open-webui/open-webui/tree/6a18585ff4c2e5f6805f682222cfbc46dc669ef8)**。以下介绍保留的分支能力和已实现的产品策略；验收结果、限制和完整提交基线见[版本设计决策记录](docs/upstream/v0.12.2-decisions.md)。未来合并参阅[上游合并索引](docs/upstream/README.md)。

本轮核对日期为 **2026-10-22**，代码快照为 `c21cfb72b`；覆盖完整树差异和全部 **73 个分支独有提交**，通过远端完整历史补齐本地浅克隆缺失的 2 个提交。新增功能、行为/设计差异、重要修复及已被上游覆盖的能力分开列出，范围与证据见[差异审计](docs/upstream/v0.12.2-fork-audit.md)。

This fork of Open WebUI integrates the official **[v0.12.2](https://github.com/open-webui/open-webui/tree/6a18585ff4c2e5f6805f682222cfbc46dc669ef8)** release while retaining the capabilities and product policies below. See the [version decision record](docs/upstream/v0.12.2-decisions.md) for implementation status, validation limits, and exact revisions, and the [merge index](docs/upstream/README.md) for future upgrades.

### 新增功能与扩展 / Fork additions and extensions

以下是相对官方 v0.12.2 仍存在的分支增量；“扩展”表示上游已有基础能力，本分支增加配置、运行方式或兼容层。
完整源码、测试和提交索引见[差异审计](docs/upstream/v0.12.2-fork-audit.md)。
The inventory covers current additions and extensions to upstream features, with source/test evidence in the audit.

| 功能 / Feature                                 | 本分支新增或扩展的行为 / Added behavior                                                                                                                                                                  |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Search1API                                     | 新增搜索提供商、API Key、结果规范化和域名过滤；管理员可在 `/search` 与 `/ask` 间全局选择，默认 `/search`。                                                                                               |
| 原生 Gemini / Anthropic                        | 管理员服务器连接显式选择原生消息协议，支持模型发现、文本/图片、流式/非流式、工具审批、签名思考回放和用量；保留 Chat Completions/Responses 与兼容网关。详见[连接指南](docs/native-model-connections.md)。 |
| 连接请求标识 / Request identity                | 可编辑的 User-Agent 模板/自定义值，以及默认关闭的 Session ID Header；服务端 OpenAI 兼容、Gemini/Anthropic、Ollama 共用，按聊天保持会话标识。                                                             |
| Gemini 工具兼容 / Schema compatibility         | 模型显式开启受限 Schema 编译，发送兼容定义、执行前按原始定义校验；个人直连可按连接和无前缀模型 ID 配置。原生 Gemini 协议与此兼容模式是独立选择。                                                         |
| V2 上下文压缩 / Context compaction             | 全局 off/legacy/V2，按模型窗口管理历史、摘要模型和新工具结果预算；保留原始数据、固定历史选用、状态/统计、原文分页和 `read_tool_result`。详见[压缩指南](docs/context-compaction.md)。                     |
| OpenClaw 技能兼容 / Skill compatibility        | 在上游技能基础上增加 OpenClaw frontmatter/附件兼容、ClawHub/GitHub/skills.sh 等 URL 解析及多包选择；保留未知元数据和原始文件，统一到 `data.files` 与版本历史。                                           |
| 终端技能运行 / Terminal skill runtime          | 加载时检查依赖条件、同步固定版本文件、替换 `{baseDir}`；支持显式依赖安装与运行状态显示。发现列表不会同步未使用的技能，终端故障按既有策略降级。                                                           |
| 技能发现授权 / Skill discovery policy          | 管理员管理工作区自动发现白名单，终端按服务器保存的连接 opt-in 开启；终端 home 的 AGENTS.md 每轮独立加载，失败不阻止聊天。                                                                                |
| 按用户与模型记忆 / Remembered choices          | 记忆搜索、图像、知识读取/写入、记忆和笔记开关，以及推理/模型控件选择；重置清除显式值，跨账号隔离，多模型临时选择不污染单模型偏好。                                                                       |
| 命名图像引擎 / Named image engines             | 多组生成/编辑引擎可命名、配对和选择；每组工具说明可定制，可全局禁止编辑，兼容旧单引擎配置并保留图像工具上下文。                                                                                          |
| 独立聊天入口控制 / Chat access controls        | 管理员分别控制临时聊天与无会话上下文的 Direct API Chat，默认开启、管理员豁免；界面与公开 OpenAI/Ollama 路由同步约束。详见[访问控制](docs/chat-access-controls.md)。                                      |
| 引用证据与缓存 / Citation evidence and caching | 搜索摘要、页面正文和导航结果明确区分；按回复首次出现编号，新引用随新工具结果追加，保存/审批/续写保留固定引用和既有输入前缀。                                                                             |
| 受保护的终端文件交付 / Terminal files          | `display_file` 提供绑定用户、连接与原始上下文的签名下载/图片链接及文件卡片；支持隔离 SVG 预览与源码查看。详见[文件交付](docs/TERMINAL_FILES.md)。                                                        |
| 知识库写入 / Knowledge writes                  | 独立默认关闭的写入开关；在已有知识库创建 Markdown/文本，替换、局部编辑或追加检索文本，保留原始上传；部分索引失败如实报告。                                                                               |
| 知识库直接导入 / Direct knowledge import       | 将网页、文件 URL 或当前管理员终端文件直接入库；源正文不经过模型消息，可处理超过模型上下文的文档，沿用解析器、大小/类型和实时权限限制。详见[写入与导入指南](docs/knowledge-write-tools.md)。              |

### 功能与设计决策差异 / Behavior and design differences

表中明确注明沿用上游的部分属于升级兼容约束；分支差异体现在其余配置、权限、数据和执行规则。

| 领域 / Area          | 相对上游保留的合同 / Retained contract                                                                                                                                                                                |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tavily 深度          | 上游已有四档选择；本分支补充严格枚举校验、缺省 `basic` 回填及省略/`null` 更新保留已存值，与正文提取深度独立。                                                                                                         |
| 模型控件与旧推理配置 | 采用上游控件，补充旧档位/自定义字符串/用户记忆迁移；优先级为会话显式→记忆→会话高级→个人高级→控件/模型默认→全局。已有新配置优先，direct/pipe 保持兼容入口。                                                            |
| 技能文件与访问       | `data.files` 为唯一可编辑来源；旧快照不重写，冲突不静默覆盖，编辑要求版本。自动发现授权与资源 ACL 分开，白名单默认空且管理员不豁免；手选和模型预设不受发现开关禁用。                                                  |
| 技能精确读取         | 沿用上游概览/搜索/分页，全部基于同一固定版本、运行时准备文本和绝对字符偏移；精确搜索保留完整匹配及遗漏计数，历史 pin 不授予访问权。                                                                                   |
| 工具开关             | 知识/笔记发现与显式附件读取分开；记忆开关同时控制工具、自动上下文和对话后学习，仍受模型能力、全局/个人设置和用户权限约束。知识写入还须显式 opt-in、模型许可、读取开启及执行时资源写权限；记住搜索开关不代替联网确认。 |
| 知识写入范围         | 有模型/文件夹/聊天挂载时仅写挂载库，否则限当前可写库；挂载和读取不授予写权限，外部库不支持，终端导入只使用当前操作者的管理员连接。                                                                                    |
| 压缩与回放           | 升级只保留 off/legacy，不自动开 V2；原文永不删除。V2 只给新结果分配预算，发送前原子固定选用；重试不重复执行工具，旧历史不随新任务或设置重写。                                                                         |
| 共享聊天隐私         | 共用的 V2 摘要只来自保存的共享历史，个人指令/变量/终端留在当前请求；按回复作者过滤私有元数据、审批和停止，手动 `/compact` 仅所有者使用。                                                                              |
| 终端内容隔离         | 下载强制附件，SVG/HTML 隔离，拒绝 WebUI 同源端口预览；签名链接不因分享聊天转移授权，文件操作始终携带正确聊天上下文。                                                                                                  |
| 默认暴露面           | 个人 Integrations（工具/终端直连）配置入口默认隐藏，已有配置仍可用；模型 Direct Connections 是另一独立入口。Cookie 转发默认关闭，Apps/Tool Search/Realtime 等沿用原有默认，不因整合主动开启。                         |
| 权限失败与 App 写入  | Redis 撤权检查不可用时返回可重试拒绝并保留登录 Cookie。App 私有交互与公开写入分开，App 返回后重新检查写权限，频道/私有群组权限仍适用。                                                                                |
| 原生协议和工具信任   | 协议、Gemini Schema 模式不按 URL/模型名推测；签名思考绑定协议/连接/模型/原文。仅服务器注册的内置身份可做分发和精确结果处理，外部同名工具不获得特权。                                                                  |
| 构建、发布与协作     | 保留分支 GHCR 工作流，移除 PyPI、Docker Hub 复制和 Helm 通知；标准镜像为主要支持目标。根/前后端 AGENTS 为统一规则来源，工具适配文件仅引用，发布仍需单独授权。                                                         |

### 已采用的上游功能及适配 / Adopted upstream features

以下基础能力来自上游，分支适配另行说明。本次累计合入 v0.12.1 与 v0.12.2：修订比较与 Keep both、多模型消息配对、普通通话头像、Azure Realtime、
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

也可让模型直接导入网页、文件链接或当前服务器终端内的文件，支持部署已有解析器能够处理的 PDF、Word 等格式。
来源由后端获取并入库，正文无需经过模型上下文，因此可导入超过模型上下文的文档。一次处理一个来源，
等待结果；沿用部署的文件大小和格式限制。终端导入限管理员配置的终端，个人直连和目录批量导入暂不支持。

有模型、文件夹或当前聊天挂载的知识库时，只能写入这些库；没有挂载时，可选择有写权限的库。
挂载不授予写权限，外部知识库不支持写入。编辑保留原始上传文件，关联同一文件的知识库沿用现有索引同步。
使用示例、文件权限及索引失败恢复见[知识库写入指南](docs/knowledge-write-tools.md)。

In the chat input's tools menu, enable **Knowledge Base** reading, then opt in to **Knowledge Write**.
The model must allow both builtin categories. Writing defaults off and explicit choices are remembered
per user and model; disabling reading pauses writing while retaining the choice. Create `.md`/`.txt`
documents or replace, edit and append to existing retrieval text.

Models can also directly import a web page, document URL or file from the currently selected admin
terminal. Source contents stay outside model context, allowing documents larger than that context;
existing upload limits and configured document loaders still apply. Each call waits for one import.
Personal browser-direct terminals and directory imports are unsupported.

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

模型编辑页可在 **工具 Schema 兼容模式（Tool schema compatibility）** 中选择 **Gemini**，用于原生 Gemini 连接或第三方 OpenAI 兼容网关。
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

- **技能自动发现默认关闭**：管理员在“设置 → 扩展功能（Integrations）”选择工作区技能白名单；白名单仍受原有访问权限约束，管理员也不豁免。终端按管理员连接单独开启，开启后包含现有和以后新增的技能。手动选择和模型预设保持原有行为。<br>**Automatic skill discovery starts disabled**: admins select workspace skills under Settings → Integrations, subject to existing access rights for every role. Terminal connections opt in separately and include present and future terminal skills. Manual selection and model presets retain their behavior.
- **终端 AGENTS.md 独立加载**：每轮读取所选终端 home 下的工作说明；读取失败继续聊天。<br>**Terminal AGENTS.md loads independently** each turn from the selected terminal's home directory; failed reads do not stop chat.
- **个人工具/终端直连的 Integrations 配置入口默认隐藏**，管理员可以开启，已有连接继续可用；与模型 Direct Connections 入口及 Direct API Chat 开关分别控制。Cookie 转发默认关闭。<br>**Personal Integrations configuration for tools/terminals is hidden by default**, can be enabled by an admin, and does not disable existing connections. Model Direct Connections and Direct API Chat have separate controls. Cookie forwarding is opt-in.
- **Redis 撤销检查失败时拒绝请求**并提示重试，保留登录 Cookie；未配置 Redis 的部署保持原有行为。<br>**Unavailable Redis revocation checks reject requests with a retryable error**, preserving login cookies. Deployments without Redis retain their existing behavior.
- **标准镜像为主要支持目标**。slim 面向外部 AI 服务、SQLite/PostgreSQL、pgvector、本地或 S3 文件存储，不提供标准镜像的全部本地处理与集成依赖。<br>**The standard image is the primary supported target**. Slim targets external AI services, SQLite/PostgreSQL, pgvector, and local or S3 storage, with fewer local-processing and integration dependencies.

### 重要 BUG 修复 / Important fixes

以下列出相对官方 v0.12.2 仍有实现差异的重要修复，包括合并时发现的回归。
源码、回归测试和全部分支独有提交见[审计记录](docs/upstream/v0.12.2-fork-audit.md)；测试文件的存在不等于本次已重跑整套应用验收。
These are retained fixes against the pinned upstream baseline; the audit links their implementation and tests.

| 场景 / Scenario          | 修复后的行为 / Result                                                                                                                                            |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 多模型同时比较           | 每个回复独立持有嵌套输入、元数据和请求状态，避免工具结果、V2 分支、缓存或重试状态串到其他回复；同一模型重复选择也隔离。                                          |
| 跨 Redis worker 停止     | 停止意图持久化并等待执行者确认，漏收 Pub/Sub、租约到期或发布失败不再误报停止成功；失败可重试，重复停止不打断清理。                                               |
| 停止与排队/导航竞争      | 未确认停止不提交下一条排队提示、不把回复提前标完；切换聊天或回复后，旧停止请求不能改写新状态。                                                                   |
| 流式中断和完成           | 连接重置时保存缓冲文本与前轮工具结果，未完成输出标记不完整，不执行残缺工具/代码；SSE `[DONE]` 后不再等待 HTTP EOF。                                              |
| 错误恢复与等待提示       | 后端显式恢复才清除旧流错误；静默外部模型显示等待/思考状态，完成、失败或等待用户输入时正确收起。                                                                  |
| 重复/拆分工具调用        | 同轮重复与拼接 JSON 调用使用稳定执行身份；新轮复用供应商 ID 仍可正常执行，审批/重放不重复副作用。已被拆分的旧占位不再一直显示运行中。                            |
| 分发与 Gemini 参数       | 先解析真实注册目标再做权限/原始 Schema 校验与审批；参数不合规不执行，分发错误进入 stateful Responses 续轮，切换模型/审批恢复不能绕过校验。                       |
| V2 预算、摘要与状态      | 历史先处理、新结果公平分配、节省量回收、并发选用原子提交；摘要模型独立防超窗，取消/失败结束同一操作；旧回复提示、静默统计、原文游标及共享来源不串用。            |
| 引用证据与前缀           | 空摘要不伪装正文，搜索/正文来源连续编号；审批、保存、续写保持原始结果与固定引用，避免为追加来源重写全部输入前缀。                                                |
| 模型权限与推理值         | 修复本分支旧控件迁移时遗漏管理员响应中的 access grants；旧推理值迁移/重置、空显式选项及 JSON 类型变化保持语义，自定义推理字符串不误解析为数字/对象。             |
| Responses 推理参数       | 将 `reasoning_effort` 转为 `reasoning.effort`，删除无效的平面键（含 `null`）并保留其他 reasoning 配置；这是当前仍保留的分支修复。                                |
| 偏好保存和输入界面       | 仅按变化字段排队保存，避免并发设置覆盖和跨账号旧保存；修复窄输入区选择器遮挡、滚动和下拉定位，保留上游 Tab/中文输入法补全。                                      |
| 技能导入与编辑           | 修复 ClawHub owner/歧义 slug、URL MIME/重定向 TLS、旧 OpenClaw 文件迁移和版本竞争；概览内容不成为被截断的编辑源，过期修订比较不覆盖新草稿。                      |
| 图像上下文与附件         | 同一生成图片不重复附加；保存、审批恢复和继续对话保留可用视觉输入，编辑关闭时不误注册编辑工具。                                                                   |
| ComfyUI 失败和配置       | 保留提交、执行、获取结果的阶段和安全错误类别，节点详情在 ComfyUI 日志查看；无输出报错，映射新增按钮语义明确，生成/编辑引擎配置不互相覆盖。                       |
| 共享权限和历史语音       | 后续请求、实时订阅、导出及克隆复查当前访问并过滤作者私有数据；旧语音更新不能绕过作者权限，新通话遵循所有者限制；转写回包不覆盖并发新增消息、分支关系和私有语音。 |
| 通话资源与界面边界       | 结束通话后清理迟到的麦克风资源，修正 Kokoro worker 调用；反馈更新检查有效 ID，日期本地化在缺省语言时回退，避免界面边界值引发异常。                               |
| 终端授权和附件           | 文件与比较读取携带聊天上下文；签名链接、内容嗅探/安全响应、跨用户访问及 Redis 故障处理保持隔离，AGENTS.md 不挤占真实用户附件配对。                               |
| 云存储下载缓存           | S3、GCS、Azure 先写邻接临时文件再原子替换，避免并发读到半个文件或失败下载污染已完成缓存。                                                                        |
| Tika 文档提取            | 显式请求 JSON 响应，并保留上游 Tika 版本端点适配，避免上传解析收到不符合预期的返回格式。                                                                         |
| 笔记标题生成             | 使用专用标题任务接口，关闭 Direct API Chat 后仍可生成笔记标题。                                                                                                  |
| npm / Pyodide / 容器构建 | npm 仅对缓存碰撞做有限重试；Pyodide 保留已有元数据、原子写锁并重试暂时下载错误，失败停止构建；Docker 前端构建默认可用 6 GiB Node 堆。                            |
| 发布与 CI                | 发布版本比较使用 push 的固定提交，缺失基线明确失败，长说明截断并附完整链接，排队避免文档推送挤掉版本发布；增加工作流/脚本检查及 Python 3.11/3.12 后端回归 CI。   |

上游已包含终端 HTTP/WS 的基本用户/会话身份传递，以及 Tavily 深度选择、模型控件、版本历史、长技能概览、`.skill`、修订比较、多模型配对、Tab/输入法补全和技能 `/` 选择的 `$` 序列化；这些不再整体计作分支独有功能或修复。上表列出保留的扩展与缺陷修复，历史来源和对应回归覆盖保留在提交索引中。

Upstream already supplies the base terminal identity forwarding, Tavily depth controls, model controls,
version history, skill overviews, `.skill`, revision comparison and multi-model pairing. Their retained
fork adaptations are listed separately; historical fork contributions remain traceable in the commit index.

升级前阅读[依赖、配置和回滚检查](docs/upstream/v0.12.2-decisions.md#升级与回滚)。
Read the [upgrade and rollback checks](docs/upstream/v0.12.2-decisions.md#升级与回滚) before upgrading.

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
