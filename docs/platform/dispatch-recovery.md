# 任务投递故障验收

这条验收连接真实 MySQL、打包 Java API、官方 Python ControlPlaneClient/OutboxDispatcher 和独立官方 Temporal 开发服务。数据库、API、Temporal、调度进程均为测试专属；模型与用户脚本不参与。

2026-11-06 完整用例重跑通过：JUnit 1 项、0 失败/错误/跳过，用时 202.1 秒；Python 投递阶段 147.531 秒，包含两次实际 61.016 秒等待。SQL 最终为 4 个 run、4 条 delivered outbox、3 个 attempt、3 条合成执行事件；三个成功任务均只执行一次，一个投递前取消任务没有 attempt 或 Temporal workflow。多 API 配额回归另在前一批通过。详见[验收证据](dispatch-recovery-verification-20261106.json)。

首轮最后的 SQL 事件断言漏写存储层 `worker:` 前缀，导致用例失败；按真实事件契约修正后，加入实际提交前 API 强杀并完整重跑，未通过删断言或修改业务记录绕过。成功夹具的 JVM、10 个调度子进程、Temporal、数据库/网络和临时文件均已清理；失败首轮仅保留本机私有诊断文件。

## 故障边界

1. 在独占 schema 的 outbox INSERT trigger 中触发事务失败：公开提交必须返回 503，任务与 outbox 均为零；移除 trigger 后使用原键重试。
2. 使用独占 outbox 表的插入间隙锁暂停提交，在 `performance_schema.data_lock_waits` 中确认 API 的实际等待，再强杀 JVM；重启后任务和 outbox 均为零。同键重试成功后再次强杀 API，重启仍返回同一任务并保留待投递记录。
3. 调度子进程领取 SQL outbox 后、尚未调用 Temporal 时终止：另一进程立即扫描不可抢占；自然等待生产的 60 秒领取期限后重新投递。
4. Temporal 接收启动后、SQL 确认前终止调度进程：保持该执行活动运行，等待真实领取到期后重投；Temporal 的 execution run ID、activity 次数和数据库 attempt 不得增加。
5. SQL 确认已提交、调度进程尚未返回时终止：后续扫描没有可重投消息，原执行继续完成。
6. 排队任务在投递前取消：确认它的 start outbox，但不创建 Temporal workflow 或业务 attempt。

除初始事务失败 trigger 外，不回写数据库状态、时间或计数来制造成功；领取到期通过实际等待实现。暂停点只在测试子类与子进程中，生产代码没有新增故障开关。执行 activity 使用真实 worker HTTP claim、permit、event、heartbeat、finish，写入一个明确标识的合成事件；父进程再独立查询 SQL 和 Temporal history。

## 边界与复现

这里的 reconcile activity 是合成实现，只读取已存在的成功终态。测试刻意保留三个 cleanup_pending attempt，不调用物理清理或伪造释放证据。因此它证明投递幂等和提交边界恢复，不能单独关闭含独立回收器的完整 OpenSpec 任务 1.3，也不证明生产 Temporal HA、Kata 或未知执行实体恢复。

演示站实际 API 制品仍是受测 SHA256 `5b7cb30e9232ed73ac54dcf384b19433c806c62e288148c12deb5dff797fe18b`，本轮没有业务运行时改动。故障测试后原 58 步、2 文件下载摘要、20 页缓存用量、四个统计周期和 21 项公网用量对照通过；五个服务 active、观测 outbox 待补发为零。没有将合成执行记录写入演示业务数据库。

`console/backend/tests/run_durable_checks.py` 现在同时运行事务、多 API 和投递故障验收。运行前准备 Java 21、Maven、Docker、已锁定 MySQL 镜像，并设置：

```sh
export OA_RUN_DISPATCH_PYTHON="$PWD/core/agent/.venv/bin/python"
export TEMPORAL_TEST_BINARY="/absolute/path/to/preverified/temporal"
python3 console/backend/tests/run_durable_checks.py
```

Python 使用仓库锁定环境（本次为 temporalio 1.35.0、httpx 0.28.1），Temporal CLI 1.10.0 必须提前核验，不自动下载。驱动缺少前置条件时直接失败；它自行构建专用平台 JAR、自建临时 MySQL、设置 Java 集成测试环境并清理容器。GitHub 的 durable-run-database 作业已接入相同命令、冻结环境与 SHA256 校验的 Linux CLI；远端 CI 是否通过需另行查询。

Windows 验收使用已有受控 SSH 转发驱动连接展示机临时 MySQL，API、Python 和 Temporal 进程在本机运行。Java 进程提供 `OA_RUN_MULTI_API_JAR`、`OA_RUN_DISPATCH_SCRIPT`、`OA_RUN_DISPATCH_PYTHON` 和 `TEMPORAL_TEST_BINARY`，运行 `RunLedgerMySqlTest#durableOutboxSurvivesDispatcherProcessFailures`。配置经子进程 stdin 传递，不进入命令行或输出；子进程只获得本次验证所需的临时凭据，不使用演示业务密钥。

失败时保留 `oa-multi-api-*` 临时目录诊断，成功后关闭 API、调度子进程、JWKS 和 Temporal，删除临时文件；JUnit finally 删除独占 schema，外层驱动按所有权标签删除其 MySQL 容器与网络。不会重启展示环境的 Temporal、worker 或业务数据库。

当前工具无 Context7，按官方 [Temporal Client](https://python.temporal.io/temporalio.client.Client.html#start_workflow) 与 [WorkflowEnvironment](https://python.temporal.io/temporalio.testing.WorkflowEnvironment.html#start_local) 文档及已安装 SDK 源码核对；未新增业务运行时依赖。

提交前崩溃的定位使用 MySQL 官方 [data_lock_waits](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-data-lock-waits-table.html) 与 [InnoDB 锁](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)，只锁住一次性验证 schema，不锁演示业务表。
