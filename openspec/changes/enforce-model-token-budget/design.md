# Design

## Context

目前配额 API 显示 dailyTaskTokens，但 tokenBudgetEnforced=false。任务模型有逐调用 start/finish，物理请求有 PermitTransport，能力诊断有独立 Java SDK。预算不得放进通用租约许可，否则耗尽预算会同时阻止事件/产物归档与清理。

## Decisions

1. 控制面独占预算真相。新增持久账本，不在 Python 内存或 Redis 扣减。锁顺序 tenant → environment → reservation；同环境并发预留和配额调整通过既有行锁序列化。事务不包围网络调用。
2. 每次调用预留可信提供方契约的输入+输出总量上限。未提供可靠上限时，严格预算模式拒绝调用；不能使用字符数估计或仅输出 max_tokens 作为硬上限。DeepSeek 官方模型元数据的 context_window 明确定义输入+输出总容量，可作为后续能力验证输入，字段可能缺失。内网通用接口需要模型团队确认同等契约。契约失效、实际 usage 超过声明上限时保留实际用量，并阻断该环境后续预留/发起，等待显式有审计的修复，不丢弃计量来维持表面配额。
3. prepare 使用稳定调用 ID + 请求摘要，回复重试不重复占额。dispatch 采用一次性条件更新；仅首次成功返回可发起，重复确认不产生第二次许可。发起回复丢失宁可保留未知占额，也不重放外部请求。不同请求复用 ID 拒绝。
4. 仅 prepared 且从未 dispatch 的调用允许撤销预留。已发起后的取消、超时、网络错误、进程退出或没有 usage 都不能释放。unknown 保留上限；后续可信同次 usage 可结算。实际 input/output 明细分别校验，幂等重复结算不重复计量，冲突拒绝；缓存已包含在 input 中，不再次扣减。
5. 预算按 Asia/Shanghai 的准入日归属并固定，所有 API 副本从同一 MySQL 时钟读取绝对时间，避免宿主机时钟偏差产生不同预算日期；测试可显式注入 Clock。迟到结算只修正原日。prepared 不能跨日取得 dispatch，须撤销后以新 ID 重新准入；dispatched 跨日完成不重复计入新日。每日任务统计仍按完成日展示，UI 必须明确两者口径。不同日的记录不会被零点删除，未知不是零。
6. 当前增量仅交付内部账本及真实 MySQL 验证。它不注册 HTTP 入口、不开放配置开关、不接管现有 worker 或诊断；tokenBudgetEnforced 保持 false。后续需同时接入两类调用、可信上限配置、启用边界与存量在途处理后才可以声称预算已执行。

## Cross-service Impact

本次新增 TokenBudgetLedger 内部事务服务与 oa_model_token_reservation 表；调用者必须在相同事务的 authorize 回调中重新验证租户/环境/调用身份。该类不是外部授权入口，不能信任 HTTP 客户端提供的 bound 或摘要。它复用 Spring JdbcTemplate/TransactionTemplate 和 JDK Clock，不修改共享身份、Kafka、Redis、对象存储、任务许可或模型 SDK。测试使用独占真实 MySQL，模拟两个服务实例共享数据库；单机锁不参与保证。

## Migration / Rollback

V1.70 仅新增表与 FK/约束/索引，不修改已有业务数据。本次未启用预算或对外提供预留，因此回退 V1.69 应用时保留新表即可，需验证旧应用能够启动且当前无预算记录；以后接入执行后不得回退到忽略预算的版本。备份必须包含该表，恢复不可把已发起的调用当未发起重放。

## Sources / Assumptions

当前没有 Context7 工具；沿用仓库已验证的 Spring JDBC 事务用法，无新 SDK。2026-11-06 查阅官方 [模型元数据](https://api-docs.deepseek.com/api/list-models/) 及 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。提供方上限约束仍需对实际配置验证；不能由文档推断全部内网接口支持，也不能将账本测试视为完整模型调用链已受控。
