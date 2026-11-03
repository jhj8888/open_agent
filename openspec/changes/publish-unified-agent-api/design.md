# Design

## Context

两条 Agent 路径不能只在网关加 route 就自动统一能力。已有发布审批逻辑应复用；租户归属不能只信任外部请求中的 flow_id/app_id。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/agent-http-access/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 先交付统一异步 API：POST /api/v1/agent-deployments/{deployment_id}/runs（202、run_id、status_url、events_url），GET /api/v1/runs/{run_id}，GET /api/v1/runs/{run_id}/events（SSE），POST /api/v1/runs/{run_id}/cancel。HTTP Idempotency-Key 为创建任务必需项，客户端可生成随机键。
2. deployment_id 固定 tenant/environment/agent version/Skill releases/model config refs；由服务端查询授权后解析，路径无需暴露 tenant_id。权限凭据分用户身份与部署服务凭据，服务凭据仅允许配置的部署与动作，支持轮换/吊销。
3. v1 状态码：401 未认证、403 已认证但动作不足、404 不可见资源、409 幂等键内容冲突/部署状态冲突、422 参数/能力错误、429 准入背压、503 执行依赖不可用。接入后的错误进任务状态，已开始 SSE 后使用带 run_id 的 error 事件，不伪造 HTTP 200 成功。
4. UI 聊天创建相同 run，订阅相同事件，取消走同一 API；对话 session 不是共享沙盒目录。开发者可在测试环境查看草稿快照（入队前生成明确临时 release），生产只使用已发布部署。展示工具轨迹、结果、耗时和可得用量，默认不展示原始敏感提示词。
5. 保留旧 workflow API，文档给出迁移映射；增加独立 Agent 能力通过新的部署 API 完成，不突然更改老用户的请求/响应协议。按部署提供可复制 curl 示例和内部文档链接。
6. 前置：manage-tenant-access、version-skill-releases、persist-agent-runs；代码工具还依赖 isolate-private-skill-sandboxes。模型配置须通过 connect-internal-model-api 契约。无需替换现有入口网关。

## Risks / Trade-offs

[旧 API 行为破坏] → 新命名空间与旧契约回归；[浏览器重连重复提交] → 幂等键和独立事件订阅；[凭据范围过宽] → 部署级授权；[调试泄露生产数据] → 强制环境权限。

## Migration Plan

新增部署 API 和测试聊天路径，旧路由继续提供兼容访问；迁移工具生成 deployment 绑定但不自动开放生产权限。回滚隐藏新入口并保留既有任务查询与取消，不直接停止运行中的任务。
