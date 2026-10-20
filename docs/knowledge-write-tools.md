# 知识库写入工具 / Knowledge write tools

聊天可将内容保存为已有知识库中的文本文件，也可修改已有文件的检索文本。写入是独立授权，
默认关闭。工具不会新建知识库、删除文档、管理目录或修改 PDF、Word 等原始上传文件。

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
外部知识库不支持这两个工具。

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
[后续决策](upstream/v0.12.2-knowledge-write-followup.md)。

## English quick reference

Enable Knowledge Base reading, then opt in to Knowledge Write in the chat tools menu. Writing defaults
off, requires model permission and current collection write access, and remains scoped to attached
model/folder/chat collections when any exist. Without attachments, choose a collection you can write.
File edits additionally require membership and file write access. External collections are unsupported.

`write_knowledge_file` creates UTF-8 `.md`/`.txt` files at the collection root without overwriting names.
`replace_knowledge_file_content` accepts either full `content` or `operations`, using original Unicode
character offsets and matching `expected` text for nonempty ranges. A zero-length range at the end appends.
These operations change retrieval text, preserve original uploads and reindex all collections sharing
the file. If text is saved but indexing fails, inspect the returned file ID and save the verified full
text again after fixing indexing; do not blindly repeat an append.
When retrieval bypass is configured, edits save text and report `indexing_skipped: true` with
`indexed: false`; restore retrieval and save verified full text to rebuild the index.
