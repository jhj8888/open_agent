# 开源组件记录

核验日期：2026-11-03。Stars 为当日官方 GitHub 页面显示的四舍五入值，均超过 1,000。GitHub 匿名 REST API 当时限流，以下采用仓库页面而非声称获得精确 API 数值。每行链接同时提供源码与 LICENSE 入口。

| 用途 | 官方仓库 | Stars | 仓库主许可 | 使用决策 |
|---|---|---|---|---|
| 平台基座 | [iflytek/astron-agent](https://github.com/iflytek/astron-agent) | 9.0k | Apache-2.0 | 采用用户本地快照；保留来源与许可 |
| 规格开发工具 | [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec) | 73.8k | MIT | 开发依赖已固定 1.16.0，不部署到生产 |
| Agent 主引擎推荐 | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | 30.9k | MIT | 复用 Python 路径；原生 Skill 与现有桥接需收敛 |
| 编程执行引擎候选 | [anomalyco/opencode](https://github.com/anomalyco/opencode) | 224.2k | MIT | 评估 V2；首期不新增第二运行时，见[主引擎评估](agent-runtime-evaluation.md) |
| 身份认证 | [casdoor/casdoor](https://github.com/casdoor/casdoor) | 14.7k | Apache-2.0 | 复用上游集成；仅使用身份功能 |
| 私有沙盒 API | [opensandbox-group/OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) | 16.4k | Apache-2.0 | 拟新增；需锁定版本并通过集群兼容验收 |
| 独立内核隔离 | [kata-containers/kata-containers](https://github.com/kata-containers/kata-containers) | 9.1k | Apache-2.0 | 拟用于不可信生产代码；验证 CPU 节点虚拟化 |
| 持久任务 | [temporalio/temporal](https://github.com/temporalio/temporal) | 24.1k | MIT | 拟新增私有服务；不能自动替代应用幂等和 checkpoint |
| 遥测采集 | [open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector) | 7.7k | Apache-2.0 | 接内网后端，优先复用企业已有采集 |
| 指标 | [prometheus/prometheus](https://github.com/prometheus/prometheus) | 66.9k | Apache-2.0 | 优先接企业已有实例，只采平台指标 |
| 日志查询 | [opensearch-project/OpenSearch](https://github.com/opensearch-project/OpenSearch) | 14.0k | Apache-2.0 | 默认候选；已有企业日志平台时可复用 |
| 日志仪表盘 | [opensearch-project/OpenSearch-Dashboards](https://github.com/opensearch-project/OpenSearch-Dashboards) | 2.1k | Apache-2.0 | 可选管理界面；不能绕过租户授权 |

这里只记录平台层采用/计划采用的主要组件，表中的主许可不代替版本级完整依赖审查。计划采用不代表已安装、已固定生产版本或已通过兼容测试。除 OpenSpec 外，本次不自动升级上游依赖。

## 企业许可与离线决策

优先选择 Apache-2.0/MIT 等宽松许可组件，保留版权与许可证及需要的 NOTICE。不把 AGPL、GPL、带附加限制的源码可见许可证混写为“都可无条件商用”。主仓库的许可标签不自动覆盖企业版、插件、模型权重、容器中的操作系统包或字体。

上游已有 MySQL、PostgreSQL、Redis、MinIO、Kafka 等基础依赖，本次未更换，具体镜像版本仍须逐一核验。尤其不能把上游 MinIO/Redis 的当前版本默认为宽松许可；离线交付任务要求记录实际版本、许可及企业处理决定。已有合规基础设施可优先接入以减少新增依赖。

Langfuse 属于上游可选集成，默认不启用外网；若启用，须另外核对所需功能是否在相应开源许可范围，并使用内网部署。本方案的必要日志/指标能力不依赖其商业功能。

不引入模型部署框架、模型网关、推理调度和 GPU 运维组件。OpenSandbox 的生命周期管理与 Kata 隔离运行时需要联合验证，不能以两个项目分别满足 stars/许可条件推定组合可直接生产使用。

## 选型与实现证据

- [OpenSpec CLI 官方文档](https://openspec.dev/docs/cli)：开发工具配置。
- [OpenSandbox 官方仓库](https://github.com/opensandbox-group/OpenSandbox)：私有沙盒和 Kubernetes 接入候选。
- [Kata 官方仓库](https://github.com/kata-containers/kata-containers)：独立内核隔离和硬件支持要求。
- [Temporal 官方文档](https://docs.temporal.io/)：持久执行与官方 SDK；副作用幂等由应用设计保障。
- 本地代码使用现状见 [基线评估](baseline-assessment.md)，每项具体方案和验收见 OpenSpec changes。
