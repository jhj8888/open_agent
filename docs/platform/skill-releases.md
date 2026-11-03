# Skill 发布版本

2026-11-04：已实现 ZIP 发布、旧草稿显式导入、摘要清单、环境授权下载、生产推广和引用保留。Agent 绑定与隔离任务联调仍待完成，不能据此宣布整个 Skill 运行链路已验收。

## 调用路径和存储影响

React 租户环境页 → `PlatformSkillController` → `TenantAccessService`（已验证身份和实时成员关系）→ `SkillReleaseService` → 官方 Apache Commons Compress ZIP 解析 → 官方 MinIO Java SDK → 已有 S3 桶。V1.51 只新增 `oa_skill_*` 表，不改变 `skill_file`、旧草稿接口、Kafka、Redis 或其他服务客户端。

对象使用 `workflow/artifacts/open-agent/skill-releases/<tenant SHA-256>/<upload UUID>/` 私有前缀。已有 `S3ClientUtil` 桶策略禁止匿名获取 `workflow/artifacts/*`；上线时仍须核验实际桶策略。新接口通过控制面授权读取对象，不返回公开 URL、存储密钥或预签名 URL。不改变桶策略，也不采用旧 `S3Util.getObject()` 的无界 `readAllBytes` 路径。

上传先建立可回收的暂存意图，写入新 UUID 下的对象，再在租户锁内重新鉴权并发布 manifest、文件表、环境授权和审计。暂存行锁防止清理器删除仍在写入的对象。失败或过期上传每 10 分钟重试清理；已提交的可用版本不会被暂存清理删除。相同租户、Skill 名、版本和 manifest 重试返回原发布 ID；内容不同返回 409，不覆盖已发布对象。

Manifest 保存原始 UTF-8 JSON 字节，SHA-256 覆盖文件路径、字节数、内容摘要、版本和运行镜像。包含依赖锁文件标记及 `preinstalled-image-only` 策略。读取方应校验原始 manifest 字节，不跨语言重新序列化后算摘要。文件下载再次核验长度和 SHA-256。

## 配置与使用

管理员通过 `SKILL_RUNTIME_IMAGES` 配置完整镜像白名单，例如：

```text
["registry.internal/platform/skill-python@sha256:<64位真实镜像摘要>"]
```

默认空白名单拒绝发布。示例占位符不能直接使用。镜像必须预装业务依赖，包不能要求运行时访问外网安装依赖。此配置不等于沙盒已经安全隔离，隔离由后续 OpenSandbox / Kubernetes RuntimeClass 部署验收负责。

在控制台“租户管理 → 环境 → Skill 发布版本”选择 ZIP、版本和批准镜像。ZIP 根目录含 `SKILL.md`，或只含一个与 frontmatter `name` 一致的目录。`name`、`description` 按 Agent Skills 规范验证。限制：ZIP 8 MiB、单文件 5 MiB、总内容 20 MiB、100 个文件、200 个条目、压缩比 100。拒绝路径穿越、非规范路径、大小写冲突、文件/目录冲突、链接、设备、加密条目及 ZIP64/分卷包；不解压到宿主路径、不执行代码。

Apache `ZipFile` 会先分配中央目录条目，缺少解析前的条目数量上限，因此在调用官方解析器前只检查经典 ZIP 的固定尾部和中央目录边界；实际条目、压缩和内容校验仍由官方库负责。Commons 会规范化 DOS 反斜杠，额外检查原始文件名防止掩盖非法路径。YAML 使用 SnakeYAML 安全构造器和嵌套/别名/长度限制。

基础路径：`/platform/tenants/{tenant}/environments/{test|prod}/skill-releases`。

| 方法和路径 | 作用 |
|---|---|
| GET 基础路径 | 当前环境版本列表、摘要、测试状态 |
| GET `/runtime-images` | 批准的不可变运行镜像 |
| POST 基础路径 | multipart `archive`、`version`、`runtimeImage`，仅测试环境管理员/开发者 |
| POST `/import-draft` | JSON `skillId`、`version`、`runtimeImage`，从原空间或个人归属中读取旧 Skill，记录 `legacy-import` 来源 |
| GET `/{release}/manifest` | 原始清单，响应含 `X-Manifest-SHA256`，不缓存 |
| GET `/{release}/files/{index}` | 鉴权后的二进制附件；索引对应清单文件顺序 |
| POST `/{release}/promote` | 生产环境 operator 推广已有测试版本 |
| DELETE `/{release}` | 测试环境删除；保留期、任务/部署引用、生产授权均可阻止删除 |

推广要求同租户测试环境原版本为 ready，且存在相同 manifest 摘要的成功测试证据。只有后续可信任务完成事务写入 `oa_skill_verification`；当前没有任何“手动标记通过”接口。任务链路未接入前，新版本展示“待验证”，不能用伪造证据绕过这一要求。

已删除版本保留数据库墓碑和摘要，物理对象由清理器删除。发布时按租户测试环境审计保留天数设置最低保留期。任务和部署接入时必须在同一租户锁事务下写入 `oa_skill_release_reference`，防止引用与删除竞争。当前尚未接入的调用方不得绕过版本状态和引用检查直接执行。

## 迁移与回滚

V1.51 由已有 Flyway 版本表管理执行，不支持手工重复运行 CREATE TABLE。回滚应用时停止新发布和清理任务，保留 `oa_skill_*` 表、manifest 和对象；旧 `skill_file` 未改动，草稿接口继续可用。不要通过 DROP TABLE 回滚已被引用的发布版本。全量 Flyway 重入与实际应用回滚演练尚待完成，因此 OpenSpec 任务 1.1 保持未完成。

旧草稿导入通过服务端租户映射确定 `space_id` 或大小写敏感的个人 UID；按父目录读取至多 200 个条目，不接受客户端提供的对象 URL/key。只读取该 scope 下的 `skill-files/...` 对象，不覆盖原文件。发布仍使用与 ZIP 相同的元数据/路径/大小校验、版本幂等规则和对象暂存事务。旧草稿本身没有多文件事务版本：导入前后检查目录元数据和文件长度，观察到变化则拒绝；这不是旧草稿跨文件原子快照的保证。批量迁移期间应暂停草稿编辑。无论之后草稿如何变化，已发布清单绑定的是这次实际读到并校验的字节。

## 验证与依赖

- Java 单元/契约检查覆盖恶意 ZIP、摘要稳定、授权拒绝、上传中途失败清理、同版本重试、变更中成员撤销、推广证据、保留删除和原空间导入；运行 `SkillArchiveValidatorTest,SkillReleaseServiceTest,LegacySkillReleaseImporterTest`。
- `console/backend/tests/test_tenant_migration.py` 在真实 MySQL 8.4.13 执行 V1.51、环境外键和大小约束，并从服务源码提取推广、删除 SQL，验证错误摘要不通过、保留期/永久引用/生产授权阻止删除。
- UI 已提供发布、列表、文件摘要与下载；浏览器端到端、真实对象存储故障演练、Agent 绑定和运行重试尚未验收。
- 新增直接依赖 Apache Commons Compress 1.26.2，Apache-2.0。该版本与已有 Commons IO/Lang 兼容，未顺带升级其他共享依赖。MinIO Java SDK、Jackson、SnakeYAML 为已有依赖；保留上游许可和 NOTICE。
- Context7 工具在本会话不可用，已核对[官方 ZIP 文档](https://commons.apache.org/proper/commons-compress/zip.html)、[安全公告](https://commons.apache.org/proper/commons-compress/security.html)、[MinIO SDK 文档](https://minio-java.min.io/io/minio/MinioClient.html)和 [Agent Skills 规范](https://agentskills.io/specification)，并以仓库实际依赖的编译/测试验证 API。
