# Tasks

本文件中的未勾选项均为待实现工作；规格校验通过不代表功能完成。前置关系见 docs/platform/roadmap.md。

## 2026-11-05 集群执行部署与 TLS 增量

- [x] 独立执行 chart 提供环境专用 supervisor/executor 身份、受限 RBAC、默认拒绝 NetworkPolicy、短时投射 token、固定 Pod 声明准入策略；Secret/CA 与内网仓库引用由操作者预置，仓库引用进入执行绑定摘要。
- [x] 真实零节点 K3s 1.36.6 API 对 test/prod 分别拒绝 8 类 RBAC 越权和 16 类不合规 Pod，并通过官方 SDK 的创建、解除门禁、停止、未启动证据和 finalizer 删除；CEL 无类型警告，所有自建容器/网络/卷及管理凭据已清理。
- [x] 控制 chart 接入 Spring Boot 原生 PKCS12 TLS、Caddy CA 验证和 HTTPS 探针；8 项 Java 启动/TLS 检查通过，42 项 Helm 渲染/配置检查通过。
- [x] 更新运行镜像和锁定输入，155 个包校验、真实缺包/错误哈希拒绝及 137 项镜像内检查通过，无跳过；独立 Linux overlay 112 项通过，与镜像用例重叠不相加。
- [x] 演示站原任务的 58 步、2 份产物、实际 think/工具结果、Token 汇总、跨租户拒绝及公网 HTTPS 再次核验通过；没有重放任务或替换活动执行链。

证据见 `docs/platform/execution-chart-verification-20261105.json`，安装接线见 `helm/open-agent-execution/README.md`。API 夹具无节点/kubelet/CNI/Kata，未用其证明物理隔离或生产 HA；完整企业证书链联调、私有仓库导入、冷启动、Kata admission/回收和容量仍待完成。原大项保持未勾选，集群准入继续关闭。下列日期增量保留各自当时的边界。

## 1. 离线制品

- [ ] 1.1 盘点 Helm、Docker、前后端和插件的公网依赖，生成必需/可选组件清单；验证核心流程的资源全部可从内网获得。
- [ ] 1.2 提供镜像 digest、语言依赖锁及内网构建/导入脚本，测试缺包失败关闭和完整制品冷启动。
- [ ] 1.3 生成 SBOM 与依赖/资产许可证清单，记录 MinIO/Redis/数据库等实际版本及处理意见，并补齐 NOTICE。

## 2. 部署与监控

- [ ] 2.1 新增 test/prod Helm values 和策略，验证 Secret、命名空间、数据账号、队列、网络与发布权限隔离；helm lint/template 检查通过。
- [ ] 2.2 补充平台组件健康检查、资源限制、PDB/适用 HPA 与数据库备份配置；故障演练验证退出和重启行为并更新运维手册。
- [ ] 2.3 接入内网 OTel/Prometheus/日志后端与租户过滤，测试脱敏、越权、指标基数和告警，并提供仪表盘/查询示例。

## 3. 系统验收

- [ ] 3.1 运行断网端到端验收，覆盖登录、Skill 发布、聊天、HTTP 并发、取消、日志、状态和回滚；保存网络审计及测试证据。
- [ ] 3.2 在隔离环境恢复完整备份并压测平台 CPU/任务容量，记录实际 RPO/RTO、吞吐和排队时延，明确未涵盖模型推理容量。

## 2026-11-04 控制面制品增量

新增独立 Skill 平台启动入口，复用现有平台服务而不加载旧业务控制器；默认仅监听 loopback，配置显式引用数据库、内网 OIDC 与 S3，未匹配路径拒绝。官方 Spring Boot 可执行 JAR 构建成功，4 项完整启动/身份传递/健康检查通过，其中包含真实 Tomcat HTTP 请求。产物摘要及复现命令见 `docs/platform/control-plane-runtime.md`。CI 已纳入这组启动检查以及公开 API/SSE 检查，远端执行结果另行确认。

此增量尚未完成真实身份服务、对象存储、持久 worker 的共同部署；离线制品清单、生产 Helm 和系统恢复仍待完成，原任务保持未勾选。

## 2026-11-05 真实控制面依赖增量

已部署独立 MySQL 8.4.13、SeaweedFS 4.52、Casdoor 4.25.0 与 Java 控制面，四个镜像 digest 与官方 Registry 核对一致。53 个迁移完整应用到 V1.54；实际 S3 授权/读写/重启、真实 OIDC PKCE/JWT、租户创建、身份/数据库/API 重启持久化通过。安装器带归属检查、并发锁、原子配置写入、随机持久凭据和资源上限。证据见 `docs/platform/control-plane-verification-20261105.json`，部署边界和初始失败修复见 `docs/platform/control-plane-deployment.md`。

持久 worker、物理回收核验、正式浏览器入口、生产 Helm、完整离线交付和备份恢复仍未完成，原任务保持未勾选；执行准入保持关闭。

## 2026-11-05 私有监控与全量统计增量

- [x] 租户/环境 1h、24h、7d 数据库聚合，未知用量保留未知，当前资源占用独立统计。真实 MySQL 验证 66 条窗口记录、不受列表 50 条上限影响、未释放资源占用及权限撤销。
- [x] 独立只读 observer 导出 32 条有限标签指标；主机监控仅平台管理员可查，固定内部 PromQL。20 项相关 Java/MySQL 检查通过。
- [x] 安装固定官方摘要的 Prometheus/node_exporter，私有监听、独立用户、资源和保留上限；7 条规则通过 promtool 的 22 项告警行为断言。
- [x] 实机核对 7 个统计窗口与 SQL 一致，4 类业务越权拒绝，其他网桥连接被拒绝，停止/恢复 node exporter 后 API 撤回/恢复样本。

证据见 `docs/platform/monitoring-verification-20261105.json`，运维步骤见 `deploy/platform/monitor/README.md`。持续执行、独立回收、HTTPS 工作台已在此前增量接通，当前执行准入开启；上文各日期段落描述当时状态。生产 OTel/日志系统、告警通知路由、离线冷启动、Helm/HA 和备份恢复仍未全部交付，因此原 2.3 和系统验收大项继续保持未勾选。

## 2026-11-05 展示环境一致性备份增量

- [x] 固定官方 Restic 0.19.1 摘要与 BSD-2-Clause 许可证，离线安装并生成独立加密仓库；任务空闲与停止 API 后二次检查、原状态维护记录、失败恢复和所有权约束接通。
- [x] 真实冷备份包含业务/身份库、S3 Skill/产物、任务步骤、配置/密钥、Temporal/OpenSandbox/MLflow 状态；44,255 个文件全部数据块校验通过，维护 132.115 秒。
- [x] 在新目录/私有网络恢复两个数据库且逻辑摘要一致，四个 SQLite 完整性通过；真实登录下载 12 个文件并逐一验证 SHA-256，197 个步骤可查，另一租户访问均拒绝；恢复到接口验收 143.188 秒，任务未重放。
- [x] 演练失败后清理并恢复原服务；修正临时 Docker bridge 的防火墙关联后验收通过，持久清理记录和幂等重试验证通过。10 项维护安全测试纳入 CI。
- [x] 从原冷快照导出约 16 MB 的独立加密数据/配置仓库，原完整快照保留；恢复校验、重复导出、授权工作站复制摘要及独立 DPAPI 密钥托管往返验证通过。软件制品需另行导入。

证据见 `docs/platform/backup-recovery-verification-20261105.json` 与 `deploy/platform/backup/README.md`。此增量为已有制品的单机数据恢复验证；定时/异地/WORM、生产 PITR、恢复执行器的全故障矩阵、K8S/HA、完整离线冷启动和容量测试仍待完成，原 2.2、3.2 大项保持未勾选。

## 2026-11-05 控制面镜像与专用 Helm 增量

- [x] 固定 JAR、工作台、Caddy/JRE 输入摘要并保留许可证；构建不安装依赖、不访问网络、不携带配置/密钥，导出约 537 MB API/web 镜像包并校验 Docker load 与非 root 运行。
- [x] 独立 test/prod chart 使用固定 digest、独立 namespace/Secret、只读容器、探针、资源限制、PDB/可选 HPA 和精确依赖 NetworkPolicy；默认配置及开启执行准入均拒绝，Helm lint/template 与输入/镜像引用共 29 项检查通过。
- [x] 在新数据副本和专用无公网出口网络运行新 API/web 镜像，真实 OIDC 登录代理、3 个静态资源、12 个产物摘要与 197 个步骤验证通过；越权、内部路由、伪造身份头均拒绝，准入关闭不创建任务。
- [x] 提供私有 registry manifest digest 核对工具，明确区分本地 config ID、归档摘要与可拉取 manifest digest；缺基础镜像、错误输入或错误引用失败关闭。

证据见 `docs/platform/platform-delivery-verification-20261105.json`。本轮是控制面交付增量，没有在实际 Kubernetes 集群安装，没有导入企业私有仓库或切换线上运行部署。现有 systemd 执行监督器不能直接充当集群执行器，chart 强制关闭新任务准入。集群执行、Kata/CNI/CA、全部依赖 SBOM、完整离线冷启动和容量验收继续实施，原 1.1–3.2 大项保持未勾选。

## 2026-11-05 Python 集群运行镜像增量

- [x] 固定官方 Python 3.11.18 Linux amd64 基础镜像，并与 Docker Hub Registry 核对 manifest/config；155 个目标 wheel 与 uv.lock 的包名、版本、文件名和 SHA256 一致，官方 uv CycloneDX、逐包许可与补充 NOTICE 已归档。
- [x] 显式 Git 源码输入、私有构建目录、Docker 无网络/不拉取、pip no-index/require-hashes；导出约 706 MB 运行镜像归档及约 135 MB 基础镜像归档，记录独立摘要和本地 image ID。
- [x] 修复 COPY 保留宿主 0700 目录导致非 root 无法加载源码的问题；最终镜像在 UID/GID 10001、网络关闭、只读根目录下实际加载 executor/supervisor，155 个版本一致、pip check 通过、缺配置拒绝启动。
- [x] 在最终镜像中实际执行缺 wheel、错误 hash 两种离线安装，均失败且未生成安装目录；没有访问包索引或修改运行环境。
- [x] 镜像内 130 项检查通过、无跳过：合成模型/控制面/Kubernetes 响应、真实本地 Temporal、步骤采集、工具调用及调度/取消；构建输入与许可边界的 16 项本地检查通过。
- [x] 展示站 HTTPS、3 个静态资源、21 条任务列表用量一致性及既有 58 步/2 个文件再次核验通过，无业务任务重放。

证据见 `docs/platform/worker-image-verification-20261105.json`，操作说明见 `deploy/platform/worker_image/README.md`。镜像只部署为展示机上的独立交付制品，未替换活动 worker 或开启集群准入；同一 Docker daemon 的 load 不等于空节点冷启动。当前完整上游依赖图含开发工具及不同许可，最小运行依赖拆分/企业分发审查、集群 Helm/RBAC/CNI/Kata、全平台离线冷启动与容量验收仍待完成，原大项保持未勾选。
