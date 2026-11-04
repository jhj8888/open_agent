# Tasks

本文件中的未勾选项均为待实现工作；规格校验通过不代表功能完成。前置关系见 docs/platform/roadmap.md。

## 1. 持久模型与准入

- [x] 1.1 新增 run/attempt/event/outbox/配额租约迁移与状态转换层；测试重复键、冲突、非法状态迁移及数据库重启。
- [ ] 1.2 实现跨 API 实例的原子准入与队列背压；并发测试证明活动槽位不超上限并提供配额配置说明。
- [ ] 1.3 实现 outbox 发布和 reconciler，模拟提交前后服务崩溃及重复投递，验证不丢任务、不重复启动。

## 2. 执行与恢复

- [ ] 2.1 接入官方 Temporal SDK 和内网 worker，明确每个 activity 的重试/超时/幂等边界；测试服务/worker 断连及心跳丢失。
- [ ] 2.2 迁移一条 Python Agent 执行路径到 run/attempt 上下文，再迁移 Java/工作流入口；分别测试安全重试与未知副作用失败，更新恢复语义文档。
- [ ] 2.3 实现取消、fencing、截止时间和沙盒释放；故障注入验证迟到 worker 不再发起后续操作且终态不反转。

## 3. 查询与联调

- [ ] 3.1 提供持久任务/事件查询和 SSE 重连，覆盖越权、事件过期、重复事件与浏览器断线测试并补充 API 文档。
- [ ] 3.2 进行双租户并发和控制面/调度器/worker 重启演练，保存实际吞吐、排队时间、恢复时间及配额泄漏检查结果。

## 2026-11-04 实现证据

V1.52、RunLedger/RunOutbox 与状态转换层已通过 21 项真实 MySQL 8.4.13 用例，含幂等并发、原子队列/执行槽、取消竞态、过期租约、outbox 重投和独占数据库 SIGKILL/重启；另外 3 项状态/输入契约及 33 项既有租户/Skill 回归通过。详见 `docs/platform/run-ledger-verification-20261104.json` 与 `docs/platform/durable-runs.md`。

1.2 的数据库并发机制、1.3 的消息领取/确认已有证据，但 API 多实例联调、Temporal 发布/补偿及公平调度尚未完成，保持未勾选。数据库重启检查不等于完整 worker、沙盒或生产集群故障验收。

后续增量已加入官方 Temporal SDK 1.35.0、稳定 workflow ID 的 outbox 发布器、独立内部服务身份及租约绑定模型/Skill 读取。22 项 Python 调度/传输测试通过（含真实 Temporal 服务、30 秒心跳丢失、SQLite 服务重启）；MySQL 增至 24 项；Java 模型和内部接口 37 项通过。展示机开发服务已返回 SERVING，监听均为 loopback。见 `docs/platform/temporal-runtime.md` 和对应验证记录。实际 execute/reconcile activity、清理补偿及公共 API/聊天未接通，1.3、2.1–2.3 仍不勾选。

真实执行器增量：RuntimeExecutor 已在展示机用真实 DeepSeek 驱动 DeepAgents 和隔离 Skill，观察到 UID 1000 执行及物理沙盒删除；操作许可、心跳取消和重复清理已有测试。54 项执行/传输/沙盒、9 项 Java 接口与 209 项既有 Python 回归通过，见 `docs/platform/executor-verification-20261104.json`。这次实机准入/journal 是测试实现，完整数据库/Temporal/执行器接线与跨进程强制回收仍未完成，相关任务保持未勾选。

沙盒 journal 已增加数据库原子预留、一次性 creating 状态、取消后仍可补报资源以及独立核验后释放的事务边界。MySQL 检查增至 31 项，含创建意图跨 SIGKILL/重启保存、配额竞争、未知创建持续占额和审计失败回滚。另有 44 项 Java 与 155 项 Python 检查通过，首轮两项 SSH 连接超时的重跑记录保留。见 `docs/platform/sandbox-journal.md`；实际回收器与完整 worker/API 接线仍未完成，不据此勾选 1.3 或 2.3。
