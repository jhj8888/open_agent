# 持久沙盒配额与生命周期记录

本增量把 Skill 沙盒的资源预留接入已有运行账本。调用链为 `RuntimeExecutor → OpenSandboxProvider → DurableSandboxJournal → 私有 worker HTTP → RunSandboxJournal/RunLedger → MySQL`；SDK 创建、销毁和隔离检查仍在数据库事务之外。没有修改旧 Kafka、Redis、身份 JWT 或 MinIO 协议。Context7 在本会话不可用；本次复用项目已验证的 Spring JDBC、内部服务认证及 OpenSandbox 官方 SDK 接口，没有新增第三方依赖。

V1.53 增加 `oa_run_sandbox`，一条 attempt/lease 只能拥有一条记录。复合外键同时绑定 tenant、environment、run、attempt、lease，避免把其他租约挂到当前任务。tenant → environment → run → attempt 的锁顺序沿用账本，所有非 `released` 记录计入环境 `max_sandboxes`。此配额与 `max_concurrent_runs` 分别计算；关闭、取消或过期不会自动释放任何一种槽位。

创建顺序：

1. 验证执行许可及实际隔离准入；按部署绑定的 Skill 镜像摘要预留配额。
2. 持久记录 `creating`，只允许 `reserved → creating` 一次成功。确认丢失时不再次调用 SDK 创建。
3. 再次查许可，调用官方 SDK 一次；返回后记录 sandbox ID，再校验隔离并装载制品。
4. 关闭时报告 `cleanup_pending`，尝试销毁；SDK 返回后只上报 `destruction_reported_at`，不释放配额。

预留可以在尚未进入 creating 时按同一配置重复确认；配置变更、重复创建意图、未知沙盒 ID 替换均冲突。所有 Skill 必须绑定同一固定镜像；未绑定 Skill 的任务不能申请沙盒。prod 只接受 Kata profile，展示用 gVisor 仅可用于 test。

取消、租户停用或心跳过期后，同一认证 worker 仍能补报资源 ID 和销毁观察，便于回收；这些回调不恢复执行许可。已释放记录不被迟到消息复活，已知 ID 不允许替换。创建结果不明的 reservation 继续计数，不能用一次空列表或 TTL 推断已回收。

`RunSandboxJournal.releaseVerified` 只供控制面进程内受信回收器使用，没有 worker HTTP 入口。调用者必须独立证明执行进程已停止、物理沙盒不存在，且没有在途创建能迟到成功；传入的验证 ID 引用保留的核验材料。事务同时更新沙盒状态、执行槽和审计，任一写入失败全部回滚。直接调用账本释放槽位也会检查未释放的沙盒记录。

私有接口都在 `/internal/platform/worker/runs/{runId}/sandbox/` 下：`reserve`、`creating`、`created`、`cleanup-pending`、`destruction-reported`。沿用独立 worker 服务密钥；worker 身份由认证取得，dispatcher 和用户 JWT 均不能调用。配额满返回 429，生命周期或租约冲突返回 409，错误不含私有凭据。

数据库/传输测试使用合成核验材料，不声称物理回收器已经交付。独立执行进程终止、真实 execute/reconcile activity 接线及生产 Kata/K8S 故障验收仍需完成。缺少独立证明时保留记录和配额，并交给后续回收流程处理。

验证记录见 [2026-11-04 结果](sandbox-journal-verification-20261104.json)：31 项真实 MySQL 用例、44 项其他 Java 用例和 155 项 Python 用例通过。MySQL 首轮两项在建立 SSH 转发连接时超时，尚未取得数据库连接；仅重跑这两项后通过，原错误保留在证据中。SIGKILL/重启检查同时覆盖创建意图记录，不以重启后重新创建作为恢复方式。测试使用的独占数据库容器和网络已清理，其他应用未重启。
