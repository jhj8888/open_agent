# Agent 逐步观测

2026-11-05 第一阶段已部署至展示机。任务详情 →「Agent 执行过程」可筛选模型、工具及全部框架步骤，查看状态、开始/结束、耗时、父步骤和逐次用量。监控概览增加本周、上周、本月、上月、每日数据及 CSV 导出。历史任务没有采集的步骤不会补造。

## 已验证链路

可信执行器 → 官方 LangChain `AsyncCallbackHandler` → 私有 Java worker API → V1.58 MySQL 步骤表 → 现有任务权限 API → 工作台。仅修改平台 Runner 的显式绑定路径，旧 Agent 路径不自动开启此回调，不改变公共 OTel 全局配置。

冻结版本：DeepAgents 0.7.29、langchain-core 1.6.12、langchain-openai 1.7.2、LangGraph 1.2.16。用法依据[官方回调接口](https://reference.langchain.com/python/langchain-core/callbacks/base/AsyncCallbackHandler)和安装包源码。平台特有的租约、授权与自然周期投影由业务适配实现。

- 模型叶子调用的 `AIMessage.usage_metadata` 计量；缓存字段保留且不再次加到输入；未知用量不伪装为 0。工具直接用量为 0，父步骤仅作组织，不重复计量。
- 开始与结束各自以步骤 ID / 哈希去重。开始必须有执行许可；结束允许取消后由原租约补记。失去进程的未结束步骤显示中断，不虚构结束时间或 Token。控制 API 临时故障最多重试三次同一正文，不重放模型/工具。
- 默认结构摘要包括消息角色/长度、参数字段/类型和返回长度，不记录完整提示词、工具参数正文或异常正文；用户于本次验收新增的增量正文、think、工具结果展示作为后续增量实现。
- 查询沿用实时任务授权，服务凭据仅可访问自己的任务；平台管理员身份不自动授予其他租户业务权限。
- UTC 存储且 JDBC 会话强制 UTC。Asia/Shanghai 自然周周一零点、自然月一日零点，半开区间；任务按创建时间、调用按结束时间。P95 为最近秩法，任务耗时是墙钟耗时，不累加并行步骤。工具/模型/部署分类返回前 100 组，主汇总与每日统计不受列表分页影响。

接口：`GET /api/v1/runs/{run}/observations?after=0&limit=100`；`GET /platform/tenants/{tenant}/environments/{env}/observation-statistics?period=this_week`。步骤最多 4096/attempt，单条开始/结束正文最多 8 KiB，页面最多 200 条。

## 真实验收

任务 `d33a2abc-ae0b-43e5-b7dd-5670d9ee825e` 实际读取 Skill、写/读 CSV、运行 Python 标准库计算、生成说明文件。87 步，9 次模型、8 次工具，输入 35,207 / 输出 1,537 Token，与任务级 36,744 总量相符，父子关系和两份文件的工具调用关联完整。

9 项 Java 检查通过（3 项真实 MySQL、1 项自然周期边界、5 项完整 Spring 上下文）；34 项 Python 检查通过；前端构建、11 项 OpenSpec 校验、HTTPS 资产/认证检查通过。真实 MySQL 包含 65 个模型步骤、跨周/月任务归属、未知计量、缓存不重复、并发去重、取消/权限收回；浏览器确认了执行视图和周报并实际下载 CSV。

最初真实验收任务 `90084a43-210f-41e2-8c16-2e29586a5d84` 在模型调用前因 Skill UUID 观测字段不能 JSON 序列化而失败。已修复为显式字符串并增加执行器回归测试，失败记录保留。详见 [验收证据](agent-observability-verification-20261105.json)。

## 尚未交付的部分

当前是受任务授权的步骤投影；`export_state=pending` 仅预留外部投递标记，**没有启动 Langfuse/MLflow 后端或持久 exporter**，不声称已实现后端故障补发。官方观测后端离线部署、SDK 导出/恢复、强杀/真实压缩/并发故障矩阵继续推进。Langfuse 推荐拓扑超出当前共享机预算；[MLflow 官方支持 OTLP/HTTP 接收](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/ingest/)，可作为后续轻量后端实测候选，未以文档支持替代实机验收。

新增正文需求：每步骤增量输入、模型实际返回的 think/思考摘要、工具结果和输出；保留脱敏、内容上限、明确截断及任务授权。缺失或历史未采集的字段必须明确标记，不从最终回答生成假推理。
