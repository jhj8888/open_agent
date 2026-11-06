# 多实例准入与队列背压

2026-11-06 验收使用两个独立 Java 21 `PlatformApplication` 进程，共享独占 MySQL 8.4.13 数据库。请求通过真实 HTTP、JWT 签名验证和内部服务身份进入现有业务代码。没有使用 mock MVC、H2 或单 JVM 的两个服务对象替代多进程。记录见 [验收证据](multi-api-admission-verification-20261106.json)。

## 已验证行为

| 场景 | 实际结果 |
|---|---|
| 两实例同时接收 12 次同键提交 | 1 个 run、1 个 start outbox；其余 11 次返回原任务 |
| 同键修改输入／换调用主体 | 分别返回 409／404，无额外任务 |
| 已有 1 个排队任务，再竞争提交 12 个不同键，上限 6 | 5 个接入，7 个 429；数据库仅有 6 个 run/outbox |
| 队列已满时重试已接入的相同请求 | 返回原任务，不错误限流 |
| 取消一个排队任务 | 恰好恢复一个排队位置，取消任务不会开始执行 |
| 6 个任务跨两个 API 竞争 2 个执行槽位 | 只有 2 个领取成功，重复领取不创建新 attempt |
| 两任务完成，但资源尚未释放 | 两个 cleanup_pending attempt 仍占额，其他任务不可领取 |
| 可信账本边界释放一个 fixture attempt | 恰好一个新的领取成功，未释放总数保持 2 |
| 测试环境满额时另一租户／生产环境请求 | 独立接入与领取，原租户测试环境仍保持上限 |
| 强制终止一个 API、保持另一个服务，再重启原 API | 原任务可查询、相同键返回相同 run_id、未释放槽位不丢失 |

队列拒绝响应新增 `Retry-After: 5`、`Cache-Control: no-store`。客户端保留原幂等键和输入，等待至少 5 秒后重试，建议加入随机退避；该时间不是容量恢复保证。队列检查位于幂等查重之后，因此网络响应丢失后的重试不会因队列已满而失去原任务。

## 配额配置

配额保存于 `oa_tenant_environment`，以租户和 test/prod 环境为作用域。数据库事务按 tenant → environment → run → attempt 顺序加锁；增加 API 副本不会增加额度。

由有配额管理权限的用户调用 `GET /platform/tenants/{tenant}/environments/{test|prod}/quotas` 读取当前值与 `revision`，随后调用同一路径的 PUT。提交字段为 `maxConcurrentRuns`、`maxQueuedRuns`、`maxSandboxes`、`dailyTokenBudget`、`auditRetentionDays` 和读取到的 `revision`；保留不打算修改的字段。并发修改导致 revision 冲突时重新读取，不直接覆盖。变更与旧值／新值审计在同一事务提交。

- `maxQueuedRuns` 限制 queued 任务数，不包括已经运行的任务；0 暂停新任务接入，原幂等查询仍有效。
- `maxConcurrentRuns` 限制所有尚未 released 的 attempt，包含运行中、取消处理中和终态待回收；排队不占该额度。0 暂停新执行领取。
- 下调额度不强杀已有任务；已有占用可能高于新限额，但不能继续超额领取。重启和租约过期不会自动释放额度。
- 沙盒额度与执行额度分开管理。本次验证不代表 dailyTokenBudget 的完整计费约束，也不能据此推算生产吞吐。

## 复现与证据范围

正常 Linux 验收驱动 `python3 console/backend/tests/run_durable_checks.py` 先构建专用平台 JAR，再设置 `OA_RUN_MULTI_API_JAR` 运行事务与双进程测试；没有 JAR 时直接失败。后续投递故障集成要求额外配置 `OA_RUN_DISPATCH_PYTHON` 和 `TEMPORAL_TEST_BINARY`，见[投递验收](dispatch-recovery.md)。Docker 镜像、Java/Maven/Python 依赖与 Temporal CLI 需提前准备才能断网执行。CI 的 durable-run-database 作业使用这个驱动，新增 CI 配置不代表远端 CI 已运行通过。

已有受控临时数据库时，可以设置原有 `OA_RUN_TEST_JDBC_URL`／`OA_RUN_TEST_DB_USER`／`OA_RUN_TEST_DB_PASSWORD`，再令 `OA_RUN_MULTI_API_JAR` 指向以 `-Dplatform.main-class=com.iflytek.astron.platform.PlatformApplication` 打包的绝对路径，执行 `RunLedgerMySqlTest#independentApiProcessesShareAdmissionAndExecutionQuotas`。测试仅接受 loopback 数据库连接，自建并删除随机 schema；不要连接业务数据库。未设置 JAR 的普通单元测试会显式跳过该用例。

本次实际在 Windows 运行两个 JVM，经固定 SSH 主机验证的本地转发连接展示机独占临时 MySQL。JWKS 服务只绑定本地回环地址，签名材料和服务凭据随机生成；子进程不继承业务配置或凭据。测试结束关闭进程、JWKS、连接和临时数据库，成功后删除临时进程日志；失败保留本机临时诊断目录。首次外层驱动在打印 Maven 成功日志时发生 GBK 编码错误，随后直接核验 Surefire XML：20 项通过、0 失败、0 跳过；另行核验数据库容器和网络均为零残留。API 强杀时的 SSH 转发连接重置属于该进程断连，非数据库重启。

这不是完整业务执行或容量压测：没有模型、Temporal worker、文件上传、实际沙盒或负载均衡器；fixture 领取后没有启动执行实体，释放调用只验证账本准入，不冒充物理回收证明。生产 Kubernetes/Kata、多节点故障矩阵、公平性与容量验收仍单独推进。

## 展示部署

同一受测 JAR（SHA256 `5b7cb30e9232ed73ac54dcf384b19433c806c62e288148c12deb5dff797fe18b`）已发布到授权展示服务器，schema 保持 1.69。维护前停止 API 准入并复查空闲、保存私有 SQL 备份，维护 32.234 秒。两个受测 API 进程只是隔离验收夹具；线上仍为单 API 展示部署，没有以此宣称线上 HA。

部署后复查原任务 58 步、2 个文件 SHA256、20 页缓存用量、四个周/月周期及 21 项公网列表/详情用量对照；五个服务均 active，观测 outbox 待补发为 0。下载健康报告时首次使用了错误本地工具参数中的远端路径，服务检查本身通过，随后从实际报告目录取回并核验。此前离线镜像归档没有在本轮重建，后续离线交付需包含新制品；前端保持原版本。

本次无新增运行时依赖。临时签名使用既有官方 Nimbus JOSE/JWT，参考 [RSA JWT 示例](https://connect2id.com/products/nimbus-jose-jwt/examples/jwt-with-rsa-signature)；实际资源服务器使用 [Spring Security JWT](https://docs.spring.io/spring-security/reference/servlet/oauth2/resource-server/jwt.html)。当前工具无 Context7，使用官方文档核对。
