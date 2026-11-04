# Proposal

## Why

用户要求任务生成的资料文件在任务未删除期间始终可查看、下载。当前沙盒是临时执行资源，模型工具文件读取不等于持久产出归档；回收沙盒会失去尚未导出的文件。

## What Changes

- 建立任务产物元数据与持久对象存储，关联生成步骤及内容摘要。
- 在可信边界收集文件，支持任务详情文件列表、安全预览和授权下载。
- 文件保留跟随任务，与沙盒和 trace 保留期分离；增加归档失败、部分产出、容量与删除治理契约。

## Capabilities

### New Capabilities

- `task-artifact-retention`: 任务产出持久化及授权访问。

### Modified Capabilities

无；与任务执行、沙盒清理和 Agent 观测联合验收。

## Impact

不可信沙盒/状态文件 → 可信受限收集器 → Java 有效任务租约 → S3 + MySQL 元数据 → 任务授权 API → 现有前端任务文件列表/下载。复用官方 OpenSandbox/S3 客户端，不共享 Skill 发布文件的删除前缀或扩大沙盒凭据。
