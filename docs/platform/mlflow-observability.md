# 私有 MLflow 观测后端

2026-11-05 已部署到共享展示机。平台任务详情继续通过当前租户权限展示执行过程；独立 MLflow 在任务结束、执行租约物理释放后异步接收完整快照。后端不提供绕过平台授权的用户入口。

## 版本与许可

选用 [MLflow 3.18.0](https://github.com/mlflow/mlflow/releases/tag/v3.18.0)，核验时官方仓库约 28.7K stars，主许可 Apache-2.0。使用官方 OTel SDK / OTLP HTTP exporter 1.45.1（Apache-2.0）。[官方 OTLP 接收文档](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/ingest/)与固定版本源码确认 HTTP protobuf 接收、SQL 存储及按 trace/span ID 更新；失败整批拒绝。

独立依赖由 `deploy/platform/observability/pyproject.toml`、`uv.lock` 和带哈希的 `requirements.lock` 固定，不升级任务 worker 环境。Linux CPython 3.11 实际安装 97 个 wheel，197,911,777 字节；离线压缩包 195,151,795 字节，SHA256 `978c785eede8a733c09134e7eba22de35fed95c668ba7e325c5b9e2d3e5287d3`。依赖清单见同目录 `dependency-inventory.json`，保留 wheel 内的许可文件。Flask-Cors 6.0.5 wheel 没有单独许可证文件，另附官方固定标签的 MIT LICENSE，部署脚本校验其 SHA256 后安装。Certifi 使用 MPL-2.0；科学计算 wheel 的附带运行库按各自 notices 处理，不能将 MLflow 的 Apache-2.0 覆盖到全部依赖。

## 数据与故障边界

`TaskObservationService → MySQL 步骤 + oa_observation_outbox → 专用导出器 → 官方 OTLP exporter → MLflow`。V1.61 增加独立 outbox，既有步骤迁移为待投递。每次真实新增回调原子递增 revision，重复上报不增加版本；领取与分页绑定认证身份、claim、revision 和数据库租约，旧确认不能覆盖迟到更新。

每次只领取一个已结束且已释放资源的 attempt；最多 300 秒领取租约，读取/投递预算 240 秒，每批 32 spans。失败按 5 秒起步、最多 300 秒退避，进程死亡由租约到期恢复。再次投递使用相同 trace/span ID，不触发模型或工具执行。32 MiB 投影上限、4096 步及既有 4 MiB 正文配额均明确保留；超限会留下失败类别，不静默丢弃。

模型直接 Token 仅放入模型 span，父节点不重复填聚合 Token。未完成步骤以真实开始时间的事件标记，不构造结束时间或已知用量。额外根 span 明确表示执行租约生命周期，不算成额外模型/业务步骤。增量输入、实际 think、工具结果、截断/缺失标记沿用持久化的脱敏副本。任务文件继续由平台 S3 保存，不受 MLflow trace 清理影响。

投递成功前平台正常提供步骤查询。Prometheus 新增 `open_agent_observations_pending`、`open_agent_observations_failed`，按 test/prod 固定维度统计；pending 包含尚未终结的待导出 attempt。systemd 日志仅记录投递 ID、revision、span 数和稳定错误类别。

## 展示部署

| 服务 | 边界 |
|---|---|
| `open-agent-mlflow.service` | 127.0.0.1:18750，Basic auth，默认 NO_PERMISSIONS，独立 SQLite 数据目录；1 worker，768 MiB / 1 CPU 上限 |
| `open-agent-trace-exporter.service` | 独立系统用户；仅持有 trace_exporter 控制面身份与指定 experiment 的 EDIT 权限；256 MiB / 0.5 CPU 上限 |
| 网络 | 两个服务均 `IPAddressDeny=any`、仅放行 localhost；不发布 Caddy 路由，不启用遥测、模型网关或 MLflow job execution |
| 文件 | `/opt/open-agent/observability` 保存固定运行时/源包与 root 或专用用户可读配置；数据库位于 `/var/lib/open-agent-mlflow` |

凭据不进入 Git、浏览器、任务进程或沙盒。导出身份不能进入执行、调度、回收或模型运行配置接口；MLflow 默认不向普通平台用户开放。生产需要 TLS/内网 NetworkPolicy、独立 SQL 后端、备份与容量验收，不能把这台共享机的 SQLite 单实例当作高可用部署。

离线交付流程：在有网的 Linux 构建机使用官方 pip `download --require-hashes --only-binary=:all: --no-deps -r requirements.lock` 获取 wheel，运行 `bundle_wheels.py <wheel目录>`，与 requirements、manifest、许可文件一起转入目标机；`prepare_runtime.py` 默认只导入并校验离线包，再以 uv `--offline --no-index --require-hashes` 安装。显式 `--connected` 仅用于有网构建。`package_source.py` 只打包导出器和有界控制客户端；随后使用 `provision_backend.py`、`provision_exporter.py` 安装服务。部署脚本拒绝覆盖不属于本项目的同名 unit。

重跑 backend 部署会重启该服务以应用锁定运行时，暂时积压的观测自动恢复。回退到不认识 trace_exporter 角色的旧 API 时，先停止 exporter，并恢复旧 API 服务身份配置；追加的数据库表与文件保留，不能通过删除 outbox 回退数据。

## 已通过验收

真实任务 `423ee867-ab18-4135-84c5-aef911dea25a` 在 MLflow 停服期间运行成功，生成并保留一份资料。52 个步骤映射为 53 spans（含明确的生命周期根），5 次模型调用，输入 16,534 / 输出 898，共 17,432 Token，与平台记录一致。正文逐项与原步骤匹配；投递尝试 4 次，业务执行仍仅 1 次。重复导出后 span 数与总 Token 不变。

第二租户读取任务步骤被拒绝；MLflow 导出用户不是管理员，不能读取另一个未授权 experiment。匿名后端查询返回 401。对同一可达的非 loopback 监听地址，未施加网络策略的探测前后均可连接，施加相同 systemd 网络策略的探测被阻断，避免把原本不可达的外部地址误当成隔离证据。后端在禁外联策略下重启，trace 和 Token 保持一致。实测 backend 512,122,880 字节、exporter 31,645,696 字节，合计约 519 MiB。

本增量通过 27 项 Python、4 项真实 MySQL、18 项 Java/API/安全检查；首次安全测试缺少旧 Hub 的用户服务 mock，补齐夹具后通过。真实校验脚本修正了 MLflow 的 `tr-` ID 和纯文本 403 包装，以及 cgroup 丢包表现为超时的断言。没有为修正验证脚本再次执行业务任务。证据见 [观测后端验收](mlflow-observability-verification-20261105.json)。官方依赖当前有 protobuf/Starlette 的未来弃用提示；固定版本运行及契约通过，升级需重验。

完整平台的离线冷启动、生产 Kata/K8S、强杀/压缩故障矩阵、备份恢复和容量验证仍按总计划推进。
