# Kubernetes 沙盒资源绑定

V1.66 为生产 Skill 沙盒增加持久资源身份。目标、BatchSandbox CR 和不可信 Pod 与受信 executor Pod 分开记录。此增量提供账本与严格传输接口；独立 Kubernetes 核验服务、停止证据和完整准入尚未接通，集群 Skill 入口继续关闭。

## 调用与权限

调用链：独立沙盒核验服务 → `ClusterSupervisorClient` → `PlatformClusterReconcileController` → `RunClusterSupervision` → `RunSandboxJournal` / `RunLedger` 的同一事务。只接受绑定的 reconciler 身份；普通用户、worker、dispatcher 和 observer 都不能调用下列接口。模型、文件、Redis、Kafka、单机执行接口不变。

| 内部 POST 路径（共同前缀 `/internal/platform/reconcile/cluster/attempts/{leaseId}`） | 输入 | 行为 |
|---|---|---|
| `/sandbox-prepare` | `image` | 在有效许可锁内核对已发布 Skill 镜像、预留沙盒配额，并保存操作者固定的目标 |
| `/sandbox-resource` | `sandboxId`、`workloadUid`、`workloadResourceVersion`、`podUid`、`podResourceVersion`、`evidenceSha256` | 原子保存首次独立资源观察与 journal sandboxId |
| `/inspect` | 无 | 返回包含 `sandboxTarget` / `sandboxResource` 的完整快照，供失联与重启恢复 |

准备仅在 executor UID 已登记、运行许可仍有效时允许；相同准备在尚未创建时可幂等重读。原一次性 `creating` 授权不变，创建确认不明确时不重复创建。资源观察允许在取消或终态后登记，但不会改变任务状态、恢复许可或释放资源。CR／Pod UID 和首次 resourceVersion 作为不可变观察保留；更新运行状态需使用后续独立证据，不能覆盖首次身份。

数据库对同一集群的沙盒 CR UID、Pod UID 各设唯一约束；同一对象不能登记给两个任务。JSON 观察内的 UID 必须与索引列一致。唯一约束或后续写入失败时，journal sandboxId 同事务回滚。executor UID、CR UID、沙盒 Pod UID 必须各不相同。Kubernetes `resourceVersion` 作为不透明字符串保存，不作数值大小比较。

记录了新沙盒目标后，旧的 `/sandbox-observed` 仅提交 sandboxId 的接口不再适用。若已经创建或处于未知创建阶段，旧的最终释放证明不能释放资源。只有未授权创建的 `reserved` 记录仍可随已验证的 executor 终止正常释放。这项限制会在后续接通独立沙盒停止和创建结束证据后按明确协议扩展。

## 操作者配置

控制 API 的 `PLATFORM_CLUSTER_SANDBOXES` 为 supervisor ID 到目标的映射；缺省 `{}`，不会启用沙盒。目标形状为：

```json
{
  "namespace": "open-agent-sandboxes-test",
  "namespaceUid": "<从目标集群读取的真实 namespace UID>",
  "runtimeClass": "kata-qemu"
}
```

该 supervisor 必须已在 `PLATFORM_CLUSTER_RECONCILERS` 注册。沙盒 namespace 的名称、UID 不得与同集群任一受信 executor namespace 相同；test/prod 不能共用同一沙盒 namespace 名称或 UID。配置只有名称并不证明实际 RuntimeClass 使用 Kata；物理核验仍由独立服务完成。

监督服务 `service.json` 的可选 `sandbox_target` 必须与上述目标完全一致。Python 客户端拒绝未配置或配置漂移的目标快照，包括 namespace 重建、运行时更换和资源替换。已有记录的配置不能直接更换；应先完成旧记录的独立清理，保留其身份和证据。

升级顺序：先保存数据库备份、关闭准入并升级控制 API 到 V1.66，再交付包含本协议的 worker 镜像。严格旧客户端不识别新增响应字段，不应继续连接升级后的集群接口。当前展示使用的 systemd/gVisor 路径不受影响。新增表字段和索引保留原数据，回滚应用时不要删除已经记录的绑定证据。

## 验证与未完成项

验证记录见 `cluster-sandbox-binding-verification-20261105.json`，分别列出真实 MySQL/重启、HTTP 权限、Python 协议、离线镜像和展示机检查。合成的 CR／Pod UID 只用于验证账本，不是实际 Kubernetes 身份验收。实际 CR／Pod 标签、ownerReference、finalizer、镜像、安全上下文和网络必须在独立核验服务中确认后才能调用资源登记接口。

待完成：HTTPS 沙盒核验服务与 executor 的接线、控制器停止创建的可靠屏障、kubelet 容器终止证据、独立回收及未知创建处理、真实 Kata/CNI 节点故障测试和离线冷启动。登记 UID 或迁移成功都不能替代这些验收。
