# Proposal

## Why

用户要求 Agentic Observability：任务日志、感知/执行过程、工具调用、每步 Token/耗时及周/月统计。现有任务级事件和主机监控不能完整覆盖这些能力。

## What Changes

- 采用超过 5K Stars、企业友好许可且可自托管的观测组件；当前选定 MLflow 3.18.0（Apache-2.0），Langfuse OSS 保留为可选部署，记录真实版本和许可边界。
- 接入模型/工具/上下文/Skill 步骤及父子关系，持久关联 run、attempt、trace 与产出文件。
- 增加授权执行时间线、逐调用计量、自然周/月聚合与导出；观测故障不得重放业务执行。

## Capabilities

### New Capabilities

- `agent-execution-observability`: 可授权查询的逐步执行和周期统计。

### Modified Capabilities

无。现有目标变化需与 `persist-agent-runs`、`deliver-offline-platform` 一同验证。

## Impact

DeepAgentsRunner/模型 SDK 回调 → 归一与脱敏 → 任务事件/步骤投影 + 官方 OTel/观测 SDK → 内部后端；浏览器 → Java 任务授权 → 执行步骤/周期统计。关联 `retain-task-artifacts` 的文件 ID，不把文件生命周期交给 trace TTL。模型部署、网关、推理不在范围。
