# 每日任务用量

租户管理的配额页展示当前环境今日任务模型的已知 Token 用量，作为预算执行前的计量基础。`GET /platform/tenants/{tenant}/environments/{environment}/quotas` 增加 `dailyTaskTokens`，原配置字段、并发占用、修订号和权限契约保持兼容。

## 计量契约

- 使用 `Asia/Shanghai` 自然日，范围为当天零点至 `asOf`，左闭右开；返回 `date/timezone/start/end/asOf`。`asOf` 与配额 `sampledAt` 相同。
- 以叶子 `model` 步骤结束时间归日，不以任务创建时间归日，不按页面条数截断。跨日调用结束后计入结束日；不重复累加父 agent/chain。
- `knownTotalTokens = inputTokens + outputTokens`。缓存已包含在输入中，不重复相加。失败、取消及用户删除任务后，保留实际历史计量。
- `cacheReadTokens`、`uncachedInputTokens` 只汇总已报告缓存明细的调用；没有明细时为 null。`unknownCacheModelCalls` 提示缺失数量，不能将部分缓存合计称为全部缓存量。
- `unknownModelCalls` 是今日已结束但 usage 未知的调用数。`unsettledModelCalls` 包含采样前开始但尚无结束记录的所有日期调用；它不证明进程仍在运行，也不等于零消耗。
- 仅统计任务模型调用。模型连接诊断不在任务步骤表中，不计入此视图。存在未知或未结算调用时，已知合计不代表全部消耗。
- `tokenBudgetEnforced` 仍为 false。不能把事后汇总当作请求前预算预留；自动限制仍需统一入口、并发预留、跨日及未知结算规则，未完成前页面明确标明未启用。

## 调用链与影响

工作台 → JWT 治理控制器 → `TenantGovernanceService.quotas` → MySQL `oa_run_step`。在既有只读 REPEATABLE_READ 事务中，与配置和资源占用一起读取；每次重新校验 tenant/environment 的 METADATA_READ，不返回任务正文、工具参数或凭据。使用已有按租户/环境/模型类型/结束时间索引；未新增迁移，不改变运行时、身份、模型 SDK、对象存储或任务删除协议。回滚兼容 V1.69。

实现使用仓库已有 Spring JDBC/事务及 JDK 时间接口。当前工具中没有 Context7；未引入新框架、依赖或未经核验的 SDK API。

## 验证

真实 MySQL 用例覆盖 105 个模型步骤、北京时间午夜两侧、跨日未结束、失败/取消/删除、父步骤排除、缓存 0/全量/缺失、跨租户/环境及权限撤销；已有并发重复回调测试增加每日用量核对。独立只读验收脚本 `deploy/platform/control/verify_daily_task_tokens.py` 读取公开 HTTPS 配额响应，并使用 Python 对逐条数据库记录核算，拒绝将比较期间变化的数据作为通过证据。

2026-11-06 共 16 项相关 Java 检查通过（5 项真实 MySQL、5 项治理安全、6 项 Spring 启动/入口）。首轮新增测试夹具直接修改终态，缺少必需字段，被数据库约束拒绝；改为调用正式取消接口后单项复测通过，未修改运行实现来绕过约束。前端构建和 11 项规格校验通过。

公网三个租户与逐条数据库记录一致；`space-4` 当天 23 次模型调用，输入 75,615、输出 1,487、已知合计 77,102，缓存 66,560、新增输入 9,055。Chrome 正常登录后核对同一组数值、刷新采样时间、空租户和未授权生产环境清空；未使用伪造页面响应。见[只读接口核验](daily-task-tokens-verification-20261106.json)及[构建/部署/浏览器证据](daily-task-tokens-delivery-20261106.json)。

部署前保存私有 SQL 备份，API 维护 43.612 秒，schema 仍为 1.69。API SHA256 `6bb3b96b70da19e9b0bc321b7d411adc4a473d2bb27aabea7125dee0a705e643`；静态包 `a8bb53f0f1a5ce60ba9a691c71973ed77647a08a083d391e49de94d7c2dfd276`。原任务 58 步、2 份文件摘要、缓存和四个统计周期、公网授权及 5 个服务复查通过，观测待补发为零。当前完整离线 API 镜像归档尚未同步此 JAR；生产集群准入仍关闭。每日自动限额、整个管理权限矩阵和生产集群验收不在本次完成声明中。
