# 任务产物存储故障演练

2026-11-06（北京时间）在展示服务器的独立临时 S3 上，使用当前已部署 JAR 中的 `MinioTaskOutputStore` 和打包的官方 Minio SDK 验证。生产执行类没有修改，也没有替换或重启展示站对象存储。完整证据见 [JSON](output-store-recovery-verification-20261106.json)。

验证调用为 `OutputStoreRecoveryProbe → MinioTaskOutputStore → Minio Java SDK → SeaweedFS`。数据、凭据和卷均为新建测试资源，没有连接业务数据库，也没有调用模型或重放用户任务。

| 阶段 | 实际检查 |
| --- | --- |
| prepare | 创建临时 bucket，写入保留样本并读取核对内容、大小和 SHA256 |
| outage | 对临时 S3 执行 SIGKILL；无服务监听时，上传、校验下载、删除均返回 `output_storage_unavailable` |
| candidate-present | 重启 S3 后启动新上传进程；S3 接收全部字节后，将输入流停在上传函数的最终校验处；独立 Java 进程成功读取候选对象和保留样本 |
| post-write-client-sigkill | 强制终止上述上传 JVM，退出码为 137；上传函数没有成功返回 |
| recover | 新进程读取遗留候选对象，按精确键重复删除；错误摘要上传被拒绝，候选对象可清理；随后正确上传及下载成功，保留样本仍一致 |

该检查证明真实 S3 服务重启后的持久性、存储层错误映射、上传进程在对象提交后的中断边界，以及精确清理的幂等性。它不包含 `TaskOutputService` 的业务数据库状态迁移/自动对账，不是 multipart 传输中途断流、真实 API 请求被杀、生产 Kata 或完整存储容量验收。因此 OpenSpec `retain-task-artifacts` 的复合任务 1.2 保持未关闭。

## 可重复执行

先使用 Java 21 编译器和已验证 API JAR 构建独立 probe；编译仅使用 JAR 内打包依赖，不访问 Maven 或公网。输出必须使用不存在的文件名，构建输入保留在同名 `.build` 目录。

```powershell
python deploy/platform/control/package_output_store_probe.py `
  --javac '<Java 21 编译器路径>' `
  --platform-jar console/backend/hub/target/hub-server.jar `
  --platform-sha256 75927e061935b5e05c5e77b871c464f6fd210b6ea372ae6163c2d4f741ff9c45 `
  --output .local/output-store-probe.jar
```

将生成的 `output-store-probe.jar` 放在展示机验证脚本同目录，并交付 `deploy/platform/control` 的 Python 模块；以已授权的展示机管理员执行 `verify_output_store_recovery.py`。脚本仅使用已缓存且固定 digest 的 JRE/SeaweedFS 镜像（`--pull never`）。S3 为 UID 65532、Java 为 UID 10001，均只读根目录、无 capabilities、无外网/宿主端口；Java 仅在 S3 启动时共享该临时容器的网络命名空间。

每个运行使用随机标签和卷，按实际容器 ID/所有权核验后清理。日志保留在服务器私有验证目录，临时凭据删除；成功证据写入 `/opt/open-agent/control/output-store-recovery-verification.json`。异常不会被记为成功，正常探针超时也会按自有标签回收容器。

首次搭建曾因 Docker 对空卷的复制行为改变目录属主而启动失败。独立实测为：初始化后 `65532:65532`，默认挂载 SeaweedFS 后 `1000:1000`；使用 `volume-nocopy` 后仍为 `65532:65532`。修正仅作用于临时验证卷。该选项行为见 [Docker 官方卷文档](https://docs.docker.com/engine/storage/volumes/#mounting-a-volume-over-existing-data)。失败演练和最终成功演练的临时卷/容器均已清理。
