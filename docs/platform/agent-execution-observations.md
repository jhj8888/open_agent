# Agent 逐步观测

2026-11-05 已部署逐步正文。任务详情 →「Agent 执行过程」展开模型/工具步骤，可查看增量输入、模型实际返回的 think、工具请求/结果与输出，同时保留状态、耗时、父步骤及逐次用量。监控概览提供自然周/月、每日数据及 CSV 导出。历史任务没有采集的正文不会补造。

## 缓存与新增输入 Token

模型步骤摘要与详情区分输入中的缓存命中和新增（未命中缓存）部分，例如 `输入 3,213（缓存 3,072 · 新增 141）/ 输出 72`。新增不是当前轮新增消息长度；缓存不再叠加到总 Token。依据 [DeepSeek 官方缓存协议](https://api-docs.deepseek.com/guides/kv_cache/)，固定版本的官方适配器已将 `prompt_cache_hit_tokens` 映射到 `input_token_details.cache_read`，平台以 `input_tokens - cache_read_tokens` 计算新增。

任务总计跨所有步骤聚合；周/月主汇总、每日数据和 CSV 同样提供缓存与新增。仅对模型叶子且有合法缓存明细的记录拆分；零命中明确显示 0，全命中时新增为 0。未报告缓存的旧记录显示未知；混合数据标记「已知缓存 / 已知新增」和未知调用次数，不把所有输入减去部分已知缓存作为新增。没有任何明细时接口返回 null、CSV 留空。没有引入数据库迁移、依赖升级或模型调用重放。

已部署并用旧任务 `e6eaaf2e` 核验 20 页：缓存 15,232、新增 6,042，合计输入 21,274。7 项 MySQL、5 项 Spring、3 项前端格式检查及构建通过；其中新测试的日期 mapper 夹具修正后单独复测。真实浏览器确认步骤、任务总计和周报，周报缓存 142,080 / 新增 25,726 = 输入 167,806。CSV 新列已发布；本轮下载事件超时，未将实际下载文件核对计为通过。详见[缓存明细验收记录](cache-tokens-verification-20261105.json)。

## 本次正文交付

展示 Agent「逐步观测 Skill 演示」，任务 `e6eaaf2e-08a1-4c67-b5b0-e75910ddcf8d`：58 步，6 次模型、4 次工具；输入 21,274 / 输出 968 Token，合计 22,242，与任务总量一致。2 个模型步骤实际返回 think，其他步骤明确显示未返回。四次追加输入与最后回答阶段上下文变化被正确区分。两份文件可预览、下载，摘要与大小一致，CSV 数量合计为 6。合成凭据标记已脱敏，第二租户访问被拒绝。浏览器实际展开了增量输入、think、工具请求和最终输出。

模型连接新增显式 DeepSeek 适配器与思考开关，创建不可变 v2；旧 v1 部署保留原行为。使用 MIT 许可的 `langchain-deepseek==1.1.1`；根据[官方思考模式协议](https://api-docs.deepseek.com/guides/thinking_mode/)和固定 SDK 源码，增加仅回传原始 `reasoning_content` 的子类兼容补丁。HTTP、SSE、工具序列化、usage 仍由官方 SDK 执行，网络准入和租约传输保持原路径。后续 SDK 修复该字段回传后应移除子类。

采集副本先脱敏，再按每字段 6 KiB UTF-8 截断并保留首尾；不修改实际模型上下文。已知模型密钥、可识别敏感键/Bearer/sk- 字段及本任务已识别秘密的后续回显被替换。此规则不能识别所有任意业务秘密，内容仍严格受任务权限保护。二进制/非文本块不内嵌，内部 chain 只存结构摘要。V1.59 增加模型配置，V1.60 增加每 attempt 4 MiB 正文计数器；超额仅显示 `run_limit`，继续记录 Token、状态和产物，不中止任务。

86 项 Python 检查通过，随后脱敏增强的 14 项针对性复测通过；30 项 Java 检查通过（4 项真实 MySQL、21 项模型安全、5 项 Spring 上下文）。其中新数据库测试的日期 mapper 夹具先失败，修正后单独复测通过。前端构建、5 项登录会话测试、11 项 OpenSpec 和公网 HTTPS/授权检查通过。见 [正文验收证据](step-content-verification-20261105.json)。

展示机的新增 wheel 先在开发机从 PyPI 下载并校验 SHA256 `aec298233606b1289898543e56f90373f71e919b586f580f5e161c018ab21dcb`，上传后用官方 uv `pip install --offline --no-index --no-deps --find-links <wheel目录> langchain-deepseek==1.1.1` 安装，最后 `uv sync --frozen --offline` 审核成功。不能将此单个新增包验收等同于完整平台离线交付。

## 已验证链路

可信执行器 → 官方 LangChain `AsyncCallbackHandler` → 私有 Java worker API → V1.58 MySQL 步骤表 → 现有任务权限 API → 工作台。仅修改平台 Runner 的显式绑定路径，旧 Agent 路径不自动开启此回调，不改变公共 OTel 全局配置。

冻结版本：DeepAgents 0.7.29、langchain-core 1.6.12、langchain-openai 1.7.2、LangGraph 1.2.16。用法依据[官方回调接口](https://reference.langchain.com/python/langchain-core/callbacks/base/AsyncCallbackHandler)和安装包源码。平台特有的租约、授权与自然周期投影由业务适配实现。

- 模型叶子调用的 `AIMessage.usage_metadata` 计量；缓存字段保留且不再次加到输入；未知用量不伪装为 0。工具直接用量为 0，父步骤仅作组织，不重复计量。
- 开始与结束各自以步骤 ID / 哈希去重。开始必须有执行许可；结束允许取消后由原租约补记。失去进程的未结束步骤显示中断，不虚构结束时间或 Token。控制 API 临时故障最多重试三次同一正文，不重放模型/工具。
- 结构摘要包括消息角色/长度、参数字段/类型和返回长度；模型/工具正文遵循上面的脱敏与容量边界。不记录 SDK 配置、鉴权头、原始异常或不可得的内部推理。
- 查询沿用实时任务授权，服务凭据仅可访问自己的任务；平台管理员身份不自动授予其他租户业务权限。
- UTC 存储且 JDBC 会话强制 UTC。Asia/Shanghai 自然周周一零点、自然月一日零点，半开区间；任务按创建时间、调用按结束时间。P95 为最近秩法，任务耗时是墙钟耗时，不累加并行步骤。工具/模型/部署分类返回前 100 组，主汇总与每日统计不受列表分页影响。

接口：`GET /api/v1/runs/{run}/observations?after=0&limit=100`；`GET /platform/tenants/{tenant}/environments/{env}/observation-statistics?period=this_week`。步骤最多 4096/attempt，传输 JSON 最多 128 KiB（各正文字段仍受 6 KiB 限制），每页最多 200 条。

## 真实验收

任务 `d33a2abc-ae0b-43e5-b7dd-5670d9ee825e` 实际读取 Skill、写/读 CSV、运行 Python 标准库计算、生成说明文件。87 步，9 次模型、8 次工具，输入 35,207 / 输出 1,537 Token，与任务级 36,744 总量相符，父子关系和两份文件的工具调用关联完整。

9 项 Java 检查通过（3 项真实 MySQL、1 项自然周期边界、5 项完整 Spring 上下文）；34 项 Python 检查通过；前端构建、11 项 OpenSpec 校验、HTTPS 资产/认证检查通过。真实 MySQL 包含 65 个模型步骤、跨周/月任务归属、未知计量、缓存不重复、并发去重、取消/权限收回；浏览器确认了执行视图和周报并实际下载 CSV。

最初真实验收任务 `90084a43-210f-41e2-8c16-2e29586a5d84` 在模型调用前因 Skill UUID 观测字段不能 JSON 序列化而失败。已修复为显式字符串并增加执行器回归测试，失败记录保留。详见 [验收证据](agent-observability-verification-20261105.json)。

## 独立观测后端

已部署 MLflow 3.18.0 和官方 OTel 1.45.1 exporter，V1.61 的独立持久队列按 revision、服务身份与有限租约补发。`export_state` 由当前 outbox 确认状态计算。任务结束且物理资源释放后导出；运行中仍通过平台查看。后台故障、重复导出、禁止外联重启和权限已实机验证，见 [部署边界与证据](mlflow-observability.md)。

正文是步骤结束后展示已返回的内容；不是逐 Token 实时 think 推送。旧记录不补造。真实框架压缩、进程强杀与并发故障完整矩阵，以及生产 K8S/HA 与整个平台离线验收继续推进。
