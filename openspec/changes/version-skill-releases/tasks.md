# Tasks

本文件中的未勾选项均为待实现工作；规格校验通过不代表功能完成。前置关系见 docs/platform/roadmap.md。

## 1. 制品和迁移

- [ ] 1.1 新增 Skill release/manifest 数据迁移并保留现有草稿接口；验证导入旧 Skill、重复执行迁移和回滚方案。
- [x] 1.2 实现流式包校验、摘要计算和对象暂存发布，测试目录穿越、链接、压缩炸弹、重复文件及中途失败清理。
- [x] 1.3 补充版本 API 的授权和引用保留逻辑；测试跨租户下载与在用删除失败，并记录存储接口影响。

## 2. 运行与界面

- [ ] 2.1 改造 SkillEnrichmentService 与 core/agent 的资源装载以固定 release；运行中改草稿测试证明制品不漂移。
- [ ] 2.2 在 Skill UI 提供版本列表、摘要、验收结果与 Agent 绑定；端到端测试覆盖发布和回滚并更新使用文档。
- [x] 2.3 实现测试→生产推广的角色/证据校验与审计；未经测试或越权推广必须失败。

## 3. 联调

- [ ] 3.1 与沙盒和任务记录联调同一 release 的重复运行和失败重试，核验每次的 manifest_digest 完全一致。

2026-11-04 增量证据：`SkillArchiveValidatorTest` 16 项、`SkillReleaseServiceTest` 13 项、`LegacySkillReleaseImporterTest` 6 项通过；真实 MySQL 8.4.13 的平台迁移检查 7 项通过，其中提取服务原始推广/删除 SQL 验证错误摘要、保留期、永久引用和生产授权。存储影响与 API 见 `docs/platform/skill-releases.md`。任务 1.1 已有新增表和显式旧草稿导入，但全量 Flyway 重入/应用回滚尚未演练；2.2 已有发布界面，Agent 绑定和浏览器端到端仍未完成。3.1 保持待验收。
