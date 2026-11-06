# 模型能力验证

2026-11-06 已交付展示机。进入“模型连接”，在测试环境点击“验证连接”，页面分别显示流式文本、工具调用、结果回传三步的状态、耗时、输入/输出 Token。验证期间禁用重复操作；当前版本变化后原报告明确过期，切换租户/环境清空结果。能力标签表示配置声明，不表示已经验证。

调用路径与边界：工作台 → JWT 管理 API → 实时环境执行授权 → 当前模型版本及服务端凭据 → 原有目的地址/DNS 固定传输 → Spring AI 官方 SDK。诊断只允许一次固定 `add_numbers(2,3)`，返回 `5` 后验证模型的最终响应；不运行 Skill、Shell、任意工具或业务任务。最多三次模型请求，无自动重试。网络交互共用不超过 60 秒的截止时间，连续慢流不会延长；每请求最多 1 MiB、累计正文/思考最多 64 Ki 个 Java 字符、8192 个已解析片段。每个 API 实例最多四个并发诊断。

每步前及返回前重新检查权限和配置版本，撤销或版本变化后不再发起下一步请求。未启用流式的连接不能执行该诊断，未启用工具的连接只验证文本，工具状态保持未验证。没有 usage 时输入/输出分别保留未知，不能填零。关闭浏览器不等于取消服务端诊断，服务端依照截止时间停止。报告通过 `model.probe.completed` 存入环境审计，保留版本、模式和脱敏分步数据，不包含凭据、模型正文或思考文本，不并入业务任务周/月统计。

## SDK 与依赖

沿用 Spring AI BOM 1.1.2，新增同版本 `spring-ai-deepseek` 模块（Apache-2.0），其传递依赖复用已有 Spring AI/Framework/SLF4J。主仓库 2026-11-06 显示约 9.6k stars，见[组件清单](components.md)。版本级许可依据已下载 Maven POM；SDK 使用未修改的官方 JAR，原版许可证保存为 `deploy/platform/control/LICENSE-spring-ai`，项目 NOTICE 已增加来源说明。

当前工具集未提供 Context7，核对了固定版本[官方 API 源码](https://github.com/spring-projects/spring-ai/blob/v1.1.2/models/spring-ai-deepseek/src/main/java/org/springframework/ai/deepseek/api/DeepSeekApi.java)、[高层 ChatModel](https://github.com/spring-projects/spring-ai/blob/v1.1.2/models/spring-ai-deepseek/src/main/java/org/springframework/ai/deepseek/DeepSeekChatModel.java)及[DeepSeek 思考/工具往返规范](https://api-docs.deepseek.com/guides/thinking_mode/)。1.1.2 高层模型构造 assistant 请求时未保留 reasoning_content，且缺少 thinking/stream_options 选项。因此诊断使用官方 DeepSeekApi 的请求/响应 DTO、HTTP/SSE 解码和工具分片合并；通过 Jackson JsonAppend 补入两个缺少的配置字段，并在工具历史中保留官方响应的思考字段。OpenAI-compatible 诊断使用相同公共协议，不发送 DeepSeek 专有 thinking 字段。原 Python DeepAgents 和 Java OpenAI 执行适配不变。

## 验收与部署

- 53 项 Java 检查通过：26 项真实 TCP/SSE 契约、21 项安全检查、6 项完整 Spring 上下文检查。覆盖两种适配模式、思考开关、工具历史、未知用量、错误参数、缺少结束标记、响应上限、连续慢流总截止时间、权限撤销、版本变更、脱敏审计及 401/429/重定向/断流等稳定错误；实际请求计数验证没有自动重放。
- 共享 Python 协议回归 48 项通过，TypeScript/Vite 构建与 11 项 OpenSpec 严格校验通过。第一次前端类型检查发现索引结果可能 undefined，增加既有条件保护下的类型断言后通过。上游构建兼容性提示仍在，不影响本次构建。
- 公网正常 JWT 调用验证已配置的 DeepSeek v2，思考模式开启，三个步骤均通过，共 4380 ms。输入/输出分别为 46/17、339/79、431/2；真实数据库审计与响应逐字段一致，跨租户请求拒绝，业务任务数没有变化。原始结果见[验证记录](model-capability-verification-20261106.json)。未修改模型配置或使用真实业务提示。
- Chrome 实际点击再次通过，共约 3.64 秒；验证中两个相关按钮禁用，结束恢复。三步分别显示 46/17、339/116、468/2 Token；这是另一次真实模型响应，不要求生成量与前次相同。生产环境切换后 DOM 中报告数量为零，验证按钮禁用。截图保存在忽略目录 `.local/model-capability-full.png`，验收浏览器已关闭。
- API SHA256 `c88584c6340b4c3b06d8cb84b1f3c784b0ba6c4d110851125818638e3c8ed2cb`，静态归档 `da8dd1b1ea78430233d78b6a848d020299c809612cd5247c0dd7b484ade40d64`。发布前私有数据库备份，API 维护 45.595 秒；数据库版本仍为 1.69，无新增迁移。公网三个资产摘要、TLS、授权边界、21 项列表/详情用量检查通过。
- 保留任务仍为 58 步、6 次模型调用、4 次工具、两份文件；文件下载摘要正确，缓存/新增 Token 与四个自然周期统计未改变。五个常驻服务 active，观测 outbox 待导出数为零。

本次交付连接级诊断。完整发布能力门禁、调试错误/取消日志管理矩阵和真实内网模型断网验收继续由原 OpenSpec 任务覆盖，不将该项探测当作全部 Agent 路径或生产验收。当前离线 API 镜像归档未随展示 JAR 更新，完整离线交付仍待完成。回退须使用支持数据库 1.69 删除恢复语义的 API，并同步回退静态工作台。
