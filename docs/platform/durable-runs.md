# 持久任务实现记录

本变更在 Astron Java 控制面新增独立的任务账本。内部 worker HTTP 接口和 Temporal 调度骨架已有增量，见 [Temporal 接入记录](temporal-runtime.md)；公共 HTTP/聊天和真实执行 activity 尚未接通，完整在线任务平台仍未交付。

## 调用链与共享层影响

目标调用链：已认证用户/部署凭据 → deployment 授权与版本绑定 → RunLedger → MySQL run/event/outbox → Temporal 官方 SDK → Python worker → 固定模型版本/私有 Skill 沙盒。

任务账本不替代鉴权：后续公共入口必须先解析可信 tenant/environment、主体与部署授权；提交和取消同时传入授权重检回调，在租户锁内复查，防止治理变更竞态。worker 方法通过新增的内部服务身份入口调用；UUID lease_id 和 fencing token 本身不替代内部身份。新增路由有独立安全链，未开放公共提交接口。

V1.52 只新增部署快照、Skill 绑定、run、attempt、event、outbox 表，通过组合外键关联 V1.49–V1.51 的租户环境、模型固定版本和 Skill 环境授权。不改动旧 workflow、Redis、Kafka、Celery 或其入口。模型凭据不进入 run/outbox，任务记录不返回输入或执行快照。部署创建/审批/服务凭据仍由后续 API 变更落实。

## 持久语义

- 提交将 run、初始事件、Skill 保留引用、审计及 start outbox 放在一个 Spring 事务内。幂等键按租户/环境/部署隔离，请求摘要包括规范排序的 JSON 和超时秒数；相同键不同内容冲突。同一键跨主体不返回原任务。
- 所有写入使用 `tenant → environment → run → attempt` 锁顺序和 READ COMMITTED。租户环境的队列上限与执行上限由真实数据库串行准入，跨 JVM 实例共享。`max_queued_runs=0` 暂停新接入；排队不占执行槽位。配额下调不终止已有任务，但阻止继续超额准入。
- 每个 claim 生成唯一 lease_id、worker_id、attempt 与 fencing token。重复 claim 不创建第二 attempt；目前不自动重试失联的执行。
- 心跳有效期 30 秒且不超过任务截止时间；过期不可续租。取消或冻结租户后，心跳和 worker 事件写入失败关闭。执行器后续必须在每次模型/工具操作前验证许可，不能只在开始时检查。
- 排队取消在一个事务中记录 cancel_requested 和 cancelled，后续不会开始执行。运行中取消进入 cancel_requested，并持久发送 cancel outbox。完成与取消通过行锁及 revision 条件更新竞争，终态不可反转；失去有效心跳后的成功报告会记为 `failed/outcome_unknown`。
- 完成任务只把 attempt 标为 cleanup_pending。**心跳超时、任务终态或 TTL 到期均不会自动释放执行槽位**；可信清理器确认沙盒删除且旧 worker 不再有副作用后，才可调用 resourcesReleased。尚未接入该清理器，不能把数据库方法存在视为物理隔离验收。
- 每条事件使用 run 内单调序号和独立幂等键。重复键不同内容冲突；worker 事件每条最多 16 KiB、每任务最多 1 MiB/4096 条，状态终结事件保留额外空间。返回分页支持 `after`，过期历史明确返回 event_history_expired；SSE 传输和周期清理仍待接入。
- RunOutbox 用 `FOR UPDATE SKIP LOCKED` 让多个发布器领取不同消息，确认和退避重投均校验领取令牌及有效期。发布器崩溃后可重新领取，旧确认不能覆盖新领取；这是至少一次投递存储。新增官方 Temporal 发布器使用稳定 run_id 去重，已通过实际服务测试，完整 SQL→HTTP→真实 Agent 联调仍待完成。

## 验证方式

2026-11-04 在 Java 21/Maven 3.9.9 与独占 MySQL 8.4.13 容器完成 57 项检查，0 失败、0 错误、0 跳过，其中真实数据库用例 21 项，包含数据库 SIGKILL/重启。经过固定 SSH 主机验证连接临时数据库，不开放数据库公网端口。可审阅 [用例及源文件摘要](run-ledger-verification-20261104.json)。这些检查尚不覆盖完整 HTTP/Temporal/沙盒执行链。

`RunContractTest` 检查状态终态、规范 JSON 和输入边界。`RunLedgerMySqlTest` 直接运行 Java 事务代码，不以 SQL mock 或 H2 代替 MySQL 锁行为。设置以下环境变量后执行 Maven：

Linux Docker 环境可运行 `python3 console/backend/tests/run_durable_checks.py`，脚本自行创建独占 MySQL、注入随机测试密码、执行 Java 检查及数据库崩溃恢复，最终按标签核对并清理自己的容器。镜像应提前准备；CI 使用同一固定摘要。下列方式适用于已由受控驱动提供临时数据库的环境：

```sh
export OA_RUN_TEST_JDBC_URL='jdbc:mysql://127.0.0.1:3306/?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC'
export OA_RUN_TEST_DB_USER='root'
# OA_RUN_TEST_DB_PASSWORD 从独立临时测试环境注入，禁止使用生产数据库。
mvn -B -ntp -f console/backend/pom.xml -pl hub -am \
  '-Dtest=Run*Test' -Dsurefire.failIfNoSpecifiedTests=false test
```

测试为每个用例建立 `oa_run_verify_<随机值>` 数据库并清理，只接受 loopback JDBC。`OA_RUN_TEST_RESTART_SIGNAL` 是可选测试信号文件，受控测试驱动在收到 restart 后对其独占的 MySQL 容器执行崩溃/重启并写回 ready；没有该驱动时不设置此变量。数据库密码不得放在 JDBC URL、提交或日志中。

## 仍待完成

真实执行 activity、独立补偿扫描、租户轮转公平性、token/sandbox 配额、可信清理证明、部署审批、公共 HTTP/SSE/聊天、全栈故障恢复及离线部署尚未接通。模型/Skill 内部读取接口、独立服务身份和调度骨架已有验证，详见后续增量记录；新持久表不代表端到端能力已经完成。

Context7 在当前工具集中不可用，使用现有版本源码和官方文档核对：[Spring 6.2 TransactionTemplate](https://docs.spring.io/spring-framework/reference/6.2/data-access/transaction/programmatic.html)、[MySQL 8.4 锁定读](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)。本阶段复用既有 Spring JDBC/事务组件，没有添加运行时依赖。
