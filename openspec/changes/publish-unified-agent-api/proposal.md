# Proposal

## Why

现有公开 API 发布主要面向 workflow，独立 Agent 与工作流的执行入口不同。每个部署的 Agent 都需要稳定、可鉴权、可追踪的 HTTP 访问方式，聊天调试应复用同一执行与权限语义。

## What Changes

- 新增部署级 runs API，覆盖独立 Agent 和 workflow Agent。
- 提供异步接入、状态、事件、取消及可选聊天流适配。
- 调试窗口展示 run_id、版本、环境、状态、脱敏日志和用量。

## Capabilities

### New Capabilities

- `agent-http-access`: 统一 Agent HTTP API 与聊天调试。

### Modified Capabilities

无。当前尚无已验收主规格；这是对现有代码的增量产品契约，不代表从零重写。

## Impact

现有企业入口网关 → 控制台发布/运行 API → 持久任务服务 → Java 或 Python 执行器。PublishApiServiceImpl 当前限制 WORKFLOW，保持已有 /workflow/v1/chat/completions 契约。

边界：不涉及模型网关、模型调度、推理部署或模型运维；复用已有模型 HTTP 接口。
