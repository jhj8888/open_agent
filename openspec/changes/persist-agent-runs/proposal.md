# Proposal

## Why

现有进程内 asyncio 任务和带 TTL 的 Redis 事件不能保证服务重启后仍可追踪和恢复。平台需要持久运行记录、明确状态机、租户并发准入以及可验证的失败恢复语义。

## What Changes

- 增加持久 run/attempt/event 模型、幂等提交、取消和可恢复执行。
- 按租户和环境执行任务/沙盒配额，提供排队上限、公平性和背压。
- 采用私有 Temporal 管理长任务生命周期，保留业务数据库作为对外查询与授权视图。

## Capabilities

### New Capabilities

- `durable-agent-runs`: 持久任务与租户并发控制。

### Modified Capabilities

无。当前尚无已验收主规格；这是对现有代码的增量产品契约，不代表从零重写。

## Impact

聊天/外部 API → Java 运行准入服务 → 业务库与 outbox → Temporal → Python 执行 Worker → 模型 API/沙盒。现有 Redis 可保留事件通知缓存，不能作为任务唯一真相。

边界：不涉及模型网关、模型调度、推理部署或模型运维；复用已有模型 HTTP 接口。
