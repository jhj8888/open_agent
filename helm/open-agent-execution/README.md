# 集群执行部署包

本 chart 将已实现的 Temporal 监督服务、官方 Kubernetes SDK 和受信 executor 接入专用 namespace。模型仍由现有 HTTP API 提供。镜像与 test/prod 的 RBAC、准入策略已有真实 API 验证；**集群新任务准入仍关闭**，Kata 沙盒准入/独立回收、真实节点/CNI、完整离线冷启动及容量验收尚未完成。展示站继续使用已验收的单机执行链。

调用关系：控制 API 的持久 outbox → 同环境 Temporal 调度/监督服务 → 带调度门禁的 executor Pod → 控制 API 单次许可 → 模型 API。监督器保存 Pod UID、终止证据并解除 finalizer 后，账本才能释放配额。受信 executor 不是租户代码沙盒；当前集群入口会拒绝配置单机沙盒 socket，不能把租户脚本放到 executor 或 supervisor 中执行。

## 版本和交付输入

- Kubernetes 至少 1.32，启用 `ValidatingAdmissionPolicy` 和执行 NetworkPolicy 的 CNI。真实验证使用 K3s 1.36.6 零节点 API；它不证明 1.32、kubelet、CNI 或 Kata 已验收。
- Helm 4.3.1；自包含 JSON Schema，没有在线 chart 依赖。默认配置及示例未填镜像时拒绝渲染。
- 使用[已核验的运行镜像](../../deploy/platform/worker_image/README.md)，导入内网仓库后登记实际 registry manifest digest。本地 image/config ID、归档 SHA256 都不能当作可拉取 digest。
- 控制镜像必须包含当前集群账本和环境路由（至少 V1.64）；早期控制镜像记录不能直接视为已包含这些增量。API 原生 TLS 由 Spring Boot 配置启用。

## 安装前准备

每个环境单独预置两个 namespace：`open-agent-test` / `open-agent-executors-test`，生产对应 `prod`。执行 namespace 必须专用，开启 Pod Security `restricted`；不向普通租户授予 Kubernetes API 凭据或管理这些 namespace 的权限。chart 不创建或删除 namespace。

先读取真实 `kube-system` UID 与执行 namespace UID，分别写入 `service.json` 的 `target.clusterId`、`target.namespaceUid`。这两个身份必须同时登记到控制 API 的 `PLATFORM_CLUSTER_RECONCILERS`。重建 namespace 后不得继续沿用旧 UID。配置占位 UUID/密钥/域名不得用于安装。

| 对象 | 所在 namespace | 内容 |
|---|---|---|
| `open-agent-{env}-supervisor` Secret | 控制 | `service.json`，仅 supervisor/dispatcher 服务身份及固定目标 |
| `open-agent-{env}-temporal` Secret | 控制 | `ca.pem`、`tls.crt`、`tls.key`，Temporal 客户端 mTLS |
| `open-agent-{env}-worker` Secret | 执行 | `worker.json`，独立 worker 服务身份、模型网络允许列表；不含 supervisor/dispatcher 密钥 |
| `open-agent-{env}-ca` ConfigMap | 两个 namespace 各一份 | 公共 `ca.pem`；控制面还需证书型 `truststore.p12`，密码 `changeit`，不得包含私钥 |
| `open-agent-{env}-registry` Secret | 按需在两个 namespace 分别创建 | 内网仓库只读凭据；节点也必须信任仓库 CA |
| `open-agent-{env}-api-tls` Secret | 控制 | `tls.p12` 与 `tls.password`，API 服务端证书及私钥 |

真实配置和密钥从受控本地文件或企业 Secret 管理流程写入，不通过 Helm values、命令行明文参数、Git 或构建上下文传递。`service.example.json` 仅描述结构。

`service.json` 的 `program` 必须逐项匹配 values：`worker_secret` → `executor.workerSecret`，`service_account` → `executor.serviceAccount`，`node_pool` → `executor.nodePool`，`ca_config_map` → `executor.caConfigMap`，`image_pull_secrets` → `executor.imagePullSecrets`（含顺序）。`target.executorImage` 必须等于 chart 的 `image.repository@image.digest`。镜像拉取引用进入 Pod 绑定摘要；ServiceAccount 不得隐式注入另一组拉取 Secret。

`worker.json` 使用 `agent.durable.child.ExecutorSettings` 结构，但不包含 `lease_id`（每次由不可变租约注入）；`worker_id` 必须匹配 `target.workerId`。`control_endpoint` 与 supervisor 使用相同 HTTPS API，`control_ca_file` 为 `/run/open-agent/certs/ca.pem`。按实际内网模型填写 `model_origins` / `model_networks`，不开放公网模型来源或全部出口。集群沙盒接通前 `sandbox` 为空。

控制 Secret 的 `PLATFORM_SERVICE_KEYS` 分别登记 worker、dispatcher、reconciler 角色且限定相同环境；`PLATFORM_CLUSTER_RECONCILERS` 将 supervisor 身份映射到同一固定 cluster target，单机 `PLATFORM_RECONCILER_WORKERS` 不得同时绑定该 worker。Temporal namespace 需预先注册，证书授权、地址、namespace、task_queue 三者与服务配置一致，test/prod 分别配置。

V1.66 增加沙盒目标与 CR/Pod UID 的[持久绑定接口](../../docs/platform/cluster-sandbox-binding.md)。监督服务的可选 `sandbox_target` 对应 API 的 `PLATFORM_CLUSTER_SANDBOXES`，不能复用 executor namespace。该配置只用于严格识别目标，不会自动开启 Skill；独立核验/停止证据服务接通前保持缺省。升级集群 API 后需同步使用当前 worker 镜像，旧严格响应模型不识别新增字段。

为受信 CPU 节点设置 `open-agent.io/control-pool: trusted` 与 `open-agent.io/executor-pool: trusted`（可审核修改 pool 值）；不使用 GPU 资源。镜像需预加载或验证拉取耗时：当前账本初次心跳期限仍为 30 秒，拉取/调度超时会进入失败回收。生产 supervisor 至少两个副本且启用 PDB，但副本数不是 HA 已验收的证明。

## API TLS 和双向网络放行

控制 chart 需设置以下值；示例 release 为 `oa-test`，若改名必须同步 API DNS 和证书 SAN。

```yaml
apiTls:
  enabled: true
  existingSecret: open-agent-test-api-tls
trustBundle:
  configMapName: open-agent-test-ca
network:
  apiClients:
    - namespace: open-agent-test
      podLabels:
        app.kubernetes.io/component: cluster-supervisor
        open-agent.io/environment: test
    - namespace: open-agent-executors-test
      podLabels:
        app.kubernetes.io/name: open-agent-executor
        open-agent.io/environment: test
```

证书 SAN 覆盖 `oa-test-platform-api.open-agent-test.svc`，端口 18082；`service.json` 使用 `https://oa-test-platform-api.open-agent-test.svc:18082`。Caddy 通过同一 DNS、公共 CA 验证 API 证书，探针切换 HTTPS；没有关闭证书校验的开关。Java 8 项启动/TLS 测试包含真实 HTTPS、未信任证书拒绝、明文请求拒绝和鉴权边界；企业证书、Caddy 和目标 CNI 的完整联调仍需在目标集群验收。

执行 chart 的 `network.control` 与上述 API 标签/端口一致；`network.temporal` 仅给 supervisor，`network.models` 仅给 executor。`network.kubernetes` 仅列审核后的私有 API Service/后端 `/32` 地址和明确端口，仅 supervisor 可访问。DNS 默认匹配 kube-system/CoreDNS；NodeLocal DNS 或服务 NAT 行为需按实际 CNI 验证。依赖端也必须允许对应入站，不能只配置本 chart 的出站。

supervisor 只可 get/create/patch/delete 执行 namespace 的 Pods、get 两个固定 namespace、get 节点身份；不能 list/watch、读 Secret、pods/exec、pods/log 或修改节点。仅它挂载一小时的投射服务账号 token；executor 无 API token、无 RBAC。准入策略强制固定镜像/命令、单容器、非 root、只读根目录、固定资源/卷/拉取凭据及初始门禁。API 策略不能替代宿主隔离或独立回收证明。

## 渲染、变更与退出

审核 `examples/test.yaml` / `prod.yaml` 后保存为环境 values，替换镜像、网络和配置版本；每次 Secret/CA 轮换也更新 `configurationRevision` 触发 supervisor 滚动更新。以下命令使用已审核目标集群凭据执行，`--dry-run=server` 成功后仍需核对 VAP `status.typeChecking.expressionWarnings` 和目标 CNI 行为。

```sh
helm lint helm/open-agent-execution --strict --namespace open-agent-test -f reviewed-execution-test.yaml
helm template oa-test helm/open-agent-execution --namespace open-agent-test -f reviewed-execution-test.yaml > execution-test.yaml
kubectl apply --dry-run=server -f execution-test.yaml
helm upgrade --install oa-test helm/open-agent-execution --namespace open-agent-test -f reviewed-execution-test.yaml --wait --timeout 10m
```

不得绕过控制 chart 的 `executionAdmission=false`。准入、真实节点、Kata/CNI、未知创建恢复的剩余验收在 OpenSpec 中跟踪。未来运行任务后的升级/卸载，须先停止新任务、排空并核对全部执行租约和沙盒回收证明；不能通过删 namespace、强删 Pod/finalizer 或直接释放数据库配额来“清理”。

验证脚本 `deploy/platform/cluster/verify_execution_chart.py` **仅用于带所有权标记的专属零节点 API 夹具**；它创建 test/prod namespace、角色及负例并清理，不能指向业务集群。证据见 [集群部署验证](../../docs/platform/execution-chart-verification-20261105.json)。官方依据：[RBAC](https://kubernetes.io/docs/concepts/security/rbac-good-practices/)、[网络策略](https://kubernetes.io/docs/concepts/services-networking/network-policies/)、[服务账号投射](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/)、[声明式准入](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)。
