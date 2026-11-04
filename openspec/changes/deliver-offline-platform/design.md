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

## Risks / Trade-offs

[隐藏公网依赖] → 网络审计+冷启动断网验收；[依赖许可差异] → 版本级 SBOM 和许可证清单；[日志泄露与高基数] → 脱敏/授权/label 限制；[单副本与备份不可用] → 拓扑校验和实际恢复演练。

## Migration Plan

先交付 test values 与离线清单，再生成 prod 配置；共享依赖改动必须记录所有调用者影响。数据库使用可向前兼容迁移，发布失败回滚应用和绑定但不丢弃新任务数据；恢复时间和丢失窗口以实测报告为准。
