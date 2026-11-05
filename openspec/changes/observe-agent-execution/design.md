# Design

## Context

`core/agent/engine/deepagents_runner.py` 已有模型 middleware、UsageMetadataCallbackHandler、OTel span 与原生工具事件；`durable/executor.py` 目前仅把工具名称和总 usage 持久化。观测扩展不得改变任务终态、租约与物理回收的事实来源。选型证据见 `docs/platform/agentic-observability-evaluation.md`。

## Decisions

1. 使用官方 LangChain/DeepAgents 回调和观测 SDK，复用现有 OTel 基础设施；锁定 SDK 版本，确认与 DeepAgents 0.7.29 的依赖兼容。新增跨服务公共层前先验证原 Agent 路径不被自动启用或发往外网。
2. run_id 是平台关联键；attempt、step_id、parent_step_id、调用/重试序号由可信执行器产生。所有持久事件携带稳定去重键；服务端从有效租约解析租户/环境，禁止自报其他任务/trace。旧任务的缺失步骤不补造。
3. 步骤类型覆盖上下文/Skill 加载、agent/chain、模型 generation、工具执行和产物发布。记录已授权摘要、耗时、状态、错误分类、文件/版本引用。默认禁记录凭据、HTTP 鉴权头和任意无界输出；详细输入输出先脱敏再入库/导出，沿用任务权限。并不要求或伪造不可获得的模型隐藏推理。
4. 逐模型调用保留 provider usage 与来源。纯工具直接 Token 为 0；调用模型但无 usage 为 unknown；可选估算单独标记。缓存 Token 不与包含它的输入 Token重复相加。父步骤聚合与叶子计量分离，实际重试调用计入总量，传输重试不重复记账。
5. UTC 持久化，默认 Asia/Shanghai 的自然周（周一零点）/月（一日零点）计算半开区间；本周/上周/本月/上月可选，返回明确起止和时区。任务数量按创建时间、调用量按结束时间归属，提供每日趋势与租户/环境/部署/工具维度，状态/Token/均值及 P95 分别说明口径。
6. 浏览器仅访问现有 Java 权限入口。LOG_READ 决定可见任务步骤与周期数据；平台管理员不自动获得其他租户业务内容。观测项目密钥仅可信进程持有；Langfuse 仅内部运维访问，不能靠前端过滤共享项目实现隔离。所需授权、审计和脱敏不调用 EE 功能。
7. 官方导出采用受限重试与容量控制，并与持久事件/outbox 关联；宕机/导出失败可见且可补发，不触发模型/工具执行重放。进程强杀产生中断/缺失状态，不能伪造完整性。

## Migration / Verification

### First delivery boundary

调用链为 `RuntimeExecutor -> 官方 AsyncCallbackHandler -> ControlPlaneClient -> TaskObservationService -> MySQL`，查询沿用 `DeploymentAuthorization + AgentDeploymentService.locked`。不修改公共 OTel 全局配置，不启用外网 exporter。每个开始/结束分别以稳定步骤 ID 和内容哈希去重；开始需有效执行许可，结束允许同一租约在取消后补记已有步骤。模型调用前必须有持久开始记录，平台控制面不可用时停止继续执行（与现有 permit 同一故障边界），独立观测后端不可用则保留数据库导出待办，不影响模型/工具执行。强杀留下的开始记录展示为中断且用量未知。

默认采集角色/长度、参数字段/类型、返回长度及工具文件关联，不采集完整提示词、工具输入正文、异常正文或隐藏推理；所有字段有长度和数量上限。官方 SDK 不提供本平台租约校验、任务授权及自然周期业务统计，这些适配由平台实现。叶子模型 usage 从官方 AIMessage.usage_metadata 提取，框架压缩模型同样通过继承回调采集。

迁移增加步骤/上报状态与查询索引，不覆盖既有事件/usage；特性关闭时保留当前行为且无外联。先 mock/provider 契约和真实数据库计量，再真实 Skill 执行；覆盖同时运行、取消、上下文压缩调用、重复发送、导出故障、窗口边界与越权。官方后端预导入镜像/依赖并阻断公网测试；当前展示机资源不足以直接承诺完整 Langfuse 推荐拓扑，部署需先实测资源。
