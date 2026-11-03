# Design

## Context

SkillFileService.updateContent 修改现有记录，V1.27 的 skill_file 表保存当前文件树；SkillEnrichmentService 在运行时组装下载资源。新版本不能继续依赖可变文件 URL。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/skill-releases/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 数据表 skill_release、skill_release_file 保存 tenant_id、skill_id、release_id、manifest_digest、创建者和验证状态；文件内容按摘要保存，上传完成和摘要核验后才能原子发布 manifest。草稿继续使用现有文件树。
2. manifest 包含 SKILL.md、相对路径、大小、摘要、运行镜像 digest 和依赖锁定信息。服务端拒绝绝对路径、../、链接逃逸、压缩炸弹、重复路径及大小/文件数越限。对任意代码不声称静态检查即可证明安全。
3. 发布绑定 agent_deployment 到 release_id；测试/生产只改变部署绑定，不复制后重新编辑包。运行开始将 release 和配置摘要记录为快照，不能中途读到新草稿。
4. 使用已有对象存储接口，不因为版本功能更换存储系统。暂存对象定期回收；已被部署或保留期内任务引用的 release 不允许物理删除。租户检查先于生成下载凭证。
5. 旧 Skill 先转成一个明确的 legacy-import 版本，不自动推广生产。API 兼容草稿操作；运行侧默认要求 release，对旧调用在迁移窗口提供显式版本解析记录。

## Risks / Trade-offs

[数据库与对象写入不一致] → 暂存上传+发布事务+孤儿回收；[生产旧版本被误删] → 引用检查与保留期；[恶意包] → 限额校验+隔离执行，不把扫描通过当作可信代码。

## Migration Plan

增表并导入既有 Skill；以只读快照回放测试后切换部署绑定。回滚停止新版本发布但保留 manifest 和已有引用，不覆盖发布对象。
