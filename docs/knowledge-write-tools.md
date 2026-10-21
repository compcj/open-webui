# 知识库写入工具 / Knowledge write tools

聊天可将内容保存为已有知识库中的文本文件、直接导入网页或终端文件，也可修改已有文件的检索文本。
写入是独立授权，默认关闭。工具不会新建知识库、删除文档、管理目录或修改 PDF、Word 等原始上传文件。

## 开启与目标选择

1. 选择支持内置工具调用的模型。模型设置中的“内置工具”需允许知识库读取和知识库写入。
2. 在首页或聊天输入区的工具菜单中开启“知识库”读取，再开启“知识库写入”。
3. 指定已有知识库及保存内容。可先让模型列出知识库、查看文件，再确认目标。
4. 若当前工具审批策略要求确认，核对目标和内容后批准。拒绝审批不会写入。

写入选择按用户及模型记忆，已有聊天草稿会恢复自己的选择。旧配置没有写入选择时保持关闭。
多模型聊天要求所有所选模型都允许此能力，多模型下的临时选择不改写单模型偏好。
关闭读取会暂停写入并保留偏好；重新开启读取后恢复原先选择。账户切换不会沿用上一账户的选择。

目标范围为模型、文件夹和当前对话挂载知识库的并集。只要挂载了知识库，写入就限制在这些库中，
无论内容通过检索工具还是 `file_context` 注入。没有挂载库时，可以选择当前用户有写权限的库。
每次执行都重新检查权限；审批等待期间被撤销权限的请求也会被拒绝。

列表中的 `write_access` 表示当前用户对该知识库的写权限，不保证其中每个文件都可编辑。
文件编辑还要求文件属于目标库，并且用户拥有文件级写权限。读取权限、挂载和搜索结果本身均不授权写入。
外部知识库不支持这些写入工具。

## 新增文档

可对模型说：“把本轮讨论整理成 Markdown，保存到项目知识库，文件名为 `meeting-notes.md`。”

```json
{
	"knowledge_id": "example-knowledge-id",
	"filename": "meeting-notes",
	"content": "# Meeting notes\n\nWe will validate the prototype on Friday.\n"
}
```

对应 `write_knowledge_file`。无扩展名时补 `.md`，支持 `.md` 和 `.txt`，使用 UTF-8，
保存到知识库根目录。同名文件不会被覆盖；换名创建，或读取已有文件后使用编辑工具。
禁止路径、非法文件名、空白内容和超出文件大小限制的内容。

工具确认处理状态及知识库关联后才报告成功。失败清理仅涉及本次新建且未关联的文件；
已经入库或被其他知识库关联的文件不会因清理被删除。

## 从 URL 或终端直接导入

当内容已经位于网页或文件中，尤其是内容超过模型上下文时，使用直接导入工具。
模型只提交来源和目标，后端获取、解析并写入完整来源内容；工具返回文件 ID、名称、大小及保存、
关联和索引状态，不把正文或 Base64 交给模型转述。一次调用处理一个来源，等待最终结果。
导入后可再使用知识库搜索和分页读取工具查看需要的部分。

### URL 导入

可对模型说：“将这个链接的内容直接导入项目知识库，保留完整内容，不要先总结。”
对应 `import_knowledge_url`：

```json
{
	"knowledge_id": "example-knowledge-id",
	"url": "https://example.com/guide",
	"filename": "guide.txt"
}
```

普通网页使用部署配置的网页加载器提取正文，保存为 UTF-8 文本；PDF、Word 等文件链接使用现有
文件解析流程，并保留原始文件字节。实际支持的格式取决于部署的解析器和允许上传的扩展名。
导入不会应用 `fetch_url` 的模型展示截断，但网页加载器能取得的内容仍受网站及加载器能力限制。
基础网页加载器直接解析已检查大小的下载结果，避免再次下载。浏览器或外部网页加载器仍遵循
各自的运行环境及服务限制；文件大小上限约束直接下载和最终保存的正文，不代表这些加载器的
总内存或网络开销上限。
只导入指定页面或文件，不递归抓取链接。

`filename` 可省略：网页按现有字符替换及长度规则从 URL 生成 `.txt` 名称，文件链接使用来源文件名。
自动生成的网页名称和来源 URL 记录会去掉查询参数及片段，获取内容时仍使用完整 URL。
显式名称应保留正确的文件格式，不能用改扩展名绕过格式限制。只接受 HTTP(S) URL，遵循现有域名、
内网访问、重定向和网络安全策略；不会把 WebUI 的登录凭据发送给目标网站。
导入已知 URL 不要求打开“网页搜索”，但必须开启知识库读取和写入。

### 终端文件导入

先在当前对话选择管理员配置的服务器终端，让模型下载或生成文件，然后调用
`import_knowledge_terminal_file`：

```json
{
	"knowledge_id": "example-knowledge-id",
	"path": "/workspace/reports/manual.pdf",
	"filename": "manual.pdf"
}
```

`path` 是该终端内规范化的 POSIX 绝对路径；省略 `filename` 时使用路径中的文件名。
后端使用当前用户及对话的终端身份读取文件，不在 WebUI 主机上打开这个路径。
无需把知识库 API 凭据交给终端脚本。模型还必须允许终端能力；每次导入都会复核终端访问权限，
且受终端配置的聊天／自动化上下文隔离约束。

个人浏览器直连终端、目录和目录批量导入暂不支持。未选择可用的服务器终端时，选择终端后重试；
不能通过工具参数指定其他用户、聊天或终端连接。

### 大小、同名与失败结果

两种导入都保存到已有知识库根目录。同名文件或目录会报冲突，不覆盖也不自动改名。
导入沿用现有文件大小上限、允许格式、解析器和索引配置；超过模型上下文不妨碍导入，
但服务器内存、存储、下载和解析服务仍有资源及超时限制。

只有实际保存、处理及关联均已核实才报告成功。若索引按配置跳过，结果会明确标记
`indexing_skipped: true` 和 `indexed: false`。失败时仅清理本次新建且确认未关联的孤立文件；
若文件仍被保留，结果会提供文件 ID 及实际状态；无法确认的保存或关联状态返回 `null`。
清理未能核实时会返回 `cleanup_verified: false`，并用 `file_retained` 表示文件记录仍在、
已删除或无法确认（`true`、`false` 或 `null`）；保留的文件 ID 供排查，不保证文件仍可读取。
取消或结果不确定时，应先核查文件，
不要自动重复导入或换名创建。工具审批恢复和模型请求重试不会重复执行已完成的导入。

## 全文替换、局部修改和追加

使用 `replace_knowledge_file_content`，必须在 `content` 和 `operations` 中二选一。
先读取当前完整检索文本，确认文件 ID 和需要修改的位置。

全文替换：

```json
{
	"knowledge_id": "example-knowledge-id",
	"file_id": "example-file-id",
	"content": "# Updated notes\n\nThe prototype review is on Monday.\n"
}
```

局部修改使用与笔记相同的 `replace_range` 格式。若原文为 `"A cat.\n"`，
将 `cat` 改为 `dog`：

```json
{
	"knowledge_id": "example-knowledge-id",
	"file_id": "example-file-id",
	"operations": [
		{ "action": "replace_range", "start": 2, "end": 5, "content": "dog", "expected": "cat" }
	]
}
```

偏移从 0 开始，`end` 不包含在范围内，单位为原文 Unicode 字符而非 UTF-8 字节。
同一批操作全部使用修改前的偏移，不随前面的替换移动。非空范围必须提供匹配的 `expected`；
越界、重叠、原文不匹配或最终结果为空时拒绝整次修改。

对同样的原文 `"A cat.\n"`，在末尾追加一行：

```json
{
	"knowledge_id": "example-knowledge-id",
	"file_id": "example-file-id",
	"operations": [{ "action": "replace_range", "start": 7, "end": 7, "content": "A dog.\n" }]
}
```

追加使用原文末尾的零长度范围。不要把分页读取中的局部位置当成全文偏移；发生冲突时重新读取，
再根据当前全文生成操作。

## 原文件与共享文件

编辑对象是用于检索的提取文本。下载原始 PDF、Word 或文本附件时，原始上传字节保持不变。
因此编辑后的检索内容可能与原文件不同；重新上传或重新提取原文件前，应先保存需要保留的修订。

同一文件关联到多个知识库时，内容更新沿用现有流程，重建各关联知识库中的索引。
这会影响其他知识库对该文件的检索结果，不能把只读文件挂到自己可写的知识库来绕过文件权限。

## 失败与恢复

参数、权限或目标范围错误会拒绝写入。内容已经保存但索引失败时，工具返回部分完成信息和文件 ID；
此时检索结果可能仍是旧内容或暂时不可用。

先按返回的文件 ID 查看当前提取文本，确认修改是否已保存。修复原有嵌入服务、连接或索引配置后，
使用已核实的完整文本执行一次全文保存以重建索引，或从知识库文件编辑界面重新保存。
不要直接重复追加操作，也不要在创建结果不确定时换名再次创建，否则可能产生重复内容。
工具审批恢复和模型请求重试会沿用已执行的工具结果。

若部署启用了现有的“绕过嵌入和检索”配置，编辑仍会保存文本，返回 `indexing_skipped: true`
和 `indexed: false`，表示按配置跳过索引。需要检索时，先恢复嵌入和检索配置，再保存已核实的完整文本。

部署仍使用现有上传、内容更新及检索配置，无新增环境变量、依赖或数据库迁移。
开发与验收见[验证指南](DEVELOPMENT.md#knowledge-write-verification)，设计理由见
[写入决策](upstream/v0.12.2-knowledge-write-followup.md)与
[直接导入决策](upstream/v0.12.2-knowledge-direct-import-followup.md)。

## English quick reference

Enable Knowledge Base reading, then opt in to Knowledge Write in the chat tools menu. Writing defaults
off, requires model permission and current collection write access, and remains scoped to attached
model/folder/chat collections when any exist. Without attachments, choose a collection you can write.
File edits additionally require membership and file write access. External collections are unsupported.

`import_knowledge_url(knowledge_id, url, filename=None)` imports a single HTTP(S) page or document.
`import_knowledge_terminal_file(knowledge_id, path, filename=None)` imports a file from the current
administrator-configured terminal, using the actor's existing terminal and chat context. Personal
browser-direct terminals and directory imports are unsupported. Terminal import also requires model
terminal capability. URL import does not require the web-search toggle.

Source contents stay outside model context. Both tools wait for completion and return compact file
identity and persistence/indexing status. Web pages store extracted UTF-8 text; supported documents
retain their original bytes. Existing file limits, loaders, permissions and root-name conflict checks
still apply. These tools do not crawl sites, overwrite files, or require WebUI credentials in terminal
scripts. Check retained file IDs after partial completion or interruption before attempting a new import.

`write_knowledge_file` creates UTF-8 `.md`/`.txt` files at the collection root without overwriting names.
`replace_knowledge_file_content` accepts either full `content` or `operations`, using original Unicode
character offsets and matching `expected` text for nonempty ranges. A zero-length range at the end appends.
These operations change retrieval text, preserve original uploads and reindex all collections sharing
the file. If text is saved but indexing fails, inspect the returned file ID and save the verified full
text again after fixing indexing; do not blindly repeat an append.
When retrieval bypass is configured, edits save text and report `indexing_skipped: true` with
`indexed: false`; restore retrieval and save verified full text to rebuild the index.
