# Open Agent 专用部署包

本 chart 交付当前平台的控制 API 和工作台，复用已实现的租户/Skill/Agent/任务查询/观测/文件接口。它不启用旧 `helm/astron-agent` 的整套服务，不部署模型网关、推理服务或 GPU 资源。

**当前边界：集群执行监督器尚未实现，chart 强制关闭新任务准入。** 单机展示机的 systemd/OpenSandbox/gVisor 执行链仍正常运行；不能将它直接放入一个 Pod 并称为生产 K8S 执行器。此 chart 是集群交付的控制面增量，Kata/CNI、执行器、完整断网与 HA/容量验收继续在 OpenSpec 跟踪。

## 已验证内容

Helm 4.3.1 对 test/prod 的 lint/template 和错误配置拒绝通过；合计 29 项部署、镜像引用及构建输入检查。使用官方固定摘要的 Helm CLI，JSON Schema 仅引用文件内定义，不依赖在线 schema 服务。模板采用稳定的 Kubernetes API，声明最低 1.30；渲染测试使用 1.35 版本参数，没有连接真实 Kubernetes API server，因此不代表任一集群版本已实测。

Linux amd64 API/工作台镜像已在展示服务器离线构建、导出并通过 Docker load 验证。在私有网络的新数据恢复副本上，两库内容比对、真实 OIDC 登录代理、3 个静态资源摘要、12 个产出文件下载与 197 个步骤读取通过；跨租户拒绝、内部路由屏蔽、伪造身份头拒绝和禁止新任务写入均验证。HTTPS 身份上游的 Caddy 配置/CA 加载通过 `caddy validate`，尚未以企业证书完成真实 TLS 联调。证据见 [交付验证记录](../../docs/platform/platform-delivery-verification-20261105.json)。

## 必需依赖与隔离

每个环境有独立 namespace：`open-agent-test`、`open-agent-prod`，以及不同的既有 Secret：`open-agent-test-control`、`open-agent-prod-control`。chart 不创建 Secret、不生成默认密码、不使用 `lookup` 读取 Secret、不授予 Kubernetes RBAC 或挂载服务账号 token。现有企业网关终止 HTTPS，只访问 web ClusterIP Service 的 8080 端口；API 18082 不直接暴露为 NodePort/LoadBalancer/Ingress。

| 依赖 | 必须满足 |
|---|---|
| MySQL | 当前上游迁移使用 `astron_console`；两环境必须使用独立实例及各自账号，不要用两个 schema 共享一个实例。先备份再部署，Flyway 保留既有迁移校验和 |
| S3 | 分环境桶/账号，私有访问，持久保存 Skill 包及任务文件；不能用临时 Pod 文件系统替代 |
| OIDC | 当前工作台使用同源 `/identity`、公开客户端 `open-agent-console`、PKCE S256；issuer 为 `https://publicHost/identity`，注册对应 `/open-agent/callback`，适配已验证的 Casdoor 配置 |
| 内网镜像仓库 | 导入已校验镜像后记录真实 registry manifest digest；配置节点对仓库 CA 的信任及 namespace 内 imagePullSecret |
| CNI / DNS | 必须执行 NetworkPolicy；默认 CoreDNS 的 namespace+pod 选择器。NodeLocal DNS、hostNetwork、不同 CNI 的实际语义必须在目标集群验证后适配，不能通过开放全部出口解决 |
| 模型 / Prometheus | 模型仅为既有 HTTP API；显式设置 allowlist 和精确依赖出口。未配置 Prometheus 时主机指标显示不可用，不应显示为 0 |

Secret 必须包含下列键。数据库/桶/模型加密密钥在两环境独立保管，Secret 引用名不同不能单独证明实际账号隔离；需要在目标服务完成授权核验。

```text
PLATFORM_JDBC_URL              # 专属实例 /astron_console，UTC，企业 TLS 配置
PLATFORM_DB_USER
PLATFORM_DB_PASSWORD
PLATFORM_S3_ENDPOINT
PLATFORM_S3_BUCKET
PLATFORM_S3_ACCESS_KEY
PLATFORM_S3_SECRET_KEY
MODEL_CREDENTIAL_ACTIVE_KEY
MODEL_CREDENTIAL_KEYS         # 与既有数据库密文匹配的密钥版本映射
PLATFORM_SERVICE_KEYS          # 当前可为空 JSON 对象；按独立内部角色配置
PLATFORM_RECONCILER_WORKERS    # 当前可为空 JSON 对象，不能拿管理 JWT 代替服务身份
```

企业 CA 可通过只含公共证书的 ConfigMap 提供 `ca.pem` 与 `truststore.p12`（证书型 truststore，约定密码 `changeit`，不包含私钥）。Java/S3/OIDC 与模型自定义 CA 使用该挂载；HTTPS 身份上游必须配置它。私有 registry CA 是节点侧依赖，不由该 ConfigMap 解决。Secret/CA 内容变化后滚动重启；建议使用带版本的 CA ConfigMap 名称触发明确的变更。

## 渲染与安装流程

`examples/test.yaml`、`examples/prod.yaml` 是待审核示例，`.invalid` 主机名与选择器必须替换；未提供镜像摘要时默认校验失败。prod 示例为 2 个 API 副本、2 个 web 副本、PDB 及可选 CPU HPA；资源值是起点，不是吞吐承诺，不据此推算模型/GPU 容量。

先按[镜像交付说明](../../deploy/platform/images/README.md)导入镜像，生成 `registry-images.json`，再为环境预置 namespace（建议启用 Pod Security `restricted`）、独立数据/身份服务、Secret 和企业网关路由。以下命令只在目标集群凭据已审核的安装机执行：

```sh
helm lint helm/open-agent-platform --strict --namespace open-agent-test \
  -f reviewed-test.yaml -f registry-images.json
helm template oa-test helm/open-agent-platform --namespace open-agent-test \
  -f reviewed-test.yaml -f registry-images.json > rendered-test.yaml
helm upgrade --install oa-test helm/open-agent-platform --namespace open-agent-test \
  -f reviewed-test.yaml -f registry-images.json --wait --timeout 10m
```

生产使用另一个 release、namespace 和审核过的 `reviewed-prod.yaml`。安装前应做目标 API server 的 dry-run 与网络/账号隔离检查；回退使用上一组已验证的镜像/values，不能删除数据库、对象存储或密钥来回退应用。当前 schema 与模板都拒绝 `executionAdmission=true`，集群执行器接通前不要绕过该门禁。

默认拒绝全部出入站；网关、DNS、身份/数据库/S3 依赖按 namespace **且** pod 标签匹配，防止把两个选择器误写成 OR。分环境有状态依赖和内部调用者要求环境标签一致。外部依赖仅支持显式私有 IPv4 CIDR 和 TCP 端口，拒绝公网、默认路由及元数据地址；双栈 Pod 依赖可用选择器，外部 IPv6 CIDR 需后续扩展验证。web 不读取数据库/模型 Secret，且只代理 `/platform/*`、`/api/v1/*` 和 `/identity`；`/internal/*`、`/health/ready`、Actuator 不会通过工作台代理。
