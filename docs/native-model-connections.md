# 原生模型连接 / Native model connections

## 配置 / Setup

管理员在 **设置 → 连接 → 编辑连接** 显式选择协议。现有空值仍为 Chat Completions，
也支持 Responses；Gemini 和 Anthropic 使用各自原生消息协议。协议不按 URL 或模型名猜测。
配置仍保存在现有 `OPENAI_API_*` 设置中，无数据库迁移。

Admins explicitly choose the protocol under **Settings → Connections → Edit Connection**. An empty
protocol remains Chat Completions (`chat_completions` is also accepted); Responses retains its existing
behavior. Gemini and Anthropic use native message transports. Existing `OPENAI_API_*` settings store
these connections without a database migration.

| 协议 / Protocol  | 配置值 / Value                           | 官方基础 URL / Official base URL                   | 消息端点 / Message endpoint                                           | 常用认证 / Typical auth    |
| ---------------- | ---------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------- | -------------------------- |
| Chat Completions | empty/default; legacy `chat_completions` | `https://api.openai.com/v1`                        | `/chat/completions`                                                   | Bearer                     |
| Responses        | `responses`                              | `https://api.openai.com/v1`                        | `/responses`                                                          | Bearer                     |
| Gemini           | `gemini`                                 | `https://generativelanguage.googleapis.com/v1beta` | `/models/{model}:generateContent` or `:streamGenerateContent?alt=sse` | API key / `x-goog-api-key` |
| Anthropic        | `anthropic`                              | `https://api.anthropic.com/v1`                     | `/messages` with `stream` true/false                                  | API key / `x-api-key`      |

官方协议参考 / Official protocol references:
[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create),
[OpenAI Responses](https://platform.openai.com/docs/api-reference/responses/create),
[Gemini generateContent](https://ai.google.dev/api/generate-content),
[Anthropic Messages](https://docs.anthropic.com/en/api/messages).
兼容网关须自行实现所选协议；同一模型名不保证四种协议互通。
A compatible gateway must implement the chosen protocol; identical model names do not establish compatibility.

仅在 URL 为空时填入默认值。兼容网关可填写如 `https://gateway.example.test/v1beta`（Gemini）
或 `https://gateway.example.test/v1`（Anthropic）。这些示例地址不提供服务。
选择 `api_key` 认证并使用自己的密钥；文档示例密钥 `synthetic-example-key` 不可用于真实请求。
Gemini 使用 `x-goog-api-key`，Anthropic 使用 `x-api-key` 并默认发送 `anthropic-version: 2023-06-01`。

Default URLs fill blank fields only. Compatible gateways can use the synthetic bases above. Select
`api_key` authentication and supply your own key. Gemini sends `x-goog-api-key`; Anthropic sends
`x-api-key` and defaults `anthropic-version` to `2023-06-01`. Explicit header controls retain priority.
Existing bearer, none, session and OAuth authentication, custom headers, cookie forwarding and
UA/session identity controls remain available. Model discovery follows native pagination; manual
model IDs remain usable when discovery is unavailable or incomplete.

### 分协议操作 / Per-protocol steps

先在管理员连接页新增或编辑连接，再按以下步骤配置并保存。URL 填基础地址，不附加表中的消息端点。
Add/edit an admin connection, follow the matching steps and save. Enter a base URL without the message endpoint.

1. **Chat Completions**：选 Chat Completions，官方填 `https://api.openai.com/v1`，认证选 Bearer 并填自己的密钥，再验证连接和模型。兼容网关示例用 `https://chat.example.test/v1`，按网关要求选择认证。 / Select Chat Completions, enter the official base and your Bearer key, then verify the connection/models. For a gateway, use its base (synthetic example above) and required authentication.
2. **Responses**：选 Responses，官方同样填 `https://api.openai.com/v1` 和 Bearer 密钥，再验证并选择支持 Responses 的模型。网关可用 `https://responses.example.test/v1`，须支持 `/responses`，仅支持 Chat Completions 的网关不能直接使用。 / Select Responses, use the official base/Bearer key and a Responses-capable model. A gateway must implement `/responses`; the synthetic example does not provide a service.
3. **Gemini**：选 Gemini，使用表中官方基础 URL，认证选 API key，填自己的密钥并验证模型列表。网关可用 `https://gemini.example.test/v1beta`，须支持原生 generateContent 和所需流式接口。 / Select Gemini, use the official base/API key and verify discovery. A gateway must implement native generateContent and the streaming endpoint you need.
4. **Anthropic**：选 Anthropic，使用表中官方基础 URL，认证选 API key，填自己的密钥并验证模型列表。网关可用 `https://anthropic.example.test/v1`，须支持原生 `/messages`；按需配置版本 Header。 / Select Anthropic, use the official base/API key and verify discovery. A gateway must implement native `/messages`; configure its version header if required.

**Model IDs / 模型 ID** 可以手动填写供应商实际模型 ID；不要把显示名当作供应商 ID。
**Prefix ID / 前缀 ID** 用于区分不同连接里的同名模型，发给供应商时移除该连接前缀。
例如手动 ID `synthetic-model`、Prefix ID `lab` 可在 WebUI 中区分为 `lab.synthetic-model`；
这只是配置示例，不代表真实可用模型。连接显示名称仅用于识别连接，不决定协议或供应商模型名。

Enter actual provider IDs in **Model IDs** when discovery is unavailable. **Prefix ID** distinguishes
models across connections and is removed before provider dispatch; a connection's display label does
not select its protocol or change the provider ID. The IDs above are synthetic examples.

### 认证与 Header 优先级 / Authentication and header priority

原生连接省略 `auth_type` 时默认为 API key；显式选择 Bearer、none、session 或 OAuth 仍按所选方式处理，
不会因为 URL 是官方地址而改回 API key。新建空 URL 的原生连接会填默认 URL 和 API key；编辑已有连接时保留原值。
原生自定义 Headers 按大小写不敏感的名称覆盖生成的认证与版本 Header，
如 `X-API-Key` 可覆盖 `x-api-key`，`Anthropic-Version` 可覆盖默认版本。
显式 User-Agent 和 Session ID 控件再覆盖自定义 Headers 中的同名项，Session ID Header 不可占用认证 Header。

Omitted native `auth_type` defaults to API key. Explicit bearer/none/session/OAuth choices remain
explicit. A newly created native connection with a blank URL fills the protocol defaults; editing
an existing connection retains its values. Native custom Headers override generated auth/version
headers case-insensitively. Explicit User-Agent and Session ID controls then override matching custom
headers; Session ID cannot replace authentication headers. Avoid duplicate differently cased names
in the custom header object itself.

## 能力与限制 / Capabilities and scope

支持文本、图片、工具循环、审批、流式和非流式响应、思考签名与用量。思考配置使用现有模型控件
及 `custom_params`：Gemini 为 `generationConfig.thinkingConfig`，Anthropic 为 `thinking` / `output_config`。
不根据模型名猜测版本或参数。Anthropic 未指定输出上限时使用 `max_tokens=4096`，显式设置优先。
上下文窗口仍由管理员配置，不能由一次用量响应推断。

Supported flows include text, images, tool loops, approvals, streams/nonstreams, signed thinking and
usage. Configure thinking through existing model controls and native custom parameters listed above;
model names do not select thinking parameters. Anthropic defaults absent `max_tokens` to 4096 before
budgeting; explicit limits win. Context windows remain administrator configured.

### 思考参数示例 / Thinking parameter examples

在现有模型高级配置的 `custom_params` 中填与当前模型兼容的参数对象。以下是相互独立的示例，
不应把两个供应商的参数合并；支持的思考模式、预算和输出限制以实际模型要求为准。
Use the existing model advanced `custom_params` with parameters supported by that model. These
separate examples are not universal defaults and must not be combined across providers.

Gemini:

```json
{
	"generationConfig": {
		"thinkingConfig": { "thinkingBudget": 1024, "includeThoughts": true }
	}
}
```

Anthropic:

```json
{
	"max_tokens": 8192,
	"thinking": { "type": "enabled", "budget_tokens": 1024 }
}
```

Anthropic 支持相应模式的模型也可使用 `thinking: {"type":"adaptive"}` 与 `output_config`；
不要仅因模型名猜测支持。缺少输出上限时默认 4096；`max_tokens` 或已有输出控件的显式值优先，
并在最终上下文预算前生效。思考预算须满足供应商的输出上限约束，不能只提高输入窗口来解决。

For models supporting them, Anthropic also accepts adaptive `thinking` and `output_config` through
custom parameters. Absent output limits default to 4096; explicit `max_tokens` or an existing output
control wins before final budgeting. Thinking budgets must satisfy provider output constraints.

原生协议仅适用于管理员服务端连接，不新增个人浏览器直连、Vertex、Bedrock、原生搜索、缓存管理、
音频、嵌入或图片生成端点。原生协议不能与 Azure 模式组合；不支持的端点会明确报错。

Native protocols apply to admin server connections. This feature adds no personal browser-direct,
Vertex, Bedrock, native search, cache management, audio, embedding or image-generation endpoints.
Native+Azure configurations are invalid; unsupported endpoints return explicit errors.

## 历史、工具与预算 / History, tools and budgets

原始思考块、parts 和签名保存在可选 `reasoning_details` 中，绑定协议、连接、模型和原始内容。
只有校验匹配的内容才能回放；需要签名但签名缺失或内容被修改时会明确报错，不会重新执行已完成工具。
显示用思考文本不能替代原始签名。更换连接或模型后不能假定旧签名可复用。

Optional `reasoning_details` retains exact native blocks/parts and signatures bound to protocol,
connection, model and original content. Replay requires a validated match. Required lost/edited
signatures produce explicit errors without rerunning completed tools. Display reasoning text cannot
replace signed content; switching connections or models does not make old signatures reusable.

工具引用前缀、审批和 off/legacy/V2 语义保持一致；最终预算包含原生回放、媒体、工具和协议封装，
在采用工具结果选择前校验。见[上下文压缩](context-compaction.md)。
Gemini 原生默认使用 `parametersJsonSchema`；手动受限 Schema 转换是独立选择，
见[工具 Schema 兼容指南](gemini-tool-compatibility.md)。

Citation prefixes, approvals and off/legacy/V2 behavior retain their contracts. Final budgets include
native replay, media, tools and protocol framing before tool-result selections are adopted. Native
Gemini defaults to `parametersJsonSchema`; the manual restricted-schema compatibility profile is a
separate explicit choice. See the linked compaction and schema guides.

## 排错与验证 / Troubleshooting and verification

- 认证错误：检查所选认证类型、网关要求和显式 Header 覆盖。 / Authentication errors: check auth type, gateway requirements and explicit header overrides.
- 模型列表不完整：检查网关分页支持，或手动配置模型 ID。 / Incomplete discovery: check gateway pagination or configure model IDs manually.
- 参数错误：检查当前协议与网关支持，不要仅修改模型名。 / Parameter errors: check the selected protocol and gateway support.
- 签名错误：保留原始历史并检查连接、模型与内容是否改变；不要重跑工具以规避错误。 / Signature errors: retain raw history and check connection, model and content changes; do not rerun tools to bypass the error.
- 预算错误：检查管理员窗口和输出上限，原生封装也占用预算。 / Budget errors: check the configured window and output cap, including native framing cost.

开发命令与测试映射统一放在[开发指南](DEVELOPMENT.md#native-model-connection-verification)。
模拟测试不能证明真实供应商或网关可用，真实验收需单独验证所需模型、认证、工具与签名续接。

Commands and test coverage live in the [development guide](DEVELOPMENT.md#native-model-connection-verification).
Real-provider checks remain pending for this change; no credentials or live-call success are recorded here.
Mocked tests do not establish live provider/gateway acceptance. Validate the intended model,
authentication, tools and signed continuations separately against the actual service.
