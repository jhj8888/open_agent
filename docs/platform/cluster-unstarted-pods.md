# 未启动 Pod 的回收证据

本增量处理任务取消时 executor 尚未绑定节点的情况。调用链为 Temporal 回收 activity → `ClusterLifecycle.stop_once` → 官方 Kubernetes SDK → 控制面 `/internal/platform/reconcile/cluster/attempts/{leaseId}/not-started` → MySQL V1.63 → finalizer 移除 → 独立沙盒核验 → 既有 `RunVerifiedRelease` 事务。完整集群 activity/服务尚未接通；展示机继续使用已验收的 systemd/gVisor 路径。

监督器先 fencing，核对相同 Pod UID、固定配置、空 `nodeName`，且没有 container/init/ephemeral 状态、提名节点或运行状态。使用 UID/resourceVersion 条件请求删除，再读取该 Pod，确认 `deletionTimestamp` 已设置并且仍未绑定节点。平台调度门禁可以尚在，也可以已经移除。

这组证据依赖 Kubernetes 的两个约束：已绑定的 `nodeName` 不能被清空；删除中的 Pod 不能再绑定节点。先持久保存独立 `not_started` 记录，再移除自身 finalizer。不能从 Pending、404、空列表、超时或删除成功单独推断从未执行。真实 scheduler 可能在读取与删除之间更新 resourceVersion，此时停止操作失败并保留资源；下一次有界回收重新读取，不能盲目重放旧版本请求。

未启动与实际容器终止证据互斥。已授权创建 Skill 沙盒的尝试不能使用未启动证明；只有空沙盒记录或 `reserved` 可接受。释放仍需匹配集群、namespace/Pod UID、证明 ID、沙盒状态和回收摘要，并与配额和审计原子提交。兼容字段 `terminationId` 在 `never_started_pod` 证明中引用 `notStarted.id`。重复提交相同证据可恢复，替换证据被拒绝。

V1.63 增加可空列、唯一索引及状态约束，保留既有 systemd 记录和已完成任务。生产集群的未知创建、已绑定节点但没有容器终止记录、节点失联等情况继续保留配额。不得为消除占额而强制删除生产 Pod。

## 真实 API 验证范围

测试使用固定摘要的 K3s v1.36.6+k3s1、官方 Python SDK 36.0.3。K3s 采用 `--disable-agent`，没有 kubelet、CRI、CNI 或节点；容器不使用特权或 host network，删除全部 Linux capabilities，内存上限 768 MiB、CPU 上限 1，位于独立内部 Docker 网络且没有公网端口。它验证真实 API 默认值、调度门禁、资源版本冲突、节点绑定约束、删除屏障和 finalizer 顺序，不能证明实际进程停止或 Kata 隔离。

工具位于 `deploy/platform/cluster`：

- `provision_api_fixture.py` 校验预下载二进制摘要和资源归属，创建独立测试 API；所需固定容器基础镜像须提前缓存。
- `verify_api_fixture.py` 在两个新 namespace 中分别验证有门禁和已解除门禁的路径；HTTP 账本在此使用明确标注的私有文件替身，MySQL 持久性单独验证。
- `verify_binding_barrier.py` 验证清空已分配节点返回 422、删除后绑定返回 409。其强制清理只用于此零节点、无执行能力的可丢弃测试集群。
- `cleanup_api_fixture.py` 根据完整容器 ID、资源标签和私有归属文件删除自身容器、数据卷、网络及 admin 凭据，保留非敏感验证结果。

所有脚本在专用测试机的 `/opt/open-agent/verification` 运行，元数据、K3s 二进制及契约 manifest 放在同一目录；先运行 `verify_contract.py` 安装对应摘要的独立 SDK overlay。测试 kubeconfig 仅保存在 `/opt/open-agent/k3s-api-fixture/admin.yaml`，不得提交或公开。此工具不是用户生产集群安装器，也不是完整离线交付包。

官方依据：[K3s agentless servers](https://docs.k3s.io/advanced#running-agentless-servers-experimental)、[固定版本发布](https://github.com/k3s-io/k3s/releases/tag/v1.36.6%2Bk3s1)、[Pod Scheduling Readiness](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-scheduling-readiness/)、[Finalizers](https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/)。许可为 Apache-2.0，`LICENSE-k3s` 与固定摘要清单随验证工具保留。

## 交付验证

43 项 Java 检查（含 15 项真实隔离 MySQL）通过，数据库 SIGKILL/重启后未启动证据仍可读取并幂等释放。Windows 77 项通过/1 项 Linux 专属跳过，Linux 78 项通过/0 跳过。真实 API 两条回收路径与绑定约束通过；原 fixture 和可复用部署工具复验创建的 fixture 均已删除，admin 凭据已清理。

展示 API 已应用 V1.63，JAR SHA256 为 `9150b8159361c30a90f4995e50918dc89adc53b4d883519dc841c225a997a354`。升级前保存私有 SQL 备份，维护窗口 45.802 秒。真实 Skill 任务 `acf53093-eb1c-4413-8b06-a0114dd27bfa` 成功，47.298 秒、200 条事件，脚本输出、Temporal 完成及执行/沙盒回收通过。首次验收脚本使用了缺少 Temporal SDK 的系统 Python；改用 worker venv 补查同一个任务，重用原幂等键，没有再执行模型或工具。

原逐步观测演示任务仍保留 58 个步骤、6 次模型调用、4 次工具调用、2 个含实际 think 的步骤和 2 个文件。输入增量/上下文变化、工具结果、输出、Token 合计、跨租户拒绝及文件下载摘要均复查通过；公网工作台 TLS、静态资源摘要与 20 项列表/详情 usage 一致性通过。详细证据及初次失败修正记录见[验证清单](cluster-unstarted-verification-20261105.json)。
