# Astron Agent 本地源码评估

评估对象：用户提供的 `astron-agent-main-831e7b1` 目录，2026-11-03。这是静态源码评估，不是已完成的集群部署/渗透/性能验收。

结论：适合作为控制台与 Agent 执行基础，在此基础上做企业化增量改造。现有 Skill、聊天、工作流、权限与 Helm 能显著减少从零开发；仍需完成私有不可信代码隔离、不可变版本、持久任务与统一 API 等工作。

## 已观察到的能力和缺口

| 需求 | 本地代码证据 | 结论 |
|---|---|---|
| Skill 管理 | [SkillFileService](../../console/backend/toolkit/src/main/java/com/iflytek/astron/console/toolkit/service/skill/SkillFileService.java)、[前端 Skill 页](../../console/frontend/src/pages/resource-management/skill-page/index.tsx) | 已有 SKILL.md、文件树、编辑、资源上传；需要不可变 release 与环境推广 |
| Skill 数据 | [V1.27 迁移](../../console/backend/hub/src/main/resources/db/migration/V1.27__add_skill_file_system.sql) | 当前 skill_file 记录文件树，不能等同于发布版本库 |
| 租户/空间 | [SpaceAuthAspect](../../console/backend/commons/src/main/java/com/iflytek/astron/console/commons/aspect/space/SpaceAuthAspect.java)、SkillFileService | 已有空间/成员与部分 uid/space_id 校验；需覆盖运行、日志、制品和环境全链路 |
| Python Agent | [deepagents_runner.py](../../core/agent/engine/deepagents_runner.py) | 使用 DeepAgents/StateBackend；当前过滤 task 工具，不能宣称自主多 Agent 委派已具备 |
| Java 聊天 Agent | [SpringAiAgentChatService](../../console/backend/hub/src/main/java/com/iflytek/astron/console/hub/service/chat/springai/SpringAiAgentChatService.java) | 存在独立 Spring AI 执行路径，改造须和 Python 路径一致 |
| 模型接口 | [providers.py](../../core/agent/domain/models/providers.py) | 有 base_url 等配置适配基础；平台只做调用侧接入 |
| Skill 沙盒 | [skill_sandbox.py](../../core/agent/service/plugin/skill_sandbox.py)、[SkillSandboxConfigService](../../console/backend/toolkit/src/main/java/com/iflytek/astron/console/toolkit/service/skill/SkillSandboxConfigService.java) | 当前 E2B provider 和 create/run/kill 路径；并非已验证的企业离线沙盒 |
| 资源检查 | [skill_resource_security.py](../../core/agent/service/plugin/skill_resource_security.py) | 已有 URL/路径/资源限制，迁移时保留并补充测试 |
| Workflow 代码节点 | [code_node.py](../../core/workflow/engine/nodes/code/code_node.py) | 存在不同 executor，受限 Pyodide/Deno 不能替代任意 Python/Shell 的完整隔离；逐入口检查 |
| 任务状态 | [chat_service.py](../../core/workflow/service/chat_service.py)、[event_registry.py](../../core/workflow/cache/event_registry.py) | asyncio 进程任务、Redis TTL 事件不等于持久恢复；Celery 基础设施存在也不代表主链已接入 |
| HTTP 发布 | [PublishApiServiceImpl](../../console/backend/hub/src/main/java/com/iflytek/astron/console/hub/service/publish/impl/PublishApiServiceImpl.java)、[集成说明](../guide/integration.md) | 已有工作流发布和 API；createBotApi 限制 WORKFLOW，需支持独立 Agent 的统一部署 API |
| 日志 trace | [Langfuse 集成](../../core/common/otlp/trace/langfuse.py) | 有可复用基础；要配置为内网并补租户授权、脱敏、保留与告警 |
| K8S 部署 | [Helm values](../../helm/astron-agent/values.yaml)、[Helm README](../../helm/README.md) | 已有真实 chart；仍需处理公网镜像、版本锁定、环境隔离和生产运行配置 |

## 跨服务调用链

1. 工作流 Agent：控制台 → workflow AgentNode → `/agent/v1/custom/chat/completions` → DeepAgents runner → 模型 provider / Skill 工具 → 沙盒。
2. 独立聊天 Agent：控制台 → Java BotChatService / SpringAiAgentChatService → 模型接口及 Skill tool callbacks → Python 沙盒执行服务。
3. Skill 管理：前端 → Java SkillFileService → 数据库/对象存储 → SkillEnrichmentService → 上述两条执行路径。

改造时先定义共同的租户/环境、run_id、release_id、凭据引用和事件契约，再分别接入两条路径。不要只改其中一条就宣称全平台完成。

## 验收缺口

尚未在用户真实 K8S/CPU 节点验证 Kata、CNI 出口策略、模型接口、存储、任务恢复、容量和断网冷启动。当前所有新产品能力均在 `openspec/changes`，没有归档为已交付主规格。上游主许可证是 Apache-2.0，依赖及图片/字体等资产需要单独审查。
