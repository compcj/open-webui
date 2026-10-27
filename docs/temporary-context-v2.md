# 临时聊天 V2：接口、状态与存储合同

状态：已实现并完成表列本地验收；核心提交 `ce33521968b5d2b44e2e925c8b5f1b658b0b9829`，控制记录退休修复 `a09b68687c54aaaef1116bb002eedee004d2ba68`。对应 [v0.12.4 D08](upstream/v0.12.4-decisions.md) 与[整合计划](superpowers/plans/2026-10-27-open-webui-v0.12.4-upstream-merge.md)。本文件描述实现合同；实际验收结果及覆盖边界见[验证报告](upstream/v0.12.4-validation.md)。

## 页面和身份

临时对话及其上下文身份只保存在当前页面内存。刷新、新建、离开和账号切换不恢复完整对话；同一页面的 socket 重连可以重绑定并补取状态。服务端创建用户绑定的 `id`、`epoch` 和原始来源 `revision`。socket 只负责投递，不能充当权限或上下文身份。内部兼容聊天键为 `temporary:<id>`。

生成请求附加 `temporary_context: {id, epoch, revision, branch_id}`，且 `chat_id` 必须与 scope 一致。`branch_id` 是已同步分支的用户节点；各模型回复有独立的 message/task/model 身份。前端先同步包含原始分支图和新回复占位节点的 history，再发送生成请求。后端从该快照构造历史，不把客户端 checkpoint、selection、工具来源声明视为执行证明。V2 临时请求缺少身份时返回明确的刷新提示。

`ContextScope` 是不可变的认证结果；`ContextStore` 显式绑定到 `Request.state`。保存聊天使用 SQL，临时聊天使用缓存端口。来源指纹、分支选择、选用版本和账目共用同一套规则；不得按请求修改全局 store。

## HTTP 合同

所有接口位于 `/api/v1/temporary-contexts`，使用现有登录认证及临时聊天权限。非拥有者（含管理员）不能访问。除创建外，每个请求都提交 epoch；读取接口通过查询参数提交。身份响应字段直接位于响应顶层：`id, epoch, revision, status, socket_generation`，另附 `lease_seconds, idle_deadline, maximum_deadline`。客户端不接受回退的 revision，并独立校验事件 seq 与 socket_generation；续租和完成确认可能沿用相同来源 revision。

| 接口                                 | 请求要点                                                                    | 返回／效果                                                                                                                                |
| ------------------------------------ | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /`                             | 空对象；可选已确认关闭的 `replaces: {id, epoch}`                            | 创建身份、期限和容量信息。普通创建不导入历史。重建须有旧 epoch 已隔离且执行已结束的证据。                                                 |
| `POST /{id}/sync`                    | `epoch, expected_revision, history: {messages, currentId}, params?, files?` | 全图验证并原子同步，返回新 revision。拒绝循环、悬空父链、重复 ID 和活动回复来源覆盖。历史导入不获得新工具执行证明。                       |
| `POST /{id}/lease`                   | `epoch, session_id, rebind?: boolean`                                       | 认证 socket 重绑定并续租；不改变来源 revision。                                                                                           |
| `GET /{id}/state`                    | 查询 `epoch, after_seq?`                                                    | 身份、生命周期、`tasks`、结果未知状态、`events`、`event_seq`、`events_dropped_through` 及当前原始 `history`。用于同页重连和遗漏事件协调。 |
| `POST /{id}/context-usage`           | `epoch, revision, model, branch_id, model_item?, session_id?`               | 与保存聊天相同的 C/B/T 使用量结构；只读，不生成候选或采用 selection。                                                                     |
| `POST /{id}/compact`                 | 同 usage                                                                    | 与保存聊天相同的 `{ok, compacted, usage}`；生成中或执行结果未明确时返回 409。只压缩历史，不采用工具候选。                                 |
| `GET /{id}/tool-results/{result_id}` | 查询 `epoch, branch_id, query?, cursor?, max_tokens?`                       | 现有精确原文分页结构；验证所属用户、分支祖先、原始来源与游标。                                                                            |
| `DELETE /{id}`                       | 查询 `epoch`                                                                | 隔离 epoch 并请求停止，返回 `closing` 或 `closed_confirmed`。超时保持可重试，不能据此推进待发请求。                                       |

错误结构为 `detail: {code, retryable, rebuild_allowed, limit?}`。409 是修订／并发冲突；410 是逻辑失效；503 是存储不可用。容量超限返回 413，用户同时存在的上下文数超限返回 429。410 的默认 `rebuild_allowed=false`；缓存缺失不代表旧任务已经结束。配置 Redis 后，任何故障都不能切换为内存状态。

## 原始图、模型和并发

服务端分别记录来源 revision、内部 CAS version、事件 seq、socket binding generation 和执行 fence。续租及状态查询不会增加来源 revision。保存新的原始输出会增加 revision；并行回复在同一初始修订上一次登记，后续用各自的 task/fence 与来源指纹验证，不能因兄弟回复的输出而覆盖彼此。

客户端同步可以编辑历史；修改来源会使对应 checkpoint、selection 和原文游标失效。已执行输出的完全相同回传保留服务端来源身份。新导入或恢复的工具输出只作为历史回放，不成为新结果预算候选。模型与分支隔离沿用现有 V2，Pipe 仍不支持；浏览器直连断线后不自动再次发送。

## 执行与停止

执行身份包含 scope、epoch、task、reply、model、round、ordinal、call ID 及实际目标／参数摘要。原始 provider function 另保存在完整调用中。解析实际工具、参数校验、权限与审批后，先写入 `started`，成功后才允许外部执行。工具输出完成规范化后，原文与 `resolved` 原子提交，再登记新的预算结果。相同轮次重复调用或重试不能再次执行；新轮次可以使用相同 provider call ID。

结果保存失败、外部结果未知或执行者丢失时保留 `uncertain`。仅允许后续结算，不允许自动重做工具。输出过大不得截断原文；预留的控制容量保证失败和停止状态仍能记录。

生命周期为 `open → closing → closed_confirmed → tombstone`。关闭和过期先增加 fence、禁止新任务／工具／发送，并保存停止意图。原执行者仍可用原 task token 保存最后的原文、结算结果和确认完成。Redis Pub/Sub 成功、租约过期、任务索引缺失均不是停止证明。结果未知的工作不回收为“已结束”，也不允许无证据重建。

临时审批保持断线取消，不提供刷新后的审批恢复。事件仅投递当前已认证绑定的 socket，不向同账号所有标签广播临时正文。

## 事件与恢复

临时事件保留现有 `chat_id, message_id, data` 外层，并增加 `temporary_context: {id, epoch, revision}, seq, model_id, task_id`。序号由存储提交生成；页面拒绝旧 scope/epoch、错误回复／模型、重复序号和迟到操作结果。检测到序号缺口时先读取 state，按水位协调快照及后续事件。有限事件日志可以合并早期增量，但必须保留规范原文快照及明确缺口水位。

显式离开先关闭并请求停止；真实关页使用尽力通知及服务端清理。页面内原文不会因保存或关闭失败而清空。无任务完成证明的缓存丢失只返回无法安全恢复，不能据 410 自动创建替代上下文。

## 缓存配置与部署

配置由服务器环境读取，前缀为 `TEMPORARY_CONTEXT_`，无需新增数据库迁移或普通聊天记录。

| 后缀                    | 默认值    | 含义                         |
| ----------------------- | --------- | ---------------------------- |
| `IDLE_SECONDS`          | 1800      | 页面闲置期限                 |
| `DISCONNECTED_SECONDS`  | 300       | 断线宽限                     |
| `MAXIMUM_SECONDS`       | 86400     | 最长寿命，续租不能延长       |
| `LEASE_SECONDS`         | 60        | 页面续租间隔                 |
| `CONTEXT_BYTES`         | 67108864  | 单上下文容量                 |
| `USER_BYTES`            | 134217728 | 单用户容量                   |
| `MAX_CONTEXTS_PER_USER` | 4         | 活动或尚未结算的上下文数     |
| `STORE_BYTES`           | 536870912 | 整个临时 store 容量          |
| `CONTROL_RESERVE_BYTES` | 65536     | 已计入容量的每上下文控制预留 |

原文、摘要、选用版本、账目、日志及控制记录按实际保留的 UTF-8 字节计费；媒体保留已授权引用。容量不足明确失败，不淘汰活动执行或已采用版本。执行／任务／事件数量和 tombstone 保留期也有受控上限，可由对应环境字段调整。

Redis 使用既有连接配置，所有临时键共用 `{temporary-contexts}` hash slot，以原子校验上下文、用户和总容量。这一有界域集中在一个 Cluster slot，不宣称跨 slot 分片扩容。活动或尚未结算的数据不使用破坏性的自动 TTL；先隔离、停止、确认，再回收。无 Redis 仅支持单实例、单 worker；已知多 worker 配置明确拒绝。

后台缓存服从 Redis 的 AOF/RDB、复制和备份策略，不能描述为“永不落盘”。升级采用排空旧 worker 后协调切换；旧 Redis reader 不识别新流格式，不保证混合版本滚动升级。回滚使用升级前数据库、上传、配置及镜像的整套备份。

## 转为普通聊天和导出

当前回复完成或收到执行者确认停止后，才投影完整原始历史。保留所有分支、调用配对、`provider_function`、冻结引用和签名推理；只在明确的上下文元数据位置去除 scope、checkpoint、selection、cursor、执行记录等派生身份，不递归删除工具业务 JSON 中的同名键。

保存与 JSON 导出使用同一原文投影。保存失败保留页面；保存成功后释放临时 scope。普通聊天加载或导入后，旧工具作为历史回放，不凭缓存猜测选用版本，不重执行工具。临时运行期间不能写普通 chat/chat_message 或四张 V2 SQL 表；工具经过独立授权的业务写入（例如知识库写入）不受此条禁止。

## 结果发布与保存重试

`resolved` 表示已保存规范工具原文，不表示该结果已发布到完整分支历史。
`publication_pending` 在同一个 CAS 写入原文及可信来源绑定后才清除；此前禁止新任务、转存和回收，
已接受的同批工具及并行模型仍可完成。执行者退出后，维护任务可从已存原文恢复完整输出和调用配对，
不重新执行工具。存储不可用时保留阻塞状态。

转存通过 `Idempotency-Key` 及临时身份请求头绑定一次保存，服务端先排他预留空闲 scope。
相同用户、相同键、相同原文重试返回已保存聊天；不同原文冲突。丢失 HTTP 回包不会重复创建聊天。
保存失败保留页面及缓存；关闭成功后仍已交付的终端文件链接保留签名中的原连接/context，继续验证
当前用户和连接权限，不把文件服务放到 WebUI 同源执行环境。

## 浏览器 RPC 一次性认领

`POST /{id}/rpc/{rpc_id}/claim` 接收 `epoch, socket_generation, task_id, reply_id, model_id, seq`，
返回 `{rpc_id, socket_generation, claimed: true, remaining_ms}`。服务端在 CAS 中核对当前 owner、
epoch、连接代次、任务、执行 fence 和期限；重复、失效或身份不符返回 409。前端在执行回调前
认领一次，同页状态快照已前进的序号不能充当“RPC 已执行”的证明。HTTP 回包丢失也不能重复执行。

state 的 `pending_rpcs` 只包含仍有效的标记：rpc/task/reply/model 身份、generation、seq、期限和
claimed。临时事件日志不保存 RPC 参数或连接凭据；RPC 正文只用于当前 socket 投递。完成或超时
清除标记，停止、任务结束和换代立即使其无效。默认最多同时保留 32 个有效 RPC 标记。
显式 `lease(rebind=true)` 即使 SID 未变也增加 socket_generation，普通每分钟续租不换代。
手动压缩只在当前 HTTP 操作期间接受对应分支/模型的摘要回调，结束后拒绝迟到结果。

控制容量还限制任务、执行和事件数量，避免生命周期记录无限增长；相应环境后缀为
`MAX_TASKS=32`、`MAX_EXECUTIONS=128`、`MAX_EVENTS=128`、`EVENT_BYTES=262144`、
`TOMBSTONE_SECONDS=300`、`CAS_RETRIES=32`。这些限制与字节配额同时生效，可按部署需要调整。
达到上限返回明确容量错误，不删除活动执行、未知结果或已采用版本来腾出空间。

## 长会话中的控制记录回收

`MAX_TASKS` 和 `MAX_EXECUTIONS` 限制尚未退休的控制记录，不是整个临时会话可回复的次数。
登记新任务的同一次 CAS 中，仅回收已由执行者确认 `done`、且其执行全部 `resolved` 并完成
原文发布的任务及执行控制记录。活动、结果未知或 `publication_pending` 的记录继续保留；
同一活动任务内的工具重试仍使用完整执行记录去重。

退休 task ID 的 SHA256 摘要保存在服务端 `data.retired_tasks`，计入上下文、用户及 store 的
字节配额。相同 task ID 不能重新登记；退休后的完成确认仅作只读幂等处理，旧 scope、执行、
发布和 RPC 操作仍被拒绝。客户端同步历史不能清除或签发这个账本。
回收不删除规范原文、来源授权、checkpoints、已采用 selections 或批次统计；后续原文读取、
预算回放、导出和转存继续使用这些数据。配额或 CAS 失败时，回收与新登记整体不生效。
