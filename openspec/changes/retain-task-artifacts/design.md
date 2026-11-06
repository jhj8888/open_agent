# Design

## Context

`platform_runtime/backend.py` 当前下载仅限 32 KiB 并服务于模型文件工具；`durable/executor.py` 与 `DeepAgentsRunner.run` 的 finally 会请求沙盒关闭。`MinioSkillArtifactStore` 使用现有官方 S3 SDK，但其键校验与大小限制针对 Skill 输入。必须新增独立的任务输出存储契约，不能直接扩大 Skill 权限或把临时路径当作下载地址。

## Decisions

1. 任务产物独立前缀/表，可信记录 tenant/environment/run/attempt/step、artifact_id、展示名、类型、大小、SHA256、创建时间与归档状态；对象键由服务端构造。同名不同内容生成不同版本，不覆盖已经显示的产出。目录与压缩包也按不可信文件处理。
2. 执行器引导资料写入任务输出目录，并允许显式登记其他合法工作区产物。工具完成后逐次归档已完成文件，在正常结束前补充收集；提供执行过程中持久发布能力以降低取消/强杀损失。无沙盒的 StateBackend 文件同样通过可信输出契约接入，不能漏掉这一路径。
3. 收集在沙盒内以非特权身份安全打开文件并按界限读取，防目录穿越、符号链接/硬链接逃逸、设备/FIFO、文件变化及无界文件。上传采用官方 SDK/受限传输，由可信服务计算/复核摘要。沙盒无长期 S3/数据库/模型密钥。大文件不能沿用 32 KiB 文本工具通道，需受限流式传输与独立上限。
4. 控制面先建立上传记录，持久对象完整写入并校验后发布可下载元数据，失败不会生成假链接；重复请求幂等。已发布对象与任务不可变关联。控制面崩溃/对象存储失败可恢复对账，孤立临时对象仅按确切未引用记录回收。
5. 任务未删除时已归档产物无自动到期，不跟随沙盒清理或 trace/log TTL。容量配额在写入/准入阶段明确失败，不能偷偷删旧文件。任务删除需显式授权，先隐藏并留下审计/墓碑，再按可恢复策略和独立清理作业删除精确对象；本变更不自动删除任何已有任务。
6. 用户通过任务授权 API 列表/下载，下载时重新校验任务与成员/环境权限；浏览器不接受对象键或长期公开 URL。可信下载响应采用附件、安全文件名、nosniff 和无缓存，文本/Markdown/CSV 等预览转义并限制体积，图片/PDF 使用隔离预览策略；HTML/SVG/可执行文件不以同源活动内容执行，Office 文件至少可下载。
7. 失败/取消任务保留已经成功归档的部分文件并标记。正常任务要求文件归档完成才显示交付完整；上传失败明确显示交付失败/不完整。强杀前未发布且无法安全恢复的文件标记丢失，不能承诺保留从未写完的内容；不得为了等待归档无限延长已撤销的沙盒执行权限。

## Verification

### Explicit task deletion

工作台/API → `AgentRunService.delete` → 当前租户环境管理员授权 → 租户/环境/run 锁 → 终态及全部 attempt/沙盒释放核验 → `oa_run_deletion` 墓碑、审计和产物取消发布同一事务 → 既有 `TaskOutputService.reconcile` 精确键后台清理。删除不调用模型、工具或运行时，不修改共享 TenantPermission、Skill 对象、Redis/Kafka。服务凭据不授予删除权限；测试/生产均需当前 active tenant_admin。运行中、取消中或资源尚未释放返回冲突，不自动取消或释放资源。

删除后列表隐藏，状态/事件/SSE/步骤/文件入口在事务内重新授权时拒绝；同权限 DELETE 幂等，独立 deletion 状态入口返回待清理/已清理。原幂等键仍保留且重提返回 run_deleted，不重新执行。保留任务账本、历史用量/统计、版本验证引用及审计，界面明确删除不是历史计量擦除。文件必须全部被既有上传记录覆盖后才允许取消发布；每个清理键仍需核对不存在其他发布引用。后台成功后设置 purged_at 并释放产物逻辑容量，失败保留容量与重试记录。既有 15 分钟候选年龄和退避/按日复查不变；无恢复按钮，备份按原保留策略独立管理。

旧上传在删除清理完成后返回失败时，重新登记 abandoned 的同一事务清空 purged_at，恢复待清理状态和逻辑容量占用。新 DELETE 方法同时纳入公开 API 的 CORS 方法列表，保留 Bearer 身份要求，不授予匿名或服务凭据删除能力。

### Implementation boundary and defaults

调用链为 `TaskOutputs → TaskOutputController → TaskOutputService → DeploymentAuthorization/RunLedger → MinioTaskOutputStore`；执行侧为 `RuntimeExecutor → DeepAgents official middleware → output publisher → OpenSandbox official command/range-file APIs → private worker output endpoint`。复用现有 S3 bucket 的独立 `task-outputs/v1/` 前缀，不修改 bucket policy、Skill 删除契约、Redis 或 Kafka。凭据仅在已有可信控制面/worker，沙盒内没有对象存储密钥。

当前 DeepAgents StateBackend 官方 FileData 支持字符串及旧版行数组，使用官方转换函数。OpenSandbox 1.1.0 文件 SDK 以 execd 身份读文件，不提供非特权 no-follow 快照；因此只补充该缺失边界：UID 1000 子进程用 openat/no-follow 读取限定输出目录，可信父进程按字节/数量界限接收管道数据并写入 root 私有快照。随后官方文件 SDK 按 1 MiB Range 分段传输，避免扩大已有 8 MiB SDK 响应限制和 32 KiB 模型文本通道。

交付目录 `/workspace/outputs/`；每文件 32 MiB，每任务 32 个不可变内容版本、累计 128 MiB，每租户每环境默认 2 GiB（`platform.outputs.max-tenant-bytes`）。超限明确失败，已归档文件不删除。每次物理上传新建对象键；完整 SHA256 校验及租约复核后发布。15 分钟以上、仍未引用的失败上传才由确切记录回收，已发布对象无 TTL。单实例限 2 个上传、1 个校验下载；下载以最多 32 MiB 临时文件完成校验后发送，避免将大文件全部载入 Java 堆。文本预览 256 KiB，HTML/SVG 等只作为附件下载。公开 API 不接受对象键。

分别验证成功/失败/取消文件、非沙盒输出、同名版本、幂等、并发任务、存储中断恢复、租约失效、路径/链接攻击、超大文件、越权/撤权。真实生成 TXT/CSV/PDF 等文件，完成物理沙盒回收与平台重启后仍能授权下载且 SHA256 一致。自然任务保留期内产物不受监控 3 天保留配置影响。完整备份同时包括对象、索引、任务引用与待发布状态。

### Late upload completion and reconciliation scheduling

未完成 multipart 分片由同一精确键对账清理：`TaskOutputService.reconcile → MinioTaskOutputStore.remove → OutputMultipartCleanup → AWS S3 SDK 2.27.16`。复用 toolkit 已包含的 Apache-2.0 官方 Java SDK，原因是当前 Minio 8.5.10 无法解析展示存储缺少 Initiator 的列表响应。固定 endpoint、显式凭据/region 与 path-style，不使用默认凭据发现。每键每轮最多处理 20 个上传，只中止完全相等的键并检查残余 parts；截断、残余分片或权限/存储错误保留可重试状态，不能标记 cleared。之后删除完整孤立对象；已有每日墓碑复查处理迟到写入。仅扩展任务输出清理，不改变 Skill 存储、bucket policy 或其他服务。

2026-11-06 真实 MySQL/S3 演练复现：上传读取被阻塞，对账将旧 `writing` 记录清理为 `cleared`，随后上传恢复并在 S3 接收对象后进程被强杀；旧对账永久跳过 `cleared`，会留下孤立对象。因此 `cleared` 仅表示最近一次精确键清理成功，不代表旧写入绝不可能迟到。

V1.67 增加 `reconcile_after` 和索引。初次清理仍要求创建超过 15 分钟；待清理/清理中的记录失败后至少间隔 5 分钟再处理，每批最多 20 条，按到期时间排序。已清理但未发布的记录每天复查精确对象键，成功后安排下一次复查；任何已被发布引用的键都不能删除。上传异常若发现对象尚未被引用，将 `writing/reaping/cleared` 重新标记 `abandoned` 并立即安排对账。后台重试不覆盖已有对象键、不重放模型/工具，也不给用户显示下载链接。

这些清理记录随任务数据一起备份；在显式任务删除生命周期完成前不自动删除记录。每日复查带来持续的小规模存储请求，应纳入容量测量；若存储长期离线或待清理队列积压，回收会相应延迟，不承诺固定时限内完成。数据库调用链和凭据边界不变，S3 bucket policy、Skill 输入对象、Redis/Kafka 无改动。
