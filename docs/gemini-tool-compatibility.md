# Gemini 工具 Schema 兼容

本分支为第三方 OpenAI 兼容网关提供可手动启用的 Gemini 工具 Schema 转换。
`Unknown name "examples"`、`Unknown name "const"` 和 `any_of` 同级字段冲突属于网关或 Google
接口的请求校验错误，发生在模型生成之前。它们不能证明模型理解或使用 MCP 工具的能力较差。

## 启用方式

在模型编辑页的 **工具 Schema 兼容模式（Tool schema compatibility）** 选择 **Gemini**。
基础模型默认使用 **Default (no conversion)**；派生模型可继承基础模型，也可显式覆盖。
设置保存在 `meta.tool_schema_compatibility`，只接受 `default` 或 `gemini`。
直连模型使用连接中对应模型的手动配置。模型名称、URL 和供应商名称都不会自动启用转换。

个人直连在连接编辑器的 **Tool schema compatibility** 中添加模型并选择 Gemini，填写供应商原始模型 ID，
不包含连接的 Prefix ID；同名模型在不同连接中分别配置。没有明确配置时保持 Default，
不会从供应商返回的元数据或同 ID 的服务端模型继承。该设置只启用 Schema 兼容处理，
不把个人直连变成[原生 Gemini 服务器连接](native-model-connections.md)。

启用后，服务端 Chat Completions、Responses 和浏览器直连发送边界共用同一个编译器。
转换发生在最终 Token 预算计算之前，不改变工具名称、调用 ID、结果配对、消息历史或供应商扩展字段。
原始工具定义保持不变；重复转换保持相同结果。

## 为什么没有直接采用原生 JSON Schema

Google 的 [FunctionDeclaration](https://ai.google.dev/api/generate-content#FunctionDeclaration)
分别提供 `parameters`（受限的 Schema）和 `parametersJsonSchema`（JSON Schema），两者互斥。
本功能面向把 OpenAI `function.parameters` 转为前者的网关。
不能通过在 OpenAI 请求里增加 Google 原生字段来升级第三方网关。

研究参考了以下固定版本，代码独立实现，没有复制上游源文件：

- [Vercel AI SDK](https://github.com/vercel/ai/blob/6ac1ee115c4e287be43ebb0e86d884033d85b0cd/packages/google/src/google-prepare-tools.ts)
  将输入定义直接交给 `parametersJsonSchema`；适合其原生 Google 传输。
- [LiteLLM](https://github.com/BerriAI/litellm/blob/a93fee9f6c862708b25225b1c01948a50362fa78/litellm/llms/vertex_ai/common_utils.py)
  区分原生 JSON Schema 与受限 Schema，处理引用、可空类型、字段过滤和联合类型。
  本实现保留非字符串常量的类型，也不把空字符串改成 `null`。
- 输出字段依据 [Google v1beta API discovery](https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta)
  中的 Schema 定义，并对 `anyOf` 使用独占节点的保守形式。

如果网关已经完整支持原生 JSON Schema，可以使用 **Default (no conversion)** 保留原定义。
网关支持情况需要按实际服务验证，不能仅由模型名称判断。

## 转换与执行约束

| 输入                                                        | 发送给网关的形式                                             | 执行时保障                       |
| ----------------------------------------------------------- | ------------------------------------------------------------ | -------------------------------- |
| 本地 `$ref`、`$defs`、`definitions`、静态 `$anchor`         | 先针对原始参数文档解析引用，再展开                           | 使用解析后的原始定义校验         |
| `anyOf` 及其同级约束                                        | 将同级约束合入各分支，节点只保留 `anyOf`                     | 保留原始联合语义                 |
| `allOf`                                                     | 合并可表达的类型、属性、必填项和边界交集                     | 未能合并的约束仍按原定义校验     |
| `oneOf`                                                     | 联合分支及互斥约束说明                                       | 校验恰好命中一个分支             |
| 类型数组、`null`、`nullable`                                | 类型分支及可空标记                                           | 不把缺失、空串和 `null` 混为一类 |
| 字符串 `enum` / `const`                                     | 字符串枚举；空串使用零长度边界                               | 不更改值或类型                   |
| 数字、布尔、对象、数组常量及混合枚举                        | 类型、边界、对象结构或数组条目，并为不可表达部分添加约束说明 | 区分布尔和数字，校验精确值       |
| 元组、`prefixItems`                                         | 条目联合、长度限制及顺序说明                                 | 校验原始位置约束                 |
| `additionalProperties`、`not`、条件、依赖、`uniqueItems` 等 | 描述中附加 JSON Schema 约束，并记录诊断                      | 继续校验原始约束                 |
| `examples`、弃用和读写标记等注解                            | 去掉不支持的注解字段                                         | 不修改默认值或示例中的业务数据   |

描述中的约束用于指导模型生成，不能代替校验。WebUI 原生工具执行入口在审批、直连回调或实际执行之前，
按照原始 Schema 校验参数；无效参数作为工具错误返回，供模型修正，不做强制类型转换。
原始 Schema 允许的额外参数会保留；以 `__` 开头的运行时内部参数不能由模型覆盖。
`format` 按 JSON Schema 注解处理，不额外启用格式断言。

实际发送模型的模式会同步到执行入口。每个调用还保留生成该调用时的 Gemini 校验要求，
因此续轮过滤器或审批恢复切换模型也不会丢失校验。保存的内部标记不会发送给模型供应商。
Default 模式和 legacy 提示词工具调用保留现有行为。
仅使用 WebUI 作为 API 代理、在其他程序执行工具的调用方，需要自行按照原始 Schema 校验参数。

Tool Search 分发的 `call_tool(tool_name, params)` 先解析服务器注册的实际目标，再按目标原始
Schema 校验 `params`，之后才能进入审批或执行。调用携带产生时的兼容模式，审批恢复和模型切换
不会跳过这一校验。原始 `provider_function` 保留封装名称与参数供供应商回放；内部执行、权限和
结果分类仍使用目标身份。同名外部 `call_tool` 不能获得内置分发器身份。

## 明确的边界

每个工具的 `parameters` 必须是自包含的参数文档。本地引用只在该文档中解析，不访问外部 URL。
远程引用、循环引用、动态引用、嵌套引用作用域以及无法表示的无类型值会在发送前返回工具名和 JSON 路径错误。
数组条目需要明确 Schema；不会把任意值静默缩窄成对象。

编译器限制 Schema 深度为 48、访问节点为 8192、联合展开为 128 个分支。
包含注解和常量数据在内的 JSON 容器深度限制为 192；非字符串枚举交集最多执行 8192 次候选比较。
输入、解析后和输出 Schema 各限制为 1 MiB；解析和投影阶段各有 8 MiB 累计工作量限制。
超限会返回明确错误，不发送被截断的工具定义。错误和诊断不包含参数值、默认值或示例内容。

本功能解决工具参数 Schema 的适配和 WebUI 执行校验，不实现 Google 原生传输协议。
工具数量与名称限制、`tool_choice`、思考签名、媒体、结构化输出及特定网关扩展仍取决于网关。
不承诺任意 JSON Schema、任意网关或模型别名都能无损转换。

## 开发与验证

编译器位于 `backend/open_webui/utils/gemini_schema.py`；手动配置解析和请求适配位于
`backend/open_webui/utils/gemini.py`。没有新增项目依赖，使用 MCP 已依赖的 `jsonschema`。

测试使用合成工具定义覆盖引用、联合、常量、空值、嵌套结构、错误路径、资源限制、原定义校验，
以及 Chat / Responses / 直连发送、续轮、审批恢复和模型切换。
本地测试不能替代真实网关验收；应另用实际连接验证请求被接受和工具成功执行。
具体命令见[开发指南](DEVELOPMENT.md#gemini-tool-schema-verification)。

## 与原生连接的关系 / Native transport interoperability

[原生 Gemini 连接](native-model-connections.md)默认使用 `parametersJsonSchema`。
本页的手动兼容模式仍显式选择，面向需要受限 `parameters` 的网关；不能通过 URL 或模型名自动启用。
两者共享原始 Schema 校验、目标身份解析和工具审批约束，但协议选择与 Schema 投影是独立设置。

Native Gemini defaults to `parametersJsonSchema`. The manual compatibility profile remains an
explicit choice for gateways requiring restricted `parameters`; protocol selection and schema
projection are separate settings. Preserve original-schema validation, resolved target identity
and approval checks across both paths. See [verification](DEVELOPMENT.md#native-model-connection-verification).
