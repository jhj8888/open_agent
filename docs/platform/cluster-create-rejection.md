# 集群创建被明确拒绝后的恢复

Kubernetes 创建 executor Pod 若返回完整的核心 `v1/Status` 拒绝响应，平台保存与本次租约绑定的拒绝凭据，结束任务，再由回收器独立核对 Pod 和 Skill 沙盒均不存在，释放占用。配额不会仅因 HTTP 错误或一次 404 而释放。

调用链为 `ClusterSupervisor → ClusterLifecycle → KubernetesProcesses → 官方 Kubernetes SDK`；明确拒绝经专用 reconciler HTTP 接口进入 `RunClusterSupervision.creationRejected`。同一数据库事务调用 `RunLedger.finish` 撤销执行许可，并记录 V1.65 新增的不可替换凭据。正常 worker 没有该接口权限；没有改变共享认证、模型、存储、Redis 或 Kafka 接口。

## 接受的证据

仅在本次唯一一次 `create_namespaced_pod` 调用中接受以下对应关系，读取/删除操作的错误不进入此路径：

| HTTP 状态 | 核心 Status reason | 用途 |
| --- | --- | --- |
| 401 | Unauthorized | 创建请求认证被拒绝 |
| 403 | Forbidden | 创建请求授权或准入被拒绝 |
| 422 | Invalid | Pod 配置校验拒绝 |

同时要求完整 `kind=Status`、`apiVersion=v1`、`status=Failure`、匹配的整数 code、正确的 Pod 名（响应提供时）和规范 UUID 格式的 `Audit-ID`。保存的凭据只有租约绑定 Pod 名、namespace UID、审计 ID、状态/reason、请求和响应 SHA256；不保存可能含敏感内容的原始拒绝文本。失败分类复用已有 `configuration_unavailable`，不扩展普通 worker 的错误上报权限。

409、429、5xx、超时、重定向、格式错误和缺少 Audit-ID 的响应继续保留为创建结果未知。SDK 不自动重试创建。拒绝凭据落库前丢失，不能从 Pod 404 推断创建结束；凭据事务已提交但 HTTP 回复丢失，可由新回收进程读取持久记录恢复。相同凭据可幂等重报，不同凭据不能覆盖。

`rejected` 必须对应无 Pod UID、无终止/未启动凭据、终态任务和待清理租约。只有 `reserved` 或未预留的 Skill 沙盒允许此路径；已经授权创建沙盒时拒绝入账。最终释放依然使用现有配额、Skill 状态和审计原子事务，且释放后的 Skill journal 为 `released`。取消先到时保留取消终态。

依据官方 [Admission Controllers](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/) 的持久化前拒绝语义，以及固定版本 [Audit-ID 初始化实现](https://github.com/kubernetes/apiserver/blob/v0.36.6/pkg/endpoints/filters/audit_init.go)。没有把 API 代理的任意 HTTP 错误当作核心拒绝，也没有宣称审计 ID 能代替实际执行节点的停止证据。

## 验证和部署边界

可重复的证据见 [本轮验证记录](cluster-rejection-verification-20261105.json)：

- 46 项 Java 检查通过，其中 19 项真实隔离 MySQL 检查，包含凭据与失败状态原子回滚、取消/沙盒边界、幂等释放及数据库 SIGKILL/重启后恢复。
- 本机 Python 定向检查 116 项通过、1 项 Linux 专属跳过；Linux 契约 overlay 138 项通过、零跳过。两轮覆盖范围重叠，不能相加为独立用例数。
- 独立零节点 K3s v1.36.6+k3s1 返回真实 401/403/422；403 场景走过完整监督/回收逻辑，确认一次启动授权和一次释放凭据。账本为显式合成夹具，不是业务 MySQL 与 K8S 的共同联调。没有模型或工具调用。
- 临时 K3s 容器、网络、卷和 admin 凭据已删除。离线镜像及展示 API 的最终摘要和复查结果记录在同一证据文件中。

`deploy/platform/cluster/verify_create_rejection.py` 依赖当前契约 overlay 和独立 API fixture。按现有 `provision_api_fixture.py → verify_create_rejection.py → cleanup_api_fixture.py` 顺序执行，验证脚本还需同目录的 `verify_execution_chart.py` 和 `cluster-contract-manifest.json`。即使中间失败也应检查并清理带 owner 标记的 fixture。

V1.65 与新增快照字段要求控制面和集群 worker 使用匹配版本；未来已有集群任务时必须先排空并升级相应 worker，不能滚动混用旧的严格反序列化客户端。当前展示机集群身份仍为空，活动任务仍走既有 systemd/gVisor 链路。

本增量不解决丢失请求后的未知创建、节点失联或已绑定但缺少实际终止记录。Kata/CNI 隔离、真实业务数据库/Temporal/K8S/模型共同联调、生产 HA、离线空节点冷启动和容量验收仍未完成，集群总准入保持关闭。
