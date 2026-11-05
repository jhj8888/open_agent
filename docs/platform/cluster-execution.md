# Kubernetes 执行接入

当前已实现集群执行的控制面账本、独立服务接口、Python 严格传输，以及官方 Kubernetes SDK 的 Pod 过程适配、scheduling gate/finalizer 与受信 executor 入口。复用既有 RunLedger、配额、终态、SandboxJournal、Skill 验证与审计。尚未接通完整集群 Temporal 服务、OpenSandbox/Kata admission/物理核验和目标节点验收；专用 Helm 继续关闭新任务准入。单机展示仍使用已验收的 systemd/OpenSandbox/gVisor 执行链。

## 调用路径与身份

目标调用路径为 Temporal → 集群监督器 → 控制面账本 / Kubernetes 官方客户端 → 每次租约的可信 executor Pod → 模型接口 / 独立不可信代码沙盒。监督器到控制面的协议与 SDK 过程调用均已实现，尚未对真实 Kubernetes API/kubelet 完成验收。executor Pod 与租户代码沙盒是不同资源，不能把可信 executor 的容器停止当成沙盒停止。

仅 `reconciler` 服务身份能访问 `/internal/platform/reconcile/cluster/*`；普通 JWT、worker、dispatcher、observer 均不能提交证明。`PLATFORM_CLUSTER_RECONCILERS` 默认为 `{}`，未配置身份不得领取集群任务。其键为监督身份 ID，值为下表中的部署目标；凭据仍从已有 `PLATFORM_SERVICE_KEYS` 注入，不放在请求、Pod 标签或 Temporal 载荷中。

| 字段 | 来源与约束 |
|---|---|
| `workerId` | 已配置的独立 worker 服务身份，不与单机或另一集群监督器共用 |
| `environment` | `test` 或 `prod`，所有领取/查询请求必须匹配 |
| `clusterId` | 目标集群的受信标识；后续适配器需核验 Kubernetes 系统命名空间 UID，不能只信 URL 名称 |
| `namespace` / `namespaceUid` | 执行资源命名空间名称及实际 UID；跨环境不得共用相同集群/namespace UID |
| `executorImage` | 经审核的 `repository@sha256:...`，不能使用可变 tag |

目标信息随租约持久保存。已存在尝试期间不能用同一监督身份更换集群、namespace UID、worker 或镜像；配置不一致明确拒绝操作，应保留原身份/目标直至清理完成，再登记新身份。控制面中没有 Kubernetes token、kubeconfig 或集群管理员凭据。

## 状态与回收

```mermaid
stateDiagram-v2
    [*] --> prepared: 准入与资源绑定同一事务
    prepared --> launching: 单次启动授权
    launching --> observed: 保存唯一 Pod UID
    observed --> terminated: 保存不可替换的容器终止证据
    observed --> not_started: 保存未绑定节点及删除屏障证据
    terminated --> released: Pod 与沙盒回收证明及审计同一事务
    not_started --> released: 未启动证据与独立回收证明同一事务
    prepared --> released: 从未授权启动且任务已终态
```

`oa_run_cluster_execution` 通过 lease ID 引用既有 attempt，不产生第二套任务状态。任务成功/失败/取消与资源释放分开；取消或截止时间先 fencing，终态不反转。启动响应丢失时不能重试 `begin` 或据此重新创建 executor，需查询持久绑定并进入独立回收。

记录终止时要求相同 Pod UID、resourceVersion、节点名称/UID、`Succeeded`/`Failed` Pod 状态、`executor` 容器 ID、实际退出码和有时区的起止时间，以及独立证据摘要。resourceVersion 按不透明字符串保存，不转换为整数比较。终止记录在删除 Pod 前提交，并且只允许完全一致的重放；删除后、控制面重启后仍可查。最终证明必须匹配集群、namespace UID、Pod 名/UID、终止记录和 sandbox ID。

Kubernetes 的强制删除可能在节点进程仍运行时移除 API 对象，因此删除成功、404、NodeLost、空列表或 TTL 不能单独证明已停止。[Kubernetes 删除说明](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_delete/)。后续执行适配器需要使用 finalizer 保留终止前的资源记录，且不能在节点失联时绕过核验。[Finalizers](https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/)。当前未知启动和未知沙盒创建仍保留配额，尚未交付自动处置所有未知创建的机制。

单机和集群各自通过核验后，调用同一个事务内 `RunVerifiedRelease`：释放执行/沙盒配额、登记真实成功任务的同版本 Skill 验证与写入租户审计。审计或证明持久化失败必须回滚所有释放，不能部分释放或生成通过标记。

## 验证边界

2026-11-05：104 项 Java 检查最终通过（首轮两项测试数据/断言修正后定向复验），其中 76 项使用隔离 MySQL；27 项 Python 传输/单机兼容检查通过。数据库 SIGKILL/重启后，集群目标、Pod UID、终止记录仍可读取并幂等释放。展示 API 已应用 V1.62，未配置集群执行身份；实际 HTTP 的 6 类访问拒绝通过。升级后真实 DeepSeek + Skill 任务成功、180 条事件、脚本输出及物理回收证明通过；旧任务 58 个步骤、2 个文件摘要与 HTTPS 工作台复查通过。详见[验证记录](cluster-control-verification-20261105.json)。

MySQL/API 检查覆盖并发领取、一次性启动、跨身份/环境拒绝、资源 UID 固定、终止证据重放、未知状态保留配额、审计失败回滚、单机兼容和数据库重启。测试中的 Pod 元数据是合成契约数据，不能据此宣称真实 Pod 已终止。

前一轮 Python 传输检查覆盖响应身份/状态一致性、分页、重复回收确认、秘密字段拒绝与启动授权不重试，不包含模型请求或租户代码。

后续 SDK 增量使用 `kubernetes==36.0.3`，新 Pod 先带 scheduling gate，配置和 UID 持久绑定成功后才允许调度；非零宽限停止、UID/resourceVersion CAS、终止证据先提交、只移除自身 finalizer。创建/绑定回复丢失不重放创建，恢复不自动激活；未知或尚未运行的 Pod 继续占额。非 root 集群入口接入已有执行器，但明确拒绝旧单机 Unix socket 沙盒配置。

兼容回归 298 项通过；最终 SDK/入口定向检查在 Windows 为 64 通过、1 项 Linux 专属跳过，展示服务器 Linux 为 65 通过、0 跳过。Linux 使用校验过的增量 wheel 在专用 vendor 目录离线安装，活动 worker 的 aiohttp/aiosignal/yarl 版本前后相同。Pod/节点数据是合成 API 响应，真实本机 TLS 测试证明创建回复丢失时不自动重试、不跟随重定向；文件系统测试核验 Secret 投射形式的符号链接边界。不能将这些证据视为真实调度、容器停止、网络隔离或完整离线交付。见 [SDK 接入与依赖](../../deploy/platform/cluster/README.md)及[SDK 验证记录](cluster-sdk-verification-20261105.json)。

迁移 V1.62 仅新增集群绑定表，保留现有 systemd 监督字段与接口；回退应用时保留新表和已有记录。控制面就绪检查要求新表存在，旧任务及文件不清理。应用镜像交付包的版本/摘要仍以各自验收清单为准，不能将此前控制面镜像自动视为包含本增量。

V1.63 后续增量加入从未绑定节点的独立 `not_started` 证据。已有实际 K3s 1.36.6 API 的有/无调度门禁验证、删除后绑定拒绝及节点绑定不可清空验证；43 项 Java 和 Linux 78 项定向检查通过。展示 API 已应用 V1.63，集群任务准入仍未配置。详见[未启动 Pod 的协议与边界](cluster-unstarted-pods.md)；上文 V1.62/SDK 的记录保留其当时范围，不代表完整 Kubernetes/Kata 生产验收已完成。
