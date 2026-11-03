# Proposal

## Why

租户可以上传 Python 和 Shell，普通应用容器及现有公共沙盒默认配置不足以构成企业不可信代码隔离。需要把代码执行放入私有、受限、可回收且绑定租户与任务的独立沙盒。

## What Changes

- 引入私有沙盒 provider，生产使用独立内核的隔离运行时并默认禁止网络。
- 按租户/环境/任务/attempt 分配资源并管理生命周期、取消和产物。
- 排查并约束 Skill 和 workflow code node 两条代码执行入口。

## Capabilities

### New Capabilities

- `skill-sandbox-isolation`: 私有沙盒与不可信代码隔离。

### Modified Capabilities

无。当前尚无已验收主规格；这是对现有代码的增量产品契约，不代表从零重写。

## Impact

SkillFile/配置 UI → Java SkillSandboxConfig/签名调用 → Python SkillSandboxRunner → 私有 OpenSandbox 服务 → K8S 沙盒节点（Kata）。workflow code executor 必须使用同一准入策略。

边界：不涉及模型网关、模型调度、推理部署或模型运维；复用已有模型 HTTP 接口。
