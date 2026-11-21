# 独立来源终端预览

Proxy 网页和端口预览通过与主站不同的主机名访问同一 WebUI 后端。默认关闭，管理员在
**设置 → Integrations** 配置 `TERMINAL_PREVIEW_BASE_URL`，例如 `https://preview.example.com`；
存储键为 `terminal.preview.base_url`。仅改变端口不满足隔离要求。已保存设置优先于环境默认值。

## 部署条件

- 主站如 `https://chat.example.com`，预览如 `https://preview.example.com`，都代理到同一组后端。
  生产使用 HTTPS；明确的本机开发允许 `http://localhost`、`*.localhost` 或回环 IP。
- 正确传递 Host 和可信的转发协议，配置后端仅信任部署的反向代理。预览 Host 仅提供 `/p/` 下的
  HTTP/WebSocket；其他路径、登录、主站 API 和普通 Socket.IO 均不能访问。
- 预览入口不可使用会转发主站认证的额外代理。禁用访问日志中的完整请求路径和 Referer，
  包括负载均衡器、CDN、Open Terminal 及错误跟踪；短期 URL 本身是访问能力。
- 多 worker/多实例使用共享 Redis，且各 worker 使用相同配置。Redis 不可用时返回可重试错误；
  不降级到进程内状态。无 Redis 只支持单实例单 worker。
- Open Terminal 必须支持当前 Proxy runtime 和浏览器 WebSocket 协议。未知 runtime 返回明确错误，
  不能放宽沙箱或回退同源代理。Chrome 模式单独使用其控制通道。

使用过的预览主机名会保留在服务端的独立保护记录中。禁用、改域名、重置公开配置或关闭持久化
配置，都不会让旧预览域名重新提供主站页面。不要把曾用预览域名改作 WebUI 主站。

可选 [Compose 示例](examples/terminal-preview/compose.yaml) 和 [Caddy 示例](examples/terminal-preview/Caddyfile)
只说明新部署接线，不修改已有部署。将域名改为自己控制的域名，配置 DNS 和 TLS。主站 Cookie 应使用
host-only 范围，不能设为同时覆盖主站与预览的父域；预览网关仍会丢弃 Cookie、Authorization 和 Referer。

## 共享和托管实例

在共享聊天中，打开预览的成员须对选中的终端有独立的用户或群组使用授权。只有获委派显式写权限的管理者才能管理模板和访问其他用户的托管实例。预览票据同时绑定实际目标和操作者，更改聊天、连接配置、实例或终端上下文时重新授权。Tether 使用自己的设备绑定，管理员关闭 Tether 后停止新授权。普通附件的 WebUI 上传或启用 filesystem 上传均独立于预览票据。

## 票据和请求

登录用户向主站发送 `POST /api/v1/terminals/{server_id}/preview-tickets`：

```json
{
	"kind": "port",
	"port": 5173,
	"path": "/",
	"chat_id": "saved-chat-id"
}
```

浏览器目标使用 `kind: "browser", session_id: "authorized-session-id"`。端口目标也可携带同一
聊天/终端下已授权的 `session_id`；正常浏览器标签采用该方式，关闭标签时删除的就是该会话。
独立 API 客户端也应优先显式创建、复用和删除浏览器会话。临时聊天还需
`temporary_context: {id, epoch}`，且 `chat_id` 为 `temporary:<id>`。响应为 `{url, expires_at}`，
其中 `expires_at` 是 Unix 秒。续期提交相同身份/目标和 `previous_url`；不能借续期换用户、登录会话、
聊天或终端。端口入口路径保持不变，当前页面导航不被续期重置。

票据随机、不透明，默认有效 300 秒；活动面板每 60 秒重新授权。终端凭据及原始浏览器 capability
只保存在服务器，返回页面使用独立预览 URL。浏览器入口可跳转到服务器保存的当前目标；尚未导航的
浏览器显示空白提示。HTTP 资源、表单、跳转、SSE 和 WebSocket/HMR 沿用终端浏览器代理协议。

端口必须在当前用户可见的 Open Terminal `/ports` 列表中，创建、续期、每次请求及活动流均重查。
相同用户、登录会话、聊天、连接地址、配置版本和端口复用已验证的终端浏览器会话。首次创建响应
丢失时保留持久化占位，最长 25 小时内不盲目再建，避免终端会话泄漏；此时返回可重试错误。
仅提供端口而不提供 `session_id` 的客户端使用独立会话，仍受上游每 owner 16 个会话和 24 小时
空闲回收限制；跨许多聊天创建后可能暂时达到配额。WebUI 不持久保存登录凭据用于后台代删。

每次请求及活动流持续检查登录撤销、当前用户、终端授权、聊天或临时 scope 与配置。退出、撤权、关闭
scope、更换连接或禁用预览会令后续请求失败并关闭活动流。已经发送给目标站点的操作不会被撤权撤销。
票据过期/撤销后重新从主站授权，不自动重放有副作用的请求。

## 沙箱和存储边界

iframe 和响应 CSP 都保持严格沙箱，不使用 `allow-same-origin`，不允许顶层跳转。脚本无法访问
预览站点的 DOM Cookie、localStorage 或主站凭据。主站拒绝来自预览来源和不透明来源的凭证访问。
页面与父窗口的消息同时检查来源、发送窗口、票据及消息结构。资源不缓存、无 Referer；文本响应和
重定向会重写上游 capability，不能保证安全重写时明确失败。

按用户确认的边界，保留 Open Terminal Proxy 在终端内部维护的目标站点 Cookie jar；这与网页可见
的 DOM 存储不同，不能宣称端到端无 Cookie。端口 WebSocket 的初始目标受票据端口检查，但终端
内部的后续重定向沿用上游浏览器网络权限，允许离开初始端口，不宣称端到端端口网络隔离。
需要登录网站、存储或完整浏览器语义时使用 Chrome。不要把沙箱预览当作不可信代码的操作系统隔离。

v0.13.3 验证时的 Open Terminal 0.17.7 配合 websockets 17.4 对未指定子协议的 WebSocket 会发送空
`Sec-WebSocket-Protocol`，严格目标服务器可能返回 400。带 `vite-hmr` 子协议的链路已验证；这是
终端依赖的兼容边界，不通过放宽 WebUI 沙箱或同源代理解决。部署应核对终端与目标服务的协议兼容性。

## 故障和回滚

未配置入口时 UI 不提供 Proxy 预览；地址、TLS、终端或 Redis 不可用时明确报错，没有同源降级。
大量文本资源和 SSE 单事件限制 16 MiB；过大返回错误而不截断并展示不完整可执行内容。
禁用配置后入口立即停止授权；关闭并确认活动任务后再协调更换 worker。回滚恢复升级前数据库、
上传、配置和镜像，不能只把新迁移数据库交给旧版应用。

检查命令见[开发指南](DEVELOPMENT.md)。本版的实际运行结果及未覆盖的真实浏览器/终端场景见[v0.13.4 验收记录](upstream/v0.13.4-validation.md)。
