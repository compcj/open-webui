<h2>Open WebUI</h2>

<p>Open WebUI is <a href="https://docs.openwebui.com/"><strong>a home for AI</strong></a>: an extensible, easy-to-use, self-hosted platform built to run entirely offline. Connect local models through Ollama or use OpenAI-compatible APIs for local and cloud models.</p>

[English](README.md) |
[简体中文](README.zh.md) |
[繁體中文](README.zht.md) |
[한국어](README.ko.md) |
[Deutsch](README.de.md) |
[Español](README.es.md) |
[Français](README.fr.md) |
[Italiano](README.it.md) |
[Dansk](README.da.md) |
[日本語](README.ja.md) |
[Polski](README.pl.md) |
[Русский](README.ru.md) |
[Bosanski](README.bs.md) |
[العربية](README.ar.md) |
[Norsk](README.no.md) |
[Português (Brasil)](README.br.md) |
[ไทย](README.th.md) |
[Türkçe](README.tr.md) |
[Українська](README.uk.md) |
[বাংলা](README.bn.md) |
[Ελληνικά](README.gr.md) |
[Tiếng Việt](README.vi.md)

[![Open WebUI Demo](https://cdn.openwebui.com/open-webui-demo.gif)](https://docs.openwebui.com/)

![GitHub stars](https://img.shields.io/github/stars/open-webui/open-webui?style=social)
![GitHub forks](https://img.shields.io/github/forks/open-webui/open-webui?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/open-webui/open-webui?style=social)
![GitHub repo size](https://img.shields.io/github/repo-size/open-webui/open-webui)
![GitHub language count](https://img.shields.io/github/languages/count/open-webui/open-webui)
![GitHub top language](https://img.shields.io/github/languages/top/open-webui/open-webui)
![GitHub last commit](https://img.shields.io/github/last-commit/open-webui/open-webui?color=red)
[![Discord](https://img.shields.io/badge/Discord-Open_WebUI-blue?logo=discord&logoColor=white)](https://discord.gg/5rJgQTnV4s)
[![](https://img.shields.io/static/v1?label=Sponsor&message=Support&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/open-webui)

[Explore Open WebUI →](https://docs.openwebui.com/)

## 与上游的差异 / Differences from upstream

本项目 [compcj/open-webui](https://github.com/compcj/open-webui) 在官方 **[v0.13.3](https://github.com/open-webui/open-webui/tree/b0e6098e940942281d9c83f3529ea117287a3a22)** 上保留以下扩展与产品策略。本次整合覆盖 v0.12.5 至 v0.13.3 的累计变化。实际提交、验收结果及未覆盖项见[设计决策](docs/upstream/v0.13.3-decisions.md)、[验证报告](docs/upstream/v0.13.3-validation.md)与[完整差异审计](docs/upstream/v0.13.3-fork-audit.md)。

This fork integrates official **v0.13.3**, including cumulative changes since v0.12.4, while retaining the extensions and policies below. The [decision record](docs/upstream/v0.13.3-decisions.md), [validation report](docs/upstream/v0.13.3-validation.md), and [complete audit](docs/upstream/v0.13.3-fork-audit.md) identify exact revisions and verification limits. See the [development guide](docs/DEVELOPMENT.md) and [collaboration guide](docs/agents/README.md) when working on this source.

### v0.13.3 升级要点 / Upgrade behavior

标签页、分屏、Controls 与“+”菜单、侧聊、评论、通知收件箱、分析、Notes、多种文件编辑、Git、MCP Apps、批量工具调用及原生 Agents 来自上游，不能算作分支独有功能。分支适配保留参数记忆、共享与临时上下文、工具执行身份、权限和文件隔离。完整发布变化见 [CHANGELOG](CHANGELOG.md)。

Tabs, split views, Controls and the plus menu, side chats, comments, notifications, analytics, Notes, file editing, Git, MCP Apps, batch tool dispatch and native Agents are upstream capabilities. Fork adaptations preserve remembered choices, shared/temporary contexts, execution identity, permissions and file isolation.

| 项目 / Setting              | 新安装默认与升级行为 / Defaults and upgrades                                                                                                                                                                                                             |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 附件与检索 / Retrieval      | 附件预加载关闭；Top K / Reranker Top K 10；chunk 1024；小文件全文阈值 50000 字符。保留已存显式设置，不自动重建索引。 / Preloading off, K 10, chunks 1024, full-text threshold 50000 characters; saved settings and existing indices remain.              |
| 执行限制 / Execution        | 默认最多 1024 工具轮；四项子代理限制默认不限；显式限制保留，达到工具轮次上限后生成最终答复。 / 1024 tool rounds, unlimited default subagent limits, explicit limits retained, final response after the round limit.                                      |
| 模型 / Models               | `@模型` 切换后续聊天模型，重新生成使用当前栏位模型；保持显式选择、按用户/模型记忆与重置。 / Mentions switch subsequent turns; regeneration uses the current slot model and preserves remembered choices/reset.                                           |
| 布局 / Layout               | 采用上游布局；Show Input Selections 默认关闭。每个 Chat 有独立任务、模型和临时 scope；布局不保存临时正文或票据。 / Upstream layout with input selections off; each Chat owns its state and transient data stays out of saved layouts.                    |
| 隐藏上下文 / Hidden context | 私人指令、技能运行时和终端自动上下文仅用于当前请求及其冻结续接，计入预算，不进入共享摘要或持久聊天输出。真实工具结果照常保存。 / Automatic private/runtime context is request-local and budgeted; explicit tool results retain their normal persistence. |

V2、知识写入、自动技能发现、Auto 工具审批等既有默认关闭能力不会因升级开启。升级需要排空 worker、备份数据库/上传/配置，再协调切换；回滚恢复整套升级前备份，见[升级记录](docs/upstream/v0.13.3-decisions.md)。

The upgrade does not opt users into V2, knowledge writes, automatic skill discovery or Auto tool approval. Drain workers and back up the database, uploads and configuration before coordinated upgrade. Restore the complete pre-upgrade backup when rolling back.

### Tether、Agents 与独立预览 / Tether, Agents and preview

管理员在 Integrations 中独立开启 **Tether**（`ENABLE_TETHER=false`，存储键 `tether.enable`）。开启 Tether 不要求开启个人 Direct Integrations；手工保存的个人工具与终端仍受原开关控制。Tether 不自动获得管理员终端的技能发现或知识导入资格。

原生 Agent 仅用于已保存聊天，每次单 Agent，由 Agent 管理自身上下文；不支持重新生成、继续旧回复或原地分支。离线时保留模型身份，等待恢复或手工切换。停止只有远端执行确认结束才完成；确认丢失时保留未确认状态并阻止下一轮。详见 [Tether/Agents 指南](docs/tether-agents.md)。

强制停止保留已接受的原文并标记输出可能不完整，终端尚未传回的尾部可能丢失。预览保留终端服务端 Cookie，且端口 WebSocket 可沿用上游浏览器权限跟随内部重定向；这些不赋予页面 DOM 存储或主站凭据访问。

Forced Agent stops preserve accepted output and mark a potentially missing remote tail. Proxy previews retain terminal-side cookies and upstream WebSocket redirect permissions; sandboxed pages still cannot access DOM storage or main-site credentials.

**独立来源预览默认关闭**。配置 `TERMINAL_PREVIEW_BASE_URL` 为独立主机名后，通过同一后端的预览专用入口显示 Proxy 网页/端口；仅换端口不构成隔离。生产使用 HTTPS；票据默认 5 分钟，每分钟重新授权续期。严格沙箱不提供网页 Cookie/localStorage，完整浏览器能力使用 Chrome 模式。部署条件、代理示例及当前边界见[独立预览指南](docs/terminal-preview.md)。

Tether has an independent admin switch, off by default. Saved personal tool/terminal connections remain governed by Direct Integrations. Tether does not grant admin terminal discovery or knowledge-import privileges. Native Agents require a saved chat and a single Agent, manage their own context, retain their identity while offline, and require confirmed remote completion before stopping. See the [Agent guide](docs/tether-agents.md).

Isolated Proxy preview requires a separate hostname configured through `TERMINAL_PREVIEW_BASE_URL`, HTTPS outside explicit local development, and a preview-only reverse-proxy route. It defaults off. Tickets last five minutes and active panels renew authorization every minute. Sandboxed pages cannot use DOM cookies/localStorage; use Chrome for full browser capabilities. See [deployment and limits](docs/terminal-preview.md).

在主聊天中选定支持浏览器操作的 Open Terminal 后，模型可以调用 `browser_open_tab` 打开新的空白标签页，再用返回的会话 ID 在同一页导航、读取和点击。交互操作需要终端提供 Chrome。

In the main chat, a model can call `browser_open_tab` on a selected Open Terminal with browser tools. The tool opens a blank tab and returns its session ID for navigation, reading and clicking. These interactions require Chrome on the terminal.

### 新增功能与扩展 / Fork additions and extensions

以下是相对官方 v0.13.3 仍存在的分支增量；“扩展”表示上游已有基础能力，本分支增加配置、运行方式或兼容层。
完整源码、测试和提交索引见[差异审计](docs/upstream/v0.13.3-fork-audit.md)。
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

模型编辑页可配置 **智能评分（0–99 的整数）**，在模型选择列表和 `@` 模型建议中以紫色徽标显示在名称前；留空隐藏，0 分也会显示。评分随模型配置保存，仅用于展示。

Configure an optional **intelligence score (an integer from 0 to 99)** in the model editor. A violet
badge appears before the name in the model picker and `@` model suggestions. Leave it empty to hide
the badge; zero is a valid score. Scores are saved with the model configuration and are for display only.

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
| 终端内容隔离         | 下载强制附件，SVG/HTML 隔离，拒绝 WebUI 同源端口预览；可选独立主机名预览需短期授权；签名链接不因分享聊天转移授权，文件操作始终携带正确聊天上下文。                                                                    |
| 默认暴露面           | 个人 Integrations 默认关闭，关闭时停用已有个人工具/终端连接（含管理员），保留配置；模型 Direct Connections 独立。Cookie 转发默认关闭，Apps/Tool Search/Realtime 等沿用原有默认，不因整合主动开启。                    |
| 权限失败与 App 写入  | Redis 撤权检查不可用时返回可重试拒绝并保留登录 Cookie。App 私有交互与公开写入分开，App 返回后重新检查写权限，频道/私有群组权限仍适用。                                                                                |
| 原生协议和工具信任   | 协议、Gemini Schema 模式不按 URL/模型名推测；签名思考绑定协议/连接/模型/原文。仅服务器注册的内置身份可做分发和精确结果处理，外部同名工具不获得特权。                                                                  |
| 自动上下文与推理兼容 | **关键差异 D04a**：Chat Completions 将自动上下文作为系统消息发送，保留私有快照与真实工具推理。见[技术选择](docs/upstream/v0.13.3-private-context-followup.md)。                                                       |
| 构建、发布与协作     | 保留分支 GHCR 工作流，移除 PyPI、Docker Hub 复制和 Helm 通知；标准镜像为主要支持目标。根/前后端 AGENTS 为统一规则来源，工具适配文件仅引用，发布仍需单独授权。                                                         |

### 已采用的上游功能及适配 / Adopted upstream features

以下基础能力来自上游，分支适配另行说明。此前 v0.12.1 与 v0.12.2 已合入：修订比较与 Keep both、多模型消息配对、普通通话头像、Azure Realtime、
按聊天上下文的终端文件操作、Notebook、批量删除和 ZIP 下载。文件比较读取使用同一聊天上下文。

Previously integrated v0.12.1 and v0.12.2 capabilities include: revision comparison and Keep both, paired multi-model
messages, Standard call avatars, Azure Realtime, and per-chat terminal file operations including
notebooks, bulk deletion and ZIP downloads. File comparisons read from the same chat context.

管理员可在「设置 → 扩展功能 → 终端」中为所有人开启「选择终端时显示文件」，默认关闭。
桌面端开启后，选择终端会打开文件列表。模型通过 `display_file` 展示到标签页的文件会在后台打开，点击文件标签即可查看。

In Admin Settings → Integrations → Terminal, admins can enable **Show Files on Terminal Select** for everyone.
It defaults to off and applies to desktop terminal selection. Files displayed in tabs by `display_file` open in the background.

共享聊天仅所有者可发起普通或 Realtime 通话，管理员也不能代替其他所有者；切换到他人的聊天会结束通话。
旧转写继续保留作者归属与私人语音元数据过滤，普通语音听写保持原有权限。

Only the chat owner can start Standard or Realtime calls, including when the participant is an admin.
Switching to another owner's chat ends the call. Existing transcripts retain author attribution and
private metadata filtering; ordinary dictation retains its existing permissions.

启用技能工具后，正文阈值与概览默认 20000/5000 字符，管理员可配置；较长技能先加载概览，可搜索或分页继续读取；
未启用技能工具时仍最多注入 100000 字符。概览、搜索和分页使用同一固定版本及运行时准备后的正文，
中文及 `{baseDir}` 替换后的偏移保持一致。搜索结果按完整条目缩减并标明遗漏，不做语义摘要。

With skill tools enabled, the configurable content/preview limits default to 20,000/5,000 characters;
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
等待结果；沿用部署的文件大小和格式限制。终端导入限管理员配置的终端，个人直连、Tether 和目录批量导入暂不支持。

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
Personal browser-direct terminals, Tether and directory imports are unsupported.

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

模型配置的系统提示词在一次聊天请求的首轮发送前解析并固定，工具续轮复用同一份内容。
通过 Chat Completions 兼容接口调用 Gemini 时，保留网关返回的
`extra_content.google.thought_signature`，支持流式工具调用、批量调用、保存后重放和审批恢复；
签名文本也保留原文与响应边界。该字段与既有 `reasoning_details` 独立，无需开启 Schema 兼容模式。
说明、排错和已知边界见[工具续轮与 Gemini 签名指南](docs/gemini-cache-replay.md)。

The configured model system prompt is resolved before a chat request's first send and reused by its
tool continuations. Gemini through Chat Completions preserves the gateway's
`extra_content.google.thought_signature` across streaming tool calls, batches, saved replay and
approval resumes. Signed text keeps its original content and response boundaries. This carrier is
independent of existing `reasoning_details` and the optional schema compatibility profile.
See the [continuation and Gemini signature guide](docs/gemini-cache-replay.md) for scope and troubleshooting.

模型编辑页提供 **回传推理内容 / Replay reasoning content**（默认开启）：只回传上游实际返回的
`reasoning_content`，关闭后不回传该字段，已有 `reasoning_details` 和 Gemini 签名独立保留。
服务端 Chat Completions 将自动加载的记忆、技能和终端等上下文作为系统上下文发送，避免首轮合成工具调用
触发 DeepSeek 思考模式的推理回传校验；模型实际生成的工具调用仍按原始推理回放。
这是相对官方 v0.13.3 的关键传输差异：在角色转换和最终预算前处理，按服务端身份识别自动上下文，
不按模型名猜测，也不改变 Responses、原生协议或已有审批快照。详见[D04a 技术决策与合并检查](docs/upstream/v0.13.3-private-context-followup.md)。
**extra_body 兼容模式 / Extra body compatibility** 默认关闭；开启后展开自定义参数中的 `extra_body` 对象，
显式顶层参数优先。缓存/会话标识字符串保持原值；请求过滤器每轮处理未重复加工的输入，模型提示词在本次请求内冻结。
工具收尾保留定义、设置 `tool_choice=none` 并在末尾添加 user 提示，后端继续阻止额外执行。
DeepSeek 缓存读取计数纳入多轮统计；未返回计数仍表示未知。详见[输入缓存兼容说明](docs/gemini-cache-replay.md#openai-兼容参数与续轮--openai-compatible-requests)。

Model settings offer **Replay reasoning content** (on by default), preserving only the provider's actual
`reasoning_content`, independently of `reasoning_details` and Gemini signatures. Server Chat Completions
sends automatically loaded memory, skills and terminal context as system context, avoiding synthetic
first-turn tool calls that fail DeepSeek thinking validation. Actual model calls retain original reasoning.
This retained difference from upstream v0.13.3 is protocol-scoped, runs before role conversion and final
budgeting, and preserves frozen internal snapshots. See the [D04a technical decision](docs/upstream/v0.13.3-private-context-followup.md).
**Extra body compatibility**
(off by default) flattens an `extra_body` object with explicit top-level precedence. Cache/session strings
stay strings, filters receive fresh input each round, and model prompts remain frozen within the request.
Final stops retain tools with `tool_choice=none` and a trailing user instruction; backend execution remains
blocked. Multi-call cache accounting includes DeepSeek counters, with missing measurements kept unknown.

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
采用版本与完整批次统计一起保存；普通聊天刷新后的 `/status` 仍包含这些不产生提示的结果，临时聊天仅在当前页面及 scope 有效期间保留。状态区分窗口 C、输入预算 B、触发阈值 T
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
Selected versions and full batch statistics are saved together. Saved chats retain silent results in
`/status` after reload; temporary chats retain them only while the current page and scope remain valid.
Status distinguishes window C, input budget B and trigger T, plus historical decision times, limits
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

Legacy 已接入 v0.12.4 的临时聊天和短历史改进，并保留轮间检查点和单轮用量；V2 独立处理最终请求和超窗重试，同一请求只走一套算法。
共享 V2 状态按聊天、模型、有效分支共用，摘要只取已保存的共享历史；个人指令、变量及终端上下文仅用于当前请求。
无法证明来自共享历史的旧摘要保留在数据库中，共享时回放原文。Tool Search 定义保留完整条目，技能精确读取按页缩小；连必要条目也放不下时返回可操作错误。

Legacy includes v0.12.4 temporary-chat and short-history improvements, retaining between-round checkpoints and per-round usage. V2 independently guards final sends and overflow retries.
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

### 保留的整合行为与临时 V2 / Retained integration and temporary V2

完整合入 v0.12.3/v0.12.4 的模型自定义工具、MCP 2.3.0、流式存储及互动恢复、逐键聊天参数、共享新分支、可见控件、文档批注与选区询问，以及加密频道。加密频道全局与普通用户权限默认关闭；Apps、Tool Search、Realtime、V2 不因升级启用。上游完整发布记录保留在 [CHANGELOG](CHANGELOG.md)。

The merge includes v0.12.3/v0.12.4 custom model tools, MCP 2.3.0, streaming and interaction recovery, chat parameter patches, collaborative branches, visible controls, document comments and selection queries, and encrypted channels. Encrypted channels default off globally and for ordinary users. The upgrade does not enable Apps, Tool Search, Realtime or V2.

Controls／Params 权限控制面板显示；合法选项及已保存个人参数仍可使用，模型访问授权不变。协作者可从他人消息创建新分支，其他查看者保留原分支。Access 独立即时保存；非法修改返回 403，原授权不变。技能正文/概览管理员限额默认 20000/5000 字符，无技能工具时仍限 100000 字符。

Controls/Params permissions govern panel visibility; valid options and saved personal parameters remain usable, subject to model access. Collaborators can create a new branch from another participant's message without changing other viewers' selected branch. Access changes save immediately through dedicated endpoints; denied updates leave existing grants intact. Skill body/preview limits default to 20000/5000 characters, with the 100000-character tool-disabled cap retained.

临时聊天支持完整 V2：历史压缩、新工具结果预算、原文分页、状态和 `/compact`。对话只在当前页面内存保留，刷新、新建或离开不恢复完整历史。后台缓存默认空闲 30 分钟、断线宽限 5 分钟、最长 24 小时；每上下文 64 MiB、每用户 128 MiB/4 个、每 store 512 MiB。配置 Redis 时故障返回可重试 503；未配置 Redis 只支持单实例单 worker。Redis 持久化沿用部署策略，缓存不保证永不落盘。停止与保存普通聊天需执行者完成确认，存储故障不导致工具重执行。详见[临时 V2 指南](docs/temporary-context-v2.md)。

Temporary chats support V2 history compaction, tool-result budgets, exact raw reads, status and `/compact`. Conversation state lives in the current page; refresh, navigation and new chats do not restore it. Cache defaults are 30 minutes idle, 5 minutes disconnected and 24 hours maximum, with 64 MiB per context, 128 MiB/four contexts per user and 512 MiB per store. Configured Redis failures return retryable 503; deployments without Redis require a single instance and worker. Redis persistence follows deployment policy. Saving or closing waits for executor completion, and storage failures never authorize repeating tools. See the [temporary V2 guide](docs/temporary-context-v2.md).

### 保留的配置默认值 / Retained configuration defaults

Apps 默认关闭；Tool Search、强制 MFA 和 Realtime 通话采用上游默认，升级不主动开启，也不自动启用 V2。新增知识文件浏览、资源版本历史和独立插件开关来自上游。<br>Apps default off. Tool Search, mandatory MFA and Realtime calls retain upstream defaults; the upgrade does not enable them or V2. Knowledge file browsing, resource history and independent plugin switches are upstream capabilities.

- **技能自动发现默认关闭**：管理员在“设置 → 扩展功能（Integrations）”选择工作区技能白名单；白名单仍受原有访问权限约束，管理员也不豁免。终端按管理员连接单独开启，开启后包含现有和以后新增的技能。手动选择和普通 UI 模型预设默认立即加载，懒加载默认关闭，mention 始终立即加载。<br>**Automatic skill discovery starts disabled**: admins select workspace skills under Settings → Integrations, subject to existing access rights for every role. Terminal connections opt in separately and include present and future terminal skills. Manual selection and ordinary UI model presets load immediately by default; lazy loading is opt-in and mentions always load immediately.
- **终端 AGENTS.md 独立加载**：每轮读取所选终端 home 下的工作说明；读取失败继续聊天。<br>**Terminal AGENTS.md loads independently** each turn from the selected terminal's home directory; failed reads do not stop chat.
- **个人 Integrations 默认关闭**，关闭时已有个人工具/终端连接也停用，管理员同样受限；配置保留，重新开启恢复。模型 Direct Connections 与 Direct API Chat 分别控制。Cookie 转发默认关闭。<br>**Personal Integrations default off** and disable existing personal tool/terminal connections for every role, including admins. Configuration is retained and restored on re-enable. Model Direct Connections and Direct API Chat have separate controls. Cookie forwarding is opt-in.
- **Redis 撤销检查失败时拒绝请求**并提示重试，保留登录 Cookie；未配置 Redis 的部署保持原有行为。<br>**Unavailable Redis revocation checks reject requests with a retryable error**, preserving login cookies. Deployments without Redis retain their existing behavior.
- **标准镜像为主要支持目标**。slim 面向外部 AI 服务、SQLite/PostgreSQL、pgvector、本地或 S3 文件存储，不提供标准镜像的全部本地处理与集成依赖。<br>**The standard image is the primary supported target**. Slim targets external AI services, SQLite/PostgreSQL, pgvector, and local or S3 storage, with fewer local-processing and integration dependencies.

### 重要 BUG 修复 / Important fixes

以下列出相对官方 v0.13.3 仍有实现差异的重要修复，包括合并时发现的回归。
源码、回归测试和全部分支独有提交见[审计记录](docs/upstream/v0.13.3-fork-audit.md)；测试文件的存在不等于本次已重跑整套应用验收。
These are retained fixes against the pinned upstream baseline; the audit links their implementation and tests.

| 场景 / Scenario          | 修复后的行为 / Result                                                                                                                                            |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 多模型同时比较           | 每个回复独立持有嵌套输入、元数据和请求状态，避免工具结果、V2 分支、缓存或重试状态串到其他回复；同一模型重复选择也隔离。                                          |
| 跨 Redis worker 停止     | 停止意图持久化并等待执行者确认，漏收 Pub/Sub、租约到期或发布失败不再误报停止成功；失败可重试，重复停止不打断清理。                                               |
| 停止与排队/导航竞争      | 未确认停止不提交下一条排队提示、不把回复提前标完；切换聊天或回复后，旧停止请求不能改写新状态。                                                                   |
| 流式中断和完成           | 连接重置时保存缓冲文本与前轮工具结果，未完成输出标记不完整，不执行残缺工具/代码；SSE `[DONE]` 后不再等待 HTTP EOF。                                              |
| 首段输出前断流           | 服务器 Chat Completions 在尚未收到正文、思考、工具等有效事件时，响应传输不完整可自动重试一次；复用原请求，不重复执行工具，停止或临时聊天关闭后不重试。           |
| 错误恢复与等待提示       | 后端显式恢复才清除旧流错误；静默外部模型显示等待/思考状态，完成、失败或等待用户输入时正确收起。                                                                  |
| 重复/拆分工具调用        | 同轮重复与拼接 JSON 调用使用稳定执行身份；新轮复用供应商 ID 仍可正常执行，审批/重放不重复副作用。已被拆分的旧占位不再一直显示运行中。                            |
| 分发与 Gemini 参数       | 先解析真实注册目标再做权限/原始 Schema 校验与审批；参数不合规不执行，分发错误进入 stateful Responses 续轮，切换模型/审批恢复不能绕过校验。                       |
| V2 预算、摘要与状态      | 历史先处理、新结果公平分配、节省量回收、并发选用原子提交；摘要模型独立防超窗，取消/失败结束同一操作；旧回复提示、静默统计、原文游标及共享来源不串用。            |
| 引用证据与前缀           | 空摘要不伪装正文，搜索/正文来源连续编号；审批、保存、续写保持原始结果与固定引用，避免为追加来源重写全部输入前缀。                                                |
| 模型权限与推理值         | 修复本分支旧控件迁移时遗漏管理员响应中的 access grants；旧推理值迁移/重置、空显式选项及 JSON 类型变化保持语义，自定义推理字符串不误解析为数字/对象。             |
| Responses 推理参数       | 将 `reasoning_effort` 转为 `reasoning.effort`，删除无效的平面键（含 `null`）并保留其他 reasoning 配置；这是当前仍保留的分支修复。                                |
| DeepSeek 首轮思考请求    | 自动上下文不再伪装成工具回复，避免首次提问缺少推理而被拒绝；保留真实推理、冻结前缀和工具配对，同时避免 llama.cpp 空 assistant 误触发续写。                       |
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

升级前阅读[依赖、配置和回滚检查](docs/upstream/v0.13.3-decisions.md#数据升级与回滚)。
Read the [upgrade and rollback checks](docs/upstream/v0.13.3-decisions.md#数据升级与回滚) before upgrading.

> [!NOTE]
> 下方的 `pip install open-webui` 和 `ghcr.io/open-webui/open-webui` 示例安装上游发行物。使用本分支功能需从本仓库源码构建；参见[开发指南](docs/DEVELOPMENT.md)。
>
> The `pip install open-webui` and `ghcr.io/open-webui/open-webui` examples below install upstream releases. Build this repository's source to use the fork's capabilities; see the [development guide](docs/DEVELOPMENT.md).

### Installation

#### Python

```bash
# With uv installed (macOS/Linux)
DATA_DIR=~/.open-webui uvx --python 3.12 open-webui@latest serve

# Or install and run with pip (Python 3.12)
pip install open-webui
open-webui serve
```

Then open [http://localhost:8080](http://localhost:8080).

#### Docker

```bash
docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
```

Then open [http://localhost:3000](http://localhost:3000).

| Image tag | Includes                                                                                 |
| --------- | ---------------------------------------------------------------------------------------- |
| `:main`   | Standard image with local AI dependencies                                                |
| `:slim`   | Lightweight image using external services for embeddings, document extraction, and voice |
| `:cuda`   | Open WebUI with CUDA acceleration                                                        |
| `:ollama` | Open WebUI bundled with Ollama                                                           |
| `:dev`    | Latest development build                                                                 |

Add these options to `docker run` before the image name as needed:

| Option                                               | Purpose                                                                                  |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `-e OLLAMA_BASE_URL=http://your-ollama-server:11434` | Connect to Ollama on another server                                                      |
| `-e OPENAI_API_KEY=your_secret_key`                  | Set your OpenAI API key                                                                  |
| `-e HF_HUB_OFFLINE=1`                                | Disable Hugging Face model downloads for offline use                                     |
| `--gpus all`                                         | Enable Nvidia GPU access for `:cuda` or `:ollama`; requires the Nvidia Container Toolkit |

See the [Docker installation guide](https://docs.openwebui.com/getting-started/quick-start/) for setup instructions and configuration options.

#### Desktop App

[Download the native desktop app](https://github.com/open-webui/desktop) for macOS, Windows, and Linux.

[Read the docs →](https://docs.openwebui.com/getting-started/)

### Ecosystem

- [Open Terminal](https://github.com/open-webui/open-terminal) / [Terminals (Enterprise)](https://github.com/open-webui/terminals): The action layer that turns Open WebUI into a full agent harness, with enterprise orchestration for per-user workspaces.
- [Knowledge Base Sync (oikb)](https://github.com/open-webui/oikb): Mirror folders, repos, buckets, and wikis into Open WebUI Knowledge Bases with incremental sync.
- [Open WebUI Computer](https://github.com/open-webui/computer): Your computer in any browser: files, editor, terminal, git, and optional AI agents, running on your real machine.

See the [ecosystem documentation](https://docs.openwebui.com/ecosystem/) for details.

### Troubleshooting and Updates

See the [troubleshooting guide](https://docs.openwebui.com/troubleshooting/) for connection issues and the [updating guide](https://docs.openwebui.com/getting-started/updating) to update your installation.

### Documentation

See the [docs](https://docs.openwebui.com/) for configuration and usage, and the [roadmap](https://docs.openwebui.com/roadmap/) for planned work.

For Docker Compose, Kubernetes, and other setups, see the [installation docs](https://docs.openwebui.com/getting-started/).

### Support

[Open an issue](https://github.com/open-webui/open-webui/issues) or [start a discussion](https://github.com/open-webui/open-webui/discussions).

Find us on [Reddit](https://www.reddit.com/r/OpenWebUI/), [X](https://x.com/OpenWebUI), and [Discord](https://discord.gg/5rJgQTnV4s).

### Security

Report vulnerabilities privately through [GitHub security advisories](https://github.com/open-webui/open-webui/security). Security reports are accepted only through GitHub.

### Star History

<a href="https://star-history.com/#open-webui/open-webui&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
  </picture>
</a>

#### License

Includes code under the Open WebUI License, which requires preserving the Open WebUI branding, and prior contributions under their original licenses. See [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY) for terms and history.

[Enterprise](https://docs.openwebui.com/enterprise) · [Careers](https://careers.openwebui.com/) · [Sponsor](https://github.com/sponsors/open-webui)

Created by [Timothy Jaeryang Baek](https://github.com/tjbck).
