# 独立控制面展示部署

2026-11-05，测试服务器已运行真实 MySQL、SeaweedFS、Casdoor 和独立 Java 控制面。通过真实授权码 + PKCE 登录、JWT 验证、租户创建、对象读写及服务重启检查。证据见 [部署验证记录](control-plane-verification-20261105.json)。公网预览仍明确展示模拟数据；持久 worker、可信资源回收和正式浏览器入口接通前，`PLATFORM_RUN_ADMISSION_ENABLED=false`。

## 模块与调用链

`deploy/platform/control` 仅管理 `/opt/open-agent/control`、带 `io.open-agent.owner=open_agent.control.demo.v1` 标签的容器和专属 Docker 网络。调用链为 Casdoor → JWT → PlatformApplication → 原有租户/模型/Skill/run 服务 → 专属数据库与私有 S3 桶。身份数据库、业务数据库使用不同账号。没有修改原 Hub 配置、旧 MinIO 客户端、已有 Dify 容器或 80 端口服务。

数据服务所在网络 `open-agent-control-data` 为 Docker internal 网络，IPv6 关闭，没有发布宿主机端口。SeaweedFS 的 master、volume、filer 只绑定容器 loopback；仅 S3 服务绑定私有网络。关闭 SeaweedFS telemetry、IAM、Iceberg、Lance 和匿名访问。控制面只持有单个 Skill 桶的 Read/Write/List 权限，不持有创建其他桶或设置公开 ACL 的权限。

真实 worker 接线增量为 API 单独增加 `open-agent-control-api` 网络，并仅映射宿主机 `127.0.0.1:18082`；数据库、S3 和身份服务继续只连接 internal 网络。调用链变为宿主机 supervisor/executor → loopback API → 原有数据网络。采用 [Docker 官方多网络配置](https://docs.docker.com/reference/cli/docker/container/run/#connect-a-container-to-a-network---network)，不改变已有应用的网络或端口。共享 firewalld 辅助函数只增加可选的本项目网络参数，默认行为保持数据桥接处理；每次仍核对归属，只添加对应桥接接口，不做全局 reload。展示 API 的模型检查仅允许管理员已授权的 DeepSeek HTTPS origin；企业离线部署应改为内网模型接口，不能沿用公网展示允许列表。

展示服务器 Docker 为 24.0.9，不支持在一次 create 请求中带多个 network endpoint。安装器因此对该 API 使用官方 `docker create → docker network connect → docker start` 顺序，两个网络连接完成后才启动 Java；其他单网络数据服务保留原启动路径，不升级共享 Docker daemon。已存在本项目容器在启动前也会核对并补齐附加网络。

## 固定制品与启动

镜像版本、摘要见 [镜像锁](../../deploy/platform/control/images.lock.json)。2026-11-05 通过 Docker Hub 官方 Registry API 核对四个镜像的 manifest 摘要，与下载镜像一致。安装器仅使用已导入的 digest，`--pull never`，不在线安装依赖。

将 `deploy/platform/control/*.py` 一同复制到管理员可读的目录，按顺序执行：

```sh
python3 provision_data.py
python3 provision_identity.py
# 将经过摘要核对的 platform.jar 放入 /opt/open-agent/control/releases/<sha256>/
python3 provision_api.py
/opt/open-agent/agent-worker/venv/bin/python verify_data.py
python3 verify_identity_api.py
```

JAR 的启动类、构建与固定 SHA-256 见 [控制面运行说明](control-plane-runtime.md)。`verify_data.py` 使用既有 worker 环境锁定的官方 Boto3 1.40.53。两个验证脚本会重启本项目相关服务，适用于当前独立展示环境，不能直接当作无中断生产检查。

安装器保留随机凭据，写入 root 管理目录下的权限 0600 文件；挂载到服务的配置交给对应非 root UID 读取。并发安装通过主机文件锁拒绝。配置采用临时文件 + fsync + 原子替换；既有凭据不自动重新生成。容器、目录或网络归属不符时停止。API 更新可以替换本项目旧容器，持久数据目录不删除。

| 服务 | 内存上限 | CPU 上限 | 运行身份 |
| --- | --- | --- | --- |
| MySQL 8.4.13 | 640 MiB | 1.25 | 999 |
| SeaweedFS 4.52 | 384 MiB | 1 | 65532 |
| Casdoor 4.25.0 | 384 MiB | 1 | 1000 |
| Java 21 控制面 | 896 MiB | 1.5 | 65531 |

容器均使用只读根文件系统、移除 capabilities、禁止获得新权限、PID 限制和有界日志。单次空闲观测合计约 775 MiB，不能作为并发容量承诺。

## 数据库与身份注意事项

上游 V1.17–V1.19 中有硬编码的 `astron_console` 库名，因此专属 MySQL 实例内使用该业务库名，账号仍为 `open_agent`。首次使用 `open_agent` 库名时迁移在 V1.17 被拒绝；未修改历史迁移或伪造成功记录，改为正确名称的全新库后，原始 53 个迁移完整执行到 V1.54。失败库仅保留用于排障，不接应用。Flyway 仍验证校验和，禁止 clean 和自动 baseline。当前沿用上游 Flyway 10.21.0，它对 MySQL 8.4 输出版本支持提示；本次冷启动与重启实测通过，版本升级仍需独立验证。

Casdoor 使用官方 init-data merge 机制配置本项目组织、应用和用户；自生成 RSA 签名密钥，关闭公开注册与外部身份 Provider。管理员初始密码随机生成，不使用上游示例密码。普通 demo 用户通过真实 Spring JWT 校验后，由主机安装脚本一次性登记为平台管理员；已有 bootstrap 不重新启用被停用账号，也不恢复已撤销的管理员授权。

当前 issuer 为私有服务地址，仅用于集成验证。公开入口上线时需要配置稳定 issuer、精确回调地址和浏览器静态资源。Casdoor 静态资源地址已改为内部地址，但完整图片资源和浏览器断网流程尚未验收。模型网络策略与执行接入也尚待启用；现有数据网络不提供公网出口。

## 实测与恢复边界

已验证 S3 1 MiB 文件读写、列表、删除、重启保留；跨桶创建/读取和公开 ACL 被拒绝，桶列表只返回有权限的桶。真实 OIDC PKCE 登录不需要前端 client secret，授权码重放被拒绝，伪造 JWT/身份头被拒绝。通过控制面创建演示租户并核验测试环境管理员权限；重启身份、数据库和 API 后签名公钥和租户均保留。

服务间最初被测试机 firewalld 拒绝，安装器按 [Docker 官方 firewalld 集成](https://docs.docker.com/engine/network/packet-filtering-firewalls/#integration-with-firewalld) 将本项目桥接接口关联到 docker zone，保留其他接口和现有防火墙规则，不做全局 reload。Docker internal 的隔离规则及独立沙盒防火墙继续存在。

这不是完整备份恢复、生产高可用、K8S 网络隔离或离线验收。生产需要内网身份地址、TLS、备份恢复演练、SBOM/第三方制品许可清单、监控告警和完整执行链。MySQL 当前仅通过私有 Docker 网络使用非 TLS 连接，不能照搬为跨主机生产配置。

Context7 工具本次不可用，使用 [Casdoor 初始化文档](https://casdoor.ai/docs/deployment/data-initialization/)、[官方 v4.25.0 源码](https://github.com/casdoor/casdoor/tree/v4.25.0)、[SeaweedFS S3 凭据文档](https://github.com/seaweedfs/seaweedfs/wiki/S3-Credentials)、[MySQL 官方镜像说明](https://hub.docker.com/_/mysql)与固定镜像的 CLI help 核对配置。
