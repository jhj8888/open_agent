# Design

## Context

helm 已存在，但默认引用 ghcr.io 和 latest，部分服务单副本；Langfuse 默认关闭而 host 有云地址，前端有在线资源引用。现有日志/trace 代码可复用，不能据此推断日志访问控制、保留或完整离线验收已完成。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/offline-platform-operations/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 建立 inventory，区分必须组件和可选知识库/RPA/MCP/云插件；MVP 只启用所需组件，云连接器默认关闭。镜像含应用、沙盒基础镜像、迁移 job 和第三方依赖，均固定 digest。npm/PyPI/Maven/Go/系统包依赖在构建阶段预备到内网源，运行时禁止临时公网安装。
2. Helm 使用 test/prod 独立 release/namespace 和独立 Secret/存储前缀/数据库账号/消息命名空间；生产是否独立集群由企业 K8S 团队交付，但同集群也必须通过隔离验收。使用现有入口网关接入规则，不交付新模型网关。模型 endpoint 只作为 Secret/配置引用，不在 chart 创建模型 Deployment。
3. 生产应用增加 requests/limits、健康检查、PDB、适用的 HPA 和受限 NetworkPolicy；控制面/数据服务高可用拓扑按容量测试和现有基础设施确定。HPA 针对 Agent 平台 CPU/队列，不能解读为 GPU 或模型调度。资源示例不是承诺的容量。
4. 日志和 trace 采用现有 OTel 设施发往内网 Collector；metrics 接企业 Prometheus，日志索引默认候选 OpenSearch。已有合规内网日志系统可通过适配接入，避免重复部署。租户查询必须经后端授权过滤；统计包含接入/成功/失败/取消、排队与运行时延、并发/队列、沙盒回收、模型 API 调用用量；不采集 GPU 推理运维指标。
5. labels 不含 run_id/用户输入等无限基数字段；run_id 放日志/trace。默认脱敏 prompt、响应和凭据；详细调试需授权、期限、脱敏策略。审计与普通日志分离保留，默认值在部署清单明确，管理员可配置容量和保留期。
6. 离线制品包含 SBOM、第三方许可证、签名/摘要、源版本、迁移顺序及回滚手册。现有 MinIO/Redis/MySQL 等按实际版本审核，不能因 Astron 主仓库 Apache-2.0 就宣称所有依赖都是宽松许可。OpenSpec 只在开发/CI 使用，不成为运行时外部依赖。
7. 从浏览器到后端做断网端到端验收：DNS/防火墙阻断公网，导入制品后冷启动，登录、发布、聊天、运行 Skill、查日志、重启恢复全部可用。备份涵盖配置、制品、任务状态、Temporal 与业务数据；恢复必须在隔离环境实际演练。
8. Skill 平台使用独立 Spring Boot 启动入口，只扫描新增平台控制器和共享租户授权服务；复用既有实现，不为了启动 Skill 平台而要求知识库、语音或云连接器在线。原 HubApplication 入口及其配置保持兼容。独立配置显式提供数据库、对象存储和内网 OIDC，未匹配的接口默认拒绝，健康检查区分进程存活与必需数据库版本就绪。
9. 单机展示控制面使用专属 MySQL、Casdoor 与 SeaweedFS S3；SeaweedFS 采用 Apache-2.0 版本，复用已有 S3 客户端。容器非 root、只读根文件系统、资源与日志有限额，数据网络无公网出口且不发布宿主机端口；不更改旧基础服务。上游迁移硬编码 `astron_console`，专属实例使用此库名并保留历史迁移校验和。安装时锁定镜像摘要、随机生成凭据并一次性登记经真实 JWT 验证的演示账号；重启不重置密钥、密码或账号授权。此展示部署不代替生产 Helm/HA/断网验收。
10. 展示备份使用官方 Restic 0.19.1（BSD-2-Clause）加密仓库，固定二进制摘要、离线安装。维护程序先核验项目服务归属、无活动任务/未释放尝试，停止 API 后再次检查，随后协调停止本项目写入服务；不修改共享 Docker 数据目录或其他应用。备份覆盖账号/授权、业务库、Skill/任务文件、模型凭据加密密钥、Temporal、OpenSandbox 状态、步骤/观测数据库与当前配置/应用制品。源配置与 Docker inspect 含凭据，只进入私有目录及加密仓库，密钥单独托管。失败也恢复原运行状态；中断恢复通过持久维护记录重试。演练用独立目录和无公网出口的容器网络，关闭执行准入且不启动 worker，核验数据库、真实登录、文件摘要与观测记录，不覆盖线上数据，不重新执行业务任务。该单机停机备份不等于生产 PITR/HA；完整离线依赖和容量验收仍独立追踪。

## Risks / Trade-offs

集群交付按已实现组件逐步接入独立 `helm/open-agent-platform`，不复用旧 Astron chart 的云插件/知识库/推理依赖。控制面与工作台先独立容器化，镜像只复制已校验的 JAR、静态资源和固定 Caddy 二进制，不在启动时安装依赖。test/prod 使用不同 namespace/release、Secret 引用和专属数据库/存储账号；现有上游迁移要求数据库名 `astron_console`，因此当前两环境必须使用独立 MySQL 实例，不能仅换 schema 名共用实例。Chart 默认拒绝可变镜像 tag，默认拒绝全部出入站，仅开放同环境组件、指定网关和显式依赖端点；不挂载 Kubernetes 服务账号 token，不授予集群权限。控制面/工作台有请求与限制、探针、PDB/可选 HPA，外部企业网关负责 TLS。独立数据库/身份/S3、Temporal/OpenSandbox/Kata、执行监督器及镜像仓库为明确依赖；当前 systemd supervisor 不能包装成一个声称可在 K8S 运行的 Pod。集群执行器接通前，专用 chart 强制关闭任务准入，持续追踪后续实现与实机验收。

[隐藏公网依赖] → 网络审计+冷启动断网验收；[依赖许可差异] → 版本级 SBOM 和许可证清单；[日志泄露与高基数] → 脱敏/授权/label 限制；[单副本与备份不可用] → 拓扑校验和实际恢复演练。

## Migration Plan

集群 Python 运行镜像同时提供受信 executor 和 supervisor 入口，使用固定 Linux amd64 Python 基础镜像和当前 `uv.lock` 的完整 cluster extra。官方 uv 导出带哈希 requirements 与 CycloneDX 清单；准备阶段由官方 PyPA marker 根据已探测的目标环境过滤依赖，再由官方 pip 指定 Python/ABI/manylinux 平台下载 wheel，禁止安装 sdist 或未锁定依赖。构建时在实际 Linux Python 中消费完整原始 requirements 再核验。构建阶段只使用已核验的基础镜像、wheel、显式 Git 源码清单与许可证，Docker 构建网络关闭、pip no-index/require-hashes，缺包不得在线补齐。宿主构建目录私有，COPY 进入镜像的公开源码/许可目录可由非 root 遍历。最终镜像不包含 wheel 缓存、业务配置、账号、模型密钥或 Kubernetes token，默认 UID/GID 10001，启动不安装依赖。基础镜像配置 ID 与官方 Registry manifest 交叉核验；制品记录区分 registry manifest digest、本地 image/config ID 和导出归档摘要。单机导入、模块/权限检查、空节点冷启动与真实 Kata/CNI/业务全链路验收分别追踪，不互相替代。

监控增量调用路径：工作台 → Java 租户授权 → MySQL 窗口聚合；平台管理员 → Java 固定查询 → 内部 Prometheus → node_exporter / Java Micrometer 指标。窗口为 1h/24h/7d，起止时刻固定，不受部署列表分页影响，缺少 usage 的任务不按零计量。当前排队、执行租约占用及未释放沙盒与窗口历史统计区分。仅 LOG_READ 角色可读取租户聚合；主机指标要求平台管理员，生产执行委托不自动获得租户聚合。

采用 Spring Boot 管理版本的 micrometer-registry-prometheus，使用隔离的官方注册表导出固定指标，不手写 Prometheus 文本格式。只读 observer 服务角色仅能 GET 私有指标端点，worker/dispatcher/reconciler 角色互不扩权。聚合指标采用固定有限 labels，任务标识只进入既有授权日志。Prometheus 与 node_exporter 固定官方版本/摘要、Apache-2.0；展示安装使用专属非 root systemd 服务、有限保留空间及仅内部可达地址，保留其他项目服务。生产配置可接已有内网 Prometheus。采集失效不得用 0 表示健康，告警规则需 promtool 验证。

先交付 test values 与离线清单，再生成 prod 配置；共享依赖改动必须记录所有调用者影响。数据库使用可向前兼容迁移，发布失败回滚应用和绑定但不丢弃新任务数据；恢复时间和丢失窗口以实测报告为准。
