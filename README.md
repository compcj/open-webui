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

Open WebUI is **a home for AI**, a self-hosted AI platform that's **[extensible](https://docs.openwebui.com/features/extensibility/plugin/)**, **[feature-rich](https://docs.openwebui.com/features/)**, user-friendly, and built to run **[entirely offline](https://openwebui.com/sovereign-ai)**. With support for **Ollama** and **OpenAI-compatible APIs**, it gives you a powerful, provider-agnostic interface for both local and cloud-based models.

Passionate about open-source AI? [Join our team →](https://careers.openwebui.com/)

![Open WebUI Demo](./demo.png)

> [!TIP]  
> **Looking for an [Enterprise Plan](https://docs.openwebui.com/enterprise)?** – **[Speak with Our Sales Team Today!](https://docs.openwebui.com/enterprise)**

For more information, be sure to check out our [Open WebUI Documentation](https://docs.openwebui.com/).

For development in this checkout, see [CONTRIBUTING.md](CONTRIBUTING.md),
[development commands](docs/DEVELOPMENT.md), and the shared [agent guide](AGENTS.md).

## 与上游的差异 / Differences from upstream

本项目 [compcj/open-webui](https://github.com/compcj/open-webui) 是 [open-webui/open-webui](https://github.com/open-webui/open-webui) 的分支。以下以本仓库已合入的上游 **[v0.11.3](https://github.com/open-webui/open-webui/tree/v0.11.3)** 为对照基线（[合入提交 `0231d2c`](https://github.com/compcj/open-webui/commit/0231d2ca89dcf66b152d0feb536edc83d80410f8)），根据提交记录与代码核验至 **[`46bbe37`](https://github.com/compcj/open-webui/commit/46bbe371e2ceb2f3bfe1d8520b9f8e3dc0e4636a)**（**2026-09-21**），介绍本分支的功能增强与对该基线已有问题的修复。

This project, [compcj/open-webui](https://github.com/compcj/open-webui), is a fork of [open-webui/open-webui](https://github.com/open-webui/open-webui). The comparison below uses the integrated upstream **[v0.11.3](https://github.com/open-webui/open-webui/tree/v0.11.3)** as its baseline ([integration commit `0231d2c`](https://github.com/compcj/open-webui/commit/0231d2ca89dcf66b152d0feb536edc83d80410f8)). Entries were checked against this fork's history and code through **[`46bbe37`](https://github.com/compcj/open-webui/commit/46bbe371e2ceb2f3bfe1d8520b9f8e3dc0e4636a)** (**2026-09-21**) and cover its enhancements and fixes for issues present in that baseline.

### 功能增强 / Enhancements

| 功能 / Feature                                              | 本项目增强 / Enhancement in this fork                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | 参考 / References                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Search1API                                                  | 增加 Search1API 搜索引擎，可在管理界面配置 API Key，也支持 `SEARCH1API_API_KEY` 环境变量；统一搜索结果格式并支持域名过滤。<br>Adds Search1API as a web search provider, with an admin settings field and a `SEARCH1API_API_KEY` environment default, normalized results, and domain filtering.                                                                                                                                                                                                                                                                              | [搜索实现 / Search implementation](backend/open_webui/retrieval/web/search1api.py)、[管理设置 / Admin settings](src/lib/components/admin/Settings/WebSearch.svelte)                                                                                                                                                                                                                                                                                                                  |
| 推理强度配置 / Reasoning effort controls                    | 按模型配置可选档位和自定义值，在聊天中选择；按用户和模型记住上次选择，并显示当前生效的默认值。窄屏下扩展按钮与模型、推理选择区分别滚动，选择区可换行并使用整行宽度，避免遮挡按钮。<br>Configure standard or custom effort values per model, select them in chat, remember each user's last choice per model, and display the effective default. On narrow composers, extension buttons and model/reasoning selectors have separate scrolling areas; selectors can wrap onto a full-width row to avoid covering buttons.                                                     | [配置与选择 / Configuration and selection](https://github.com/compcj/open-webui/commit/488ec90c287b70324eaeb70446f2dd3606e47d77)、[选择记忆 / Remembered choices](https://github.com/compcj/open-webui/commit/3e724c16166c6b95a7e363b3547de42cbe61246b)、[默认值 / Defaults](https://github.com/compcj/open-webui/commit/a6f8eefca94fddef987da3c7e91f5ad2effa3a00)、[窄屏布局 / Narrow layout](https://github.com/compcj/open-webui/commit/9cf7f9442f3f5183dd4ca113d7c4e73850456220) |
| OpenClaw 技能兼容 / OpenClaw-compatible skills              | 在原生工作区技能基础上，增加 `SKILL.md` 与 ZIP 导入导出，保留元数据和附带文件；支持本地文件、直接链接、GitHub、skills.sh、ClawHub 等来源，并为未指定目标的多技能归档提供选择器。<br>Extends native workspace skills with `SKILL.md` and ZIP import/export, preserving metadata and bundled files. Supports local files, direct links, GitHub, skills.sh, and ClawHub, with a picker for multi-skill archives when no target is specified.                                                                                                                                   | [导入导出 / Import and export](src/lib/utils/skills.ts)、[来源解析 / Source resolution](backend/open_webui/routers/skills.py)                                                                                                                                                                                                                                                                                                                                                        |
| Open Terminal 技能运行支持 / Skill runtime in Open Terminal | 在已配置的 Open Terminal 中检查技能运行条件、同步附带文件，并在加载时替换 `{baseDir}`；依赖安装由用户显式触发，终端失败时回退到原有技能加载。<br>Checks skill requirements in the configured Open Terminal, syncs bundled files, and substitutes `{baseDir}` when loading a skill. Dependency installation requires an explicit user action; terminal failures fall back to the existing skill-loading behavior.                                                                                                                                                            | [运行时 / Runtime](backend/open_webui/utils/skills_runtime.py)、[加载集成 / Loading integration](backend/open_webui/utils/middleware.py)                                                                                                                                                                                                                                                                                                                                             |
| 聊天访问控制 / Chat access controls                         | 增加临时聊天与直接 API 聊天两个独立全局开关，按请求路径和会话上下文限制普通用户的访问；两者默认开启，管理员豁免。<br>Adds independent global switches for temporary chats and direct API chats, restricting non-admin access by request path and conversation context. Both default to enabled; administrators are exempt.                                                                                                                                                                                                                                                  | [详细说明 / Documentation](docs/chat-access-controls.md)                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 工具开关记忆 / Remembered tool toggles                      | Web 搜索与图像生成开关按用户和模型分别记忆，新会话自动恢复；支持单模型与 `@` 指定模型，多模型下的切换仅影响当前会话。聊天设置通过共享保存队列持久化，避免连续修改互相覆盖。<br>Remembers web search and image generation toggles per user and model and restores them in new chats. Applies to a single selected model or an `@` model override; multi-model changes stay local to the conversation. A shared save queue prevents successive chat setting updates from overwriting each other.                                                                              | [开关记忆 / Remembered toggles](https://github.com/compcj/open-webui/commit/cc10ba42ec48ea13fabcaf9ef7c9b59f4d016e51)、[偏好与保存 / Preferences and saving](src/lib/utils/tool-feature-preferences.ts)                                                                                                                                                                                                                                                                              |
| 知识、记忆与笔记开关 / Knowledge, memory, and notes toggles | 输入区新增知识、记忆与笔记独立开关，沿用上述按用户和模型记忆机制；后端按模型能力、全局开关与用户权限校验，记忆还受个人总开关控制。关闭知识发现或笔记工具时，在权限允许范围内保留已附加内容的检索和读取。<br>Adds independent knowledge, memory, and notes toggles using the same per-user/model preferences. The backend checks model capabilities, global switches, and permissions; memory also respects the personal master switch. Disabling knowledge discovery or notes tools preserves retrieval and reading of attached content within existing access permissions. | [开关与后端策略 / Toggles and backend policy](https://github.com/compcj/open-webui/commit/8a15f4ec6879ae80ac17d9bab03364d5a653fc33)、[工具策略 / Tool policy](backend/open_webui/utils/tools.py)                                                                                                                                                                                                                                                                                     |
| 终端文件交付 / Terminal file delivery                       | 管理员配置的终端通过签名链接交付生成文件，绑定生成用户、终端及原始聊天或自动化上下文；访问仍需该用户登录，链接不含登录令牌，分享聊天不会授予访问权。聊天内文件卡片支持预览、源码查看与下载。<br>Adds signed download and image links for admin-configured terminals, bound to the generating user, terminal, and original chat or automation context. Access requires that user's login; links contain no login tokens, and sharing a chat grants no access. Inline file cards offer previews, source viewing, and downloads.                                               | [详细说明 / Documentation](docs/TERMINAL_FILES.md)、[交付实现 / Delivery implementation](https://github.com/compcj/open-webui/commit/fa86245d995fd4f93fbbd9400e3ab283dea81645)                                                                                                                                                                                                                                                                                                       |
| 终端内容隔离 / Terminal content isolation                   | 文件下载强制以附件响应，图像按实际内容校验，SVG 使用 `<img>` 与 CSP 沙箱隔离；禁用终端同源端口网页预览（`proxy/{port}`），HTML 可查看源码或下载。<br>Forces file downloads to use attachment responses, validates image bytes, and isolates SVG previews with `<img>` and a CSP sandbox. Same-origin terminal port previews (`proxy/{port}`) are disabled; HTML remains available as source or a download.                                                                                                                                                                  | [隔离策略 / Isolation policy](docs/TERMINAL_FILES.md#content-isolation)、[内容校验 / Content validation](backend/open_webui/utils/terminal_files.py)                                                                                                                                                                                                                                                                                                                                 |

### 上游已有问题的修复 / Fixes for upstream issues

以下仅列出上游 v0.11.3 已有功能的问题修复。

The entries below address issues in functionality already present in upstream v0.11.3.

| 问题 / Issue                                      | 修复效果 / Fix                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | 参考 / Reference                                                                                                                                                                   |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Responses API 参数兼容 / Parameter compatibility  | 将已有的 `reasoning_effort` 参数映射为 `reasoning.effort`，保留其他推理字段，避免 Responses 接口因不支持顶层参数而返回 HTTP 400。<br>Maps the existing `reasoning_effort` parameter to `reasoning.effort`, preserving other reasoning fields and avoiding HTTP 400 errors caused by the unsupported top-level parameter.                                                                                                                                                                                                                          | [修复提交 / Commit](https://github.com/compcj/open-webui/commit/46d7acc44fab3d3797bca1393790c94cf857216b)                                                                          |
| Tika 文档解析 / Document parsing                  | 上传文档时显式发送 `Accept: application/json`，避免 Tika 返回文本而加载器按 JSON 解析导致失败。<br>Explicitly requests `Accept: application/json` for document uploads, preventing parsing failures when Tika would otherwise return text to a loader expecting JSON.                                                                                                                                                                                                                                                                             | [修复提交 / Commit](https://github.com/compcj/open-webui/commit/fcef90735654c70fa099e5f428c3ea0a0dfc7766)                                                                          |
| 原生技能引用 / Native skill mentions              | 修复从 `/` 菜单选择技能后被错误序列化为 `@` 引用的问题，正确生成 `$` 技能引用，使后端能够识别并加载技能。<br>Fixes skills selected through the `/` menu being serialized as `@` mentions; they now produce `$` skill references that the backend can recognize and load.                                                                                                                                                                                                                                                                          | [修复提交 / Commit](https://github.com/compcj/open-webui/commit/8f773f2bdfea0410f09c95dc2962fe8ac7c23ac4)                                                                          |
| 搜索后读取全文 / Reading full pages after search  | 完善 `search_web` 与 `fetch_url` 的工具说明，明确搜索结果只包含摘要，引导模型在需要全文时读取结果链接，减少将摘要误判为正文缺失的情况。<br>Clarifies the `search_web` and `fetch_url` tool descriptions: search results contain snippets, and models should fetch relevant links when full page content is needed, reducing mistaken reports of missing page content.                                                                                                                                                                             | [说明改进提交 / Commit](https://github.com/compcj/open-webui/commit/4abada9a2483199bbd8c46a5d0e334ec1b204c6c)                                                                      |
| 网页工具引用 / Web tool citations                 | 修复 `search_web` 与 `fetch_url` 结果的引用证据丢失：保留搜索摘要与页面摘录作为模型证据，按与界面一致的首次出现顺序分配来源编号，并在 Responses 有状态续接中保持该映射；`fetch_url` 失败或无内容时返回包含 URL 的错误。<br>Fixes lost citation evidence from `search_web` and `fetch_url`: search snippets and page excerpts are preserved as model evidence, source IDs follow the UI's first-seen numbering, the mapping survives stateful Responses continuations, and `fetch_url` failures or empty content return errors containing the URL. | [修复提交 / Commit](https://github.com/compcj/open-webui/commit/58db8c878d99f460d1f1076fc200c1b04938b76f)                                                                          |
| 终端 WebSocket 会话 / Terminal WebSocket sessions | 连接交互式终端时转发与 HTTP 创建会话一致的用户身份及聊天会话标识，修复创建成功后因归属信息缺失而报“Session not found”或断开的问题；适用于直连与编排器终端，并保留跨用户、跨聊天的隔离。<br>Forwards the same user and chat session identity used when creating the terminal over HTTP, fixing failed attachment or “Session not found” errors caused by missing ownership headers. Covers direct and orchestrator-backed terminals while preserving isolation between users and chats.                                                            | [修复提交 / Commit](https://github.com/compcj/open-webui/commit/46bbe371e2ceb2f3bfe1d8520b9f8e3dc0e4636a)、[回归测试 / Regression tests](backend/tests/test_terminal_websocket.py) |

> [!NOTE]
> 下方的 `pip install open-webui` 和 `ghcr.io/open-webui/open-webui` 镜像示例对应上游发行物。使用本项目的增强与修复，需要采用本仓库源码构建；参见[开发指南](docs/DEVELOPMENT.md)。
>
> The `pip install open-webui` and `ghcr.io/open-webui/open-webui` image examples below install upstream releases. To use this fork's enhancements and fixes, build from this repository's source; see the [development guide](docs/DEVELOPMENT.md).

## Key Features of Open WebUI ⭐

- 🚀 **Effortless Setup**: Install seamlessly via pip, uv, Docker, or Kubernetes (kubectl, kustomize, or helm), with `:ollama` and `:cuda` tagged images available for container deployments.

- 🤝 **Broad Model & API Integration**: Connect any OpenAI-compatible API alongside local Ollama models. Point the API URL at **LMStudio, GroqCloud, Mistral, OpenRouter, vLLM, and more** to mix and match providers freely.

- 🔐 **Granular RBAC & User Groups**: Administrators define detailed roles, groups, and permissions, giving each user exactly the access they need. Secure by default, with tailored experiences per group.

- 🧩 **Plugin Support**: Extend Open WebUI with **Filters**, **Actions**, **Pipes**, **Tools**, and **Skills**. Connect external services through **MCP**, **MCPO**, and **OpenAPI tool servers**. Build custom integrations, rate limits, approval flows, data connections, and more.

- 🤖 **Models & Agents**: Wrap any base model with custom instructions, tools, and knowledge to build specialized agents. Supports dynamic variables, per-user/group access control, and community preset imports via [Open WebUI Community](https://openwebui.com/).

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
