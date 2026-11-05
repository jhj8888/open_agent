# 集群执行监督服务

`ClusterSupervisor` 将已有集群账本、官方 Kubernetes 客户端和 Temporal 工作流接通；`cluster_service` 提供常驻入口、按环境出队、活动注册、周期补偿与就绪探测。此增量已在测试服务器的独立环境验证，没有开启展示机或 Helm 的集群任务准入。

## 执行与恢复

调用链：HTTP → SQL 任务/outbox → 环境调度器 → 官方 Temporal Worker → 集群 claim/begin/observed → Kubernetes Pod → 原受信 executor。正常执行、取消、失败、截止和心跳过期统一进入 reconcile；独立扫描器继续处理尚未完成的回收。

数据库拥有跨副本配额和一次性启动授权。活动执行不自动重试，启动回复丢失不创建第二个 Pod；未知创建仍占用配额。常驻服务重启和扩容仅扫描已经终态、取消、超时或心跳过期的任务，保留其他副本仍有有效租约的任务。副本内锁只是减少重复清理请求，不充当分布式锁。

回收按有界轮次推进：fencing → 独立读取/停止 Pod → 保存终止或未启动证据 → 移除自身 finalizer → 证实相同资源已不存在 → 再次读取 journal → 提交稳定证明并原子释放。Pod 删除和 finalizer 确认通常需要多轮；`cleanup_unconfirmed` 是此时的有限活动失败，Temporal 按既有策略重试清理，不重放模型或工具。

没有授权创建沙盒（journal 为空或仍是 reserved）时，fencing 可以阻止后续创建。已授权的 creating/created/cleanup_pending/released 记录及其 sandbox ID 必须经过独立沙盒核验；当前集群 Kata 适配尚未接入，因此保持占额。executor 自报删除、Pod 消失或进程停止均不能代替该核验。

当前新 attempt 沿用账本 30 秒首次心跳期限；调度或镜像拉取太慢会进入失败/清理，监督器不会擅自延长租约。生产安装须预置镜像并测试启动延迟，后续独立启动预算协议仍需评估。已绑定但缺少实际容器终止记录、未知创建、节点失联不通过计时自动释放。

## 服务配置

固定入口：`/opt/venv/bin/python -m agent.durable.cluster_service`。以 UID/GID 10001 运行，从 `/run/open-agent/supervisor/service.json` 的 Secret 投射读取配置。参考 [配置结构](../../deploy/platform/cluster/service.example.json) 中的占位值必须在安装时替换；实际 Secret 不入仓库。supervisor、dispatcher、worker 三个身份必须不同，且与控制 API 的集群目标配置完全一致。

Kubernetes 使用官方 in-cluster loader，保留 CA 校验、服务账号 token 刷新和不自动重试创建的行为。监督器需要的 RBAC 范围如下，已在[独立执行 chart](../../helm/open-agent-execution/README.md)接线并通过真实零节点 API 检查：

| 资源 | 权限 | 范围与用途 |
|---|---|---|
| Pods | get/create/patch/delete | 专用执行 namespace；创建、门禁、停止与 finalizer |
| Namespaces | get | 指定 `kube-system` 和执行 namespace；核对 UID |
| Nodes | get | 核对终止证据引用的节点 UID/Ready；不需要 list/watch 或修改节点 |
| Secrets、pods/exec、pods/log | 无 | 监督器不读取 worker/model 凭据，也不进入 executor 执行命令 |

executor 使用另一个无 token 的 ServiceAccount，不能复用监督器账号。监督器只挂载自己的服务凭据、控制 CA 和 Temporal 证书；模型密钥/worker 模板不进入该进程。Secret 符号链接须在固定挂载目录内，文件读取有长度及类型限制。

Temporal 非 literal loopback 地址强制 mTLS；固定 `/run/open-agent/temporal` 提供 `ca.pem`、`tls.crt`、`tls.key`。地址不接受 URL、嵌入凭据或端口越界。Kubernetes/control/Temporal 依赖、已注册 Temporal namespace 与调度身份验证通过后才写入 `/tmp/open-agent-cluster-ready.json`。探针命令为 `python -m agent.durable.cluster_service --check`，过期、异常 PID、链接或无效文件失败关闭；停止时先移除就绪记录，再等待 Worker 退出。外部依赖故障撤销就绪，不据此推断物理资源已消失。

API/Temporal/模型网络已由独立 NetworkPolicy 分别配置内网目标，executor 没有监督器的 Kubernetes API 路径。运行镜像、RBAC/NetworkPolicy/Helm 与原生 API TLS 接线已交付；实际 CNI/节点、Kata admission/回收仍未验收，控制 chart 保持任务准入关闭。镜像拉取 Secret 引用显式写入 Pod 绑定摘要，不依赖 ServiceAccount 自动注入。

## 证据与限制

Windows 首轮新增测试暴露五处回收用例错误地假设单轮完成，以及凭据测试标记与合成镜像摘要重合；修正测试准备，并修复证书挂载路径在 Windows 上的校验表示。100 项相关检查通过、3 项 Linux 专属跳过。随后补充 dispatcher 身份就绪门禁，最终新增模块定向检查 23 通过、4 项 Linux 专属跳过；两轮有重叠，不相加。

最终 Linux 离线 overlay 覆盖官方 SDK、监督活动、配置、Secret 投射、真实 Temporal 服务及常驻服务接线，包含调度身份拒绝时不宣布就绪。另用独立真实 K3s 1.36.6（零节点）与 Temporal 验证任务创建、第二副本观察、取消、未启动证据、finalizer 和释放；其账本使用显式合成夹具，持久数据库/HTTP 契约沿用独立 MySQL/Java 检查。不能将两组证据拼接成完整 K8S 业务联调或物理执行验收。

准确计数、制品摘要、API 实验、清理和展示入口复查见 [监督服务验证记录](cluster-supervisor-verification-20261105.json)。参考接口：[官方 Temporal Worker](https://python.temporal.io/temporalio.worker.Worker.html)、[官方 Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)。Context7 工具在此会话未提供，使用上述官方文档与冻结 SDK 源码核对接口。

最终 Linux 验证为 105 项通过/0 跳过，含实际不存在 namespace 的启动拒绝。真实 K3s 实验对应前一归档；最终归档只修改常驻服务 namespace 就绪检查及对应测试，活动、生命周期、SDK 适配及账本夹具文件摘要全部保持一致，两个归档和关联核对保留在证据中。
