# 展示环境备份与隔离恢复

本程序服务于当前单机展示部署。使用官方 [Restic 0.19.1](https://github.com/restic/restic/releases/tag/v0.19.1)，BSD-2-Clause；2026-11-05 官方仓库显示约 37.0K stars。加密、去重、仓库锁、完整性校验和恢复均由 Restic 实现。程序仅协调项目已有服务，不实现数据库备份格式或密码算法。生产 PITR、定时备份、异地对象存储/WORM、K8S/HA 与容量验收仍待交付。

## 覆盖与边界

- MySQL 业务库与 Casdoor 身份库，包括租户权限、Skill/Agent 版本、模型凭据密文、任务状态/步骤、文件索引、观测 outbox、审计记录。
- SeaweedFS 对象数据，包括 Skill 包及保留的任务产出文件；模型凭据加密密钥及认证/存储配置一起进入加密备份。
- Temporal、OpenSandbox、MLflow SQLite 数据及授权配置；执行器状态、当前 Java 制品、前端、项目配置、systemd 单元与现有运行时目录。
- 排除构建缓存、验证临时文件、旧 Java 版本、可重新导入的观测 wheel 包及验证专用虚拟环境。Docker 镜像、宿主机 Docker 配置及平台目录以外的 Python 运行时不在此数据快照内，恢复主机必须预先导入已锁定的镜像与运行时。备份不是完整裸机离线安装包。
- 没有 TTL 清理任务产物；备份程序不删除任务、文件或旧快照。容量与保留政策需另行配置。

调用路径：运维命令 → 所有权/任务空闲检查 → 停止 API 并再次检查 → 停止本项目写入服务 → Restic 加密备份 → 恢复服务 → 全仓库 `check --read-data`。恢复路径：Restic → 新目录 → 独立 MySQL/S3/Casdoor/API → 真实 JWT 授权读取。共享上游业务实现不改动；`DemoSession` 只增加验证用可选地址参数，默认行为保持一致。

## 安装与备份

使用授权管理员在 Linux 主机操作。将 `recovery.py`、官方 release 的 `restic_0.19.1_linux_amd64.bz2` 和本目录许可证复制到同一私有目录；安装时许可证文件名为 `restic-LICENSE`。导入文件只来自受信任制品渠道，不在运行时下载。程序验证压缩包、二进制、许可证 SHA-256：

| 文件 | SHA-256 |
|---|---|
| 官方 bz2 | `f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c` |
| Linux amd64 二进制 | `20d4142678d0d95ec11a4759def1b73fd9190abc9ca19e4b62d067c0b387e639` |
| LICENSE | `6f08a01a9fab5b24e139a09f15cc24a73087c7bc09e3bacf099fdf2d767bf897` |

```sh
python3 recovery.py install --companions /path/to/private/import
python3 /opt/open-agent/recovery/recovery.py backup
python3 /opt/open-agent/recovery/recovery.py check
```

仓库为 `/opt/open-agent-backup/repository`，口令独立保存于 root 专用 `password` 文件，目录 0700、文件 0600。`maintenance.json` 在第一次停止前持久写入原状态，所有操作持有已有项目安装锁。活动任务或未释放尝试会拒绝备份；API 停止后的二次检查封闭新任务准入竞态，不为备份取消业务任务。只操作归属标签/标记匹配的五个容器及列明的项目单元。已有网络防护单元保持运行。

正常失败或 SIGTERM 在 `finally` 恢复原服务。若进程被 SIGKILL、主机断电或恢复依赖暂不可用，保留维护记录，排除原因后运行：

```sh
python3 /opt/open-agent/recovery/recovery.py resume
```

在维护恢复完成前，新备份拒绝运行。不要清除维护记录来绕过检查。备份/校验非零退出不算成功；检查 `backup-report.json` 的快照 ID 与验证时间。失败日志只含稳定事件/错误类型，不输出 subprocess 原始诊断或用户内容。

## 隔离恢复演练

V1.69 起，演练会从当前受信业务库读取删除记录，合并到旧快照副本。新增 `deletion_overlay.py` 须与 `verify_restore.py` 一起安装。恢复先验证原数据库指纹，再使用当前已部署且摘要匹配的 API 制品执行迁移；不能启动不识别删除记录的旧制品并对外提供服务。迁移副本无公开端口、任务准入关闭，合并前停止其 API，整个过程不启动 worker。

`deletion_overlay` 使用官方 MySQL 批处理客户端，不使用 `--force`、自动重连或自制数据库协议。清单字段在生成 SQL 前严格校验，动态文本采用 UTF-8 十六进制字面量；单个会话事务同时写入删除 fence、墓碑、恢复审计与产物取消发布。租户/环境/任务/部署/提交者/请求及执行摘要不匹配、任务仍运行、资源未释放、清理键缺失或审计失败时拒绝整批。不会把源库已清理状态直接复制为完成：恢复出的对象仍须由原精确键对账删除。

快照之后创建又删除的任务没有旧账本可恢复，V1.69 的 `oa_run_deletion_fence` 独立保存最小请求身份和删除证据。它没有租户/部署/任务外键，不能授予权限；原提交者使用旧键收到 `run_deleted`，其他身份得到不可访问，新请求键仍按正常权限和准入规则处理。后续导出同时读取普通删除记录和 fence，避免再次恢复时丢失这些拒绝记录。清单可能包含用户 UID 和请求键，仅保存在 root 私有恢复目录并随加密备份保护，不放入 Git。

**演练不是生产切换。** 当前线上服务继续写入，报告记录 `captured_at`，仅证明该删除截点已合并；实际切换必须先停止旧入口和写入者，取得最后一份受信删除清单，再合并并验收后开放新入口。源库已丢失且没有独立备份/PITR 的删除不能被推断出来，不能承诺零 RPO。每次最多合并 50,000 条，超过限制明确失败，不截断记录。原快照保持不可变；导入失败保留私有诊断目录，清理临时服务后再重做演练。

事务和 CLI 行为依据 MySQL 官方[批处理选项](https://dev.mysql.com/doc/refman/8.4/en/mysql-command-options.html)及[隐式提交说明](https://dev.mysql.com/doc/refman/8.4/en/implicit-commit.html)。会话未提供 Context7，使用上述官方来源核对；业务写入事务内无持久 DDL。

从仓库部署 `verify_restore.py`、`deletion_overlay.py` 与 `recovery.py`。验证器复用 `../control` 下的 `DemoSession` 和 `verify_task_outputs.raw`；展示机安装将这些辅助模块放在同一私有目录。演练不要求模型 API，也不启动 worker。

```sh
python3 /opt/open-agent/recovery/verify_restore.py
# 断电/SIGKILL 后先恢复演练前的观测服务并清理已登记的临时资源：
python3 /opt/open-agent/recovery/verify_restore.py --cleanup
```

恢复到 `/opt/open-agent-backup/drills/<随机ID>`，从不覆盖线上数据。先全仓库校验并 `restore --verify`，再对两个 MySQL 库的有序逻辑数据摘要做完全比对，对四个 SQLite 数据库做 `PRAGMA integrity_check`。用独立且 `--internal` 的 Docker 网络、只读容器根目录、无宿主机端口、关闭任务准入、空服务密钥启动只读验收副本。经真实登录读取文件与步骤，并验证另一租户返回 404。

共享主机内存较小，演练暂时停止本项目 MLflow 与 exporter 释放约 500 MiB；线上 API 与执行器保持在线，期间观测由持久 outbox 缓冲。演练结束恢复观测服务。演练维护记录在停止前落盘，清理失败可重试。失败恢复目录保留供管理员检查，不自动递归删除明文数据；这些目录 root 专用，不加入 Git。

测试机 firewalld 需给新建 Docker bridge 设置 Docker 官方约定的 `docker` zone。演练只添加该临时 bridge 的运行时关联，并增加带唯一注释的精确规则：阻断副本向宿主机的新连接及跨网桥转发。结束仅移除本次规则/关联，不刷新防火墙、不修改其他网桥。首次演练因未关联该 zone，认证副本无法连接数据库；失败后所有临时容器已清理、原观测服务恢复，修正后完整演练通过。[Docker 官方说明](https://docs.docker.com/engine/network/packet-filtering-firewalls/)

## 2026-11-05 实测

2026-11-06 增量：同一旧加密快照经当前 V1.69 API 迁移，并合并后续删除清单后再次通过隔离恢复。最终脚本恢复至接口验收 217.717 秒，12 个文件与 197 个步骤保持完整、4 个 SQLite 库完整性通过；1 个快照之后才创建/删除的任务保留 fence。匹配旧任务的墓碑/清理恢复由独立真实 MySQL/S3 合成快照演练覆盖。恢复工具已安装，临时资源清理和原服务恢复已核验，见[删除恢复证据](../../../docs/platform/deletion-restore-verification-20261106.json)。这是读取截点的隔离演练，不包含停源后最终切换，不替代生产 RPO/RTO 验收。以下保留原始备份及初次演练记录。

数据截点为 **11:17:27（Asia/Shanghai）**。加密快照 `d85a7e206a408892d6eab9e0bca8ba602250814af377cbdba60904cc832473fc`：44,255 个文件、2,489,767,934 字节源数据；维护时间 **132.115 秒**。恢复并校验文件 **64.713 秒**，从开始恢复到真实接口验收完成 **143.188 秒**。

两个业务/身份库逻辑摘要完全一致；12 个产出文件通过真实 API 下载并校验大小和 SHA-256，197 个相关步骤可分页读取，跨租户文件/步骤请求均拒绝。MLflow 恢复数据库包含 4 条 trace、202 个 span；Temporal/OpenSandbox/MLflow/auth 四库完整性均通过。演练期间未重放任务，已完成任务在快照截点丢失数为 0。这里的 143 秒只包含当前主机已有制品条件下的数据恢复/接口核验，不包含跨机下载、重装镜像/操作系统、入口证书/网关切换或恢复执行器；不能当作生产 RTO 承诺。未配置定时备份，因此不承诺生产 RPO。

完整加密仓库另导出约 951 MB 的 tar，保留在服务器。考虑展示机出网带宽，`export_data.py` 用 Restic `rewrite --include` 从同一冷快照生成独立的数据/配置投影，再用官方 `copy` 复制到另一个加密仓库；不使用 `--forget` 或 `prune`，原完整快照保留。数据副本约 16 MB，包含完整业务/身份数据库、Skill/任务文件、观测状态及恢复所需密钥，已在服务器再次恢复并验证；它需要单独导入锁定的软件镜像与运行时。命令为 `python3 /opt/open-agent/recovery/export_data.py`，结果/范围在 `data-export-report.json`。

该数据副本复制到用户授权的本地工作站，摘要核验后才登记成功。完整仓库的冗余跨机传输已停止，部分下载文件标记为 `.partial`，不算成功备份。解密口令在本地用 Windows 当前用户 DPAPI 单独托管并验证往返，不写明文文件、不进 Git；它只能由同一 Windows 用户/设备解密，不能替代企业密码库或独立灾备密钥流程。[Microsoft 官方说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/export-clixml?view=powershell-7.5)

证据见 [backup-recovery-verification-20261105.json](../../../docs/platform/backup-recovery-verification-20261105.json)。Restic 的[完整仓库校验](https://restic.readthedocs.io/en/v0.19.1/045_working_with_repos.html)和[恢复说明](https://restic.readthedocs.io/en/v0.19.1/050_restore.html)是操作参考。完整平台的离线冷启动、生产 Kubernetes/Kata、容量与故障矩阵继续在 OpenSpec 跟踪。
