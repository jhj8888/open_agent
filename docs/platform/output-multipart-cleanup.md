# 未完成分片上传清理

任务产物上传 JVM 在首个 5 MiB 分片写入后被强杀，旧版对账只执行 DeleteObject，仍留下未完成 multipart 上传。已用真实 MySQL、SeaweedFS 和已部署业务类复现，见 [旧版证据](output-multipart-baseline-20261106.json)。这部分数据未形成可下载文件，不能把普通对象删除成功等同于分片清理成功。

## 实现与影响

调用链：`TaskOutputService.reconcile → MinioTaskOutputStore.remove → OutputMultipartCleanup → AWS S3 SDK 2.27.16`。复用 toolkit 已包含的官方 SDK（Apache-2.0），没有新增依赖或进程。上传和下载继续使用 Minio 8.5.10；其分片列表 XML 解析要求 Initiator，而当前 SeaweedFS 的响应不含该字段，因此该列表/中止操作使用 AWS 官方 SDK，不自行解析 XML。

数据库对账先锁定产物、核对没有发布引用并撤销该上传的发布资格，再传递精确键。清理只处理完全相等的 `task-outputs/v1/<run>/<output>/<upload>`，前缀仅用于缩小查询。每键每轮最多处理 20 个未完成上传；中止后查询 ListParts，再查询上传列表。分页截断、残余分片、权限或存储错误均保留 `reaping`，沿用至少 5 分钟退避；已成功处理的键仍按日复查。完整孤立对象在分片检查通过后才删除。

固定 endpoint、显式凭据/region（默认 us-east-1）与 path-style；API 总超时 15 秒、单次尝试 5 秒。只有 404 + `NoSuchUpload` 被视为幂等，不把 NoSuchBucket、403 或任意 404 当成成功。日志继续只记录固定错误和上传标识。没有更改 bucket policy、Skill 输入存储、Redis/Kafka 或沙盒凭据。受限 S3 账号需具备该前缀的 ListBucketMultipartUploads、AbortMultipartUpload 和 ListMultipartUploadParts 权限，否则按可重试失败处理。

## 验证范围

`OutputMultipartCleanupTest` 覆盖精确键过滤、非法键、重复中止、其他 404、残余 parts、分页截断、迟到上传和空列表。真实演练 `verify_output_reconciliation.py` 继续执行已有对象提交后强杀、存储中断恢复及迟到对象回收，再增加 12 MiB 上传的首个 5 MiB 分片检查、上传进程 SIGKILL、真实业务对账，以及独立 boto3 列表确认。

29 项 Java 检查通过：新增 8 项清理测试、16 项内部接口权限、5 项 Spring 检查。测试夹具最初两次编译因 SDK 异常 builder 返回基类而失败，修正显式类型转换后通过；没有忽略失败用例。11 项 OpenSpec 校验、离线 API 打包和 14 阶段真实 MySQL/S3 演练通过。制品 `69fdf45266037926853a22e40a293666682fbd8b82576a7c5adbc23d096cbded`，证据见 [验证记录](output-multipart-verification-20261106.json)。其中 `final_recovery_cleared_uploads=4` 只计迟到完整对象恢复阶段，不包含随后新增的 multipart 候选。全部夹具容器、卷及凭据已清理。

临时数据使用合成身份，不替代 HTTP 权限、生产容量或实际 Kata/CNI 验收。时间戳在测试数据库中推进，没有实际等待 15 分钟/24 小时。存储并发写入与中止之间的全部竞态、任务显式删除和完整权限矩阵仍需继续验收，不能把本演练称为完整产物交付完成。

## 展示部署

2026-11-06 已更新展示 API，schema 保持 V1.67，无新增迁移。维护前确认空闲并保存私有 SQL 备份，维护 41.879 秒；失败回退沿用既有升级流程。部署后原任务 58 步、2 个产物及下载摘要、20 页缓存明细、四个自然统计周期与 21 项公网列表/详情用量核验通过，未重放模型或工具。API 身份伪造与未授权访问拒绝；前端静态资源摘要保持一致，本轮未重新做浏览器交互验收。生产集群准入仍关闭。

AWS 明确说明，中止时仍在传输的分片可能需要再次中止并通过 ListParts 检查；本次强杀发生在已上传首个分片后、下一段读取被阻塞时，不覆盖所有网络传输竞态。参见 [AbortMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_AbortMultipartUpload.html)、[ListMultipartUploads](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListMultipartUploads.html) 和 [Java SDK 超时配置](https://docs.aws.amazon.com/sdk-for-java/latest/developer-guide/timeouts.html)。Context7 在当前环境未提供，SDK 接口以官方文档和锁定制品编译/实测核验。
