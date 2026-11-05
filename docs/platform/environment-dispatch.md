# 按环境分派任务

2026-11-05 展示 API V1.64 已部署。任务提交时只接收实际配置了执行队列的环境；outbox 从数据库领取阶段区分 test/prod，避免一个环境的消息被另一个环境提前领取。展示机的生产模型 HTTP 接口继续可用。

## 调用链与边界

HTTP/聊天 → AgentRunService 按环境准入 → RunLedger 与 outbox 同事务 → RunOutbox 按环境领取 → Python ControlPlaneClient 校验整个批次 → OutboxDispatcher → 对应 Temporal task queue → ScopedActivities → 共用 HostSupervisor → 原执行器、模型 API 和隔离 Skill。取消消息采用同样的环境过滤。

V1.64 只新增两项环境前缀索引，不修改已有记录。SQL 在 `FOR UPDATE SKIP LOCKED` 前过滤环境，包括待投递和租约过期消息。旧 claim 请求缺少 environment 时仅领取 test；空字符串、未知环境拒绝。客户端遇到越界环境、重复 ID、超量或无效批次时拒绝整个响应，dispatcher 和 activity 再次检查环境，不启动、取消或确认错误环境的任务。

`PLATFORM_RUN_ADMISSION_ENABLED` 继续作为总开关；新增 `PLATFORM_RUN_ADMISSION_ENVIRONMENTS`，缺省为 `test`，可配置 `prod`、`test,prod` 或 `prod,test`。无执行路由或配置无效时返回 `executor_unavailable`，不留下永远排队的 run/outbox；部署元数据的 `admission_enabled` 使用同一判断。Helm 从当前环境生成此字段，但集群总准入继续关闭。

展示服务使用两个官方 Temporal Worker，上层共用同一个 HostSupervisor 的锁、最大并发 2 和恢复扫描：

| 环境 | task queue | 实际验证 |
|---|---|---|
| test | `open-agent-demo-v1` | DeepAgents + 模型 + gVisor Skill 脚本 |
| prod | `open-agent-demo-prod-v1` | 已推广的模型 HTTP 接口 |

这是同一主机、同一 Temporal namespace 的逻辑路由分离。生产不可信 Skill 仍需 Kata，不能据此声称 K8S/Kata 物理隔离或 HA 已验收。生产集群仍按独立数据库、网络 namespace、服务身份及集群执行协议推进。

## 验证

- Java 两轮定向检查共 43 项通过：真实隔离 MySQL 的并发领取、过期投递、取消消息、默认环境与关闭准入无残留；HTTP 权限/参数及 Spring 启动检查。两轮有独立结果，未将其描述为完整 Java 全量测试。
- Linux 冻结运行环境 63 项通过，0 失败/跳过；包括真实 Temporal 双队列执行、只取消目标任务、消息重投无重复执行、活动环境拒绝、心跳丢失、服务重启和监督回收。Windows 初轮 47 项、补充定向 19 项分别通过，两轮有重叠，不相加。
- Helm/交付 29 项通过。没有新增依赖或更新活动 worker 的 Python 包版本；完整源归档 229 个文件，只覆盖六个经过验证的源/测试文件。
- 展示维护 80.14 秒，先私有 SQL/配置备份，关闭准入启动新 API，再确认两个 Worker 就绪后开放 test/prod。真实测试 Skill `47101b03` 与生产模型 `79d1c39f` 均成功，SDK 查询确认分别使用对应队列，工作流时间重叠 22.667 秒；最终无活动任务或未释放 attempt。
- 原逐步观测任务仍有 58 步、6 次模型、4 次工具、2 步实际 think 和 2 个文件；Token 汇总、下载摘要、跨租户拒绝保持通过。公网 TLS、三份静态资产、认证拒绝和 21 个任务的列表/详情用量一致性通过。

首轮 Java 用例的泛型推断和生产调用身份测试准备已修正后复验。发布准备脚本最初使用了不支持 `write_text(newline=...)` 的 Python，改用项目解释器成功；真实产物摘要重新核对。公网复查首次因验证工具并行上传相同目录失败，在应用检查执行前停止，顺序重传后通过。未重复执行模型/工具来掩盖这些验证工具问题。

机器证据见 [environment-dispatch-verification-20261105.json](environment-dispatch-verification-20261105.json)，源归档与操作流程见 [worker 发布说明](../../deploy/platform/worker/README.md)。更广的集群执行、未知创建恢复、Kata/CNI、完整离线冷启动与容量验收仍未完成。
