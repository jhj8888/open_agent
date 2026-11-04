# Proposal

## Why

用户需要先查看可操作的阶段演示，评审 Agent 工作台和调试流程。完整平台仍在集成，不能将模拟页面声称为真实执行交付。

## What Changes

- 提供独立的 React 界面预览，复用现有前端依赖，覆盖 Agent、Skill、任务、发布接口、监控、租户与聊天。
- 持续标注示例数据，所有输入仅保存在浏览器内存；不调用模型、不上传文件、不创建实际沙盒。
- 在授权测试服务器部署独立、非 root 的静态演示服务。

## Capabilities

### New Capabilities

- `interface-preview`: 可评审的交互界面和明确的模拟边界。

### Modified Capabilities

无。

## Impact

调用链：浏览器 → 独立静态服务 → 已构建的 HTML/JS/CSS；交互只修改 React 内存状态。与现有控制面、认证、数据库、模型和沙盒无调用关系；不改变真实平台的交付验收范围。
