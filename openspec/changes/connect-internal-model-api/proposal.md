# Proposal

## Why

模型部署与运维由专门团队承担，平台需要稳定使用其内网接口。应统一接口配置、凭据作用域、能力校验、超时与用量记录，避免把模型基础设施纳入 Agent 平台。

## What Changes

- 支持按租户/环境引用模型 API 配置及凭据，调用前验证所需能力。
- 两条 Agent 执行路径使用一致的超时、错误和脱敏用量语义。
- 不新增模型代理网关、模型路由调度、推理部署、模型下载或 GPU 管理。

## Capabilities

### New Capabilities

- `model-api-access`: 通过内网接口调用模型。

### Modified Capabilities

无。当前尚无已验收主规格；这是对现有代码的增量产品契约，不代表从零重写。

## Impact

模型配置 UI → console/backend 模型服务 → SpringAiAgentChatService 或 core/agent/domain/models/providers.py → 已有模型内网 API。调试 UI 和发布 API 复用配置引用。

边界：不涉及模型网关、模型调度、推理部署或模型运维；复用已有模型 HTTP 接口。
