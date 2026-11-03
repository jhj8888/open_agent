# Proposal

## Why

现有 Skill 编辑会修改当前文件，运行中任务难以复现，测试和生产也缺乏明确的不可变制品边界。需要将 SKILL.md、脚本及资源组成有摘要的版本，并以同一版本完成测试、推广和回滚。

## What Changes

- 草稿编辑与不可变发布版本分离，记录 manifest 和内容摘要。
- Agent 部署固定引用 Skill release；同一制品在测试验收后推广生产。
- 增加包校验、制品保留、发布审计及界面版本选择。

## Capabilities

### New Capabilities

- `skill-releases`: Skill 不可变版本与发布。

### Modified Capabilities

无。当前尚无已验收主规格；这是对现有代码的增量产品契约，不代表从零重写。

## Impact

Skill 编辑 UI → SkillFileService/SkillEnrichmentService → 数据库和对象存储 → Agent 运行配置 → 沙盒资源装载。现有资源 URL/路径检查必须保留。

边界：不涉及模型网关、模型调度、推理部署或模型运维；复用已有模型 HTTP 接口。
