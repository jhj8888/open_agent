# Kubernetes executor SDK 接入

本目录包含在独立 Linux 目录验证 SDK/执行器入口的工具，不是完整的生产部署包。运行镜像、Temporal 集群服务、OpenSandbox/Kata admission/独立回收、RBAC/NetworkPolicy 接线和真实节点验收仍需完成；已有 Helm 保持任务准入关闭。

`core/agent/durable/cluster_process.py` 使用官方 `kubernetes==36.0.3` 创建/读取/停止 Pod、提取容器终止证据并操作自身 finalizer。`cluster_lifecycle.py` 连接已有账本 HTTP，确保先记录终止、再移除 finalizer。`cluster_child.py` 复用已有 RuntimeExecutor，仅从固定 Secret 挂载读取 worker 配置并注入当前租约；租户输入与模型凭据仍从受租约保护的运行接口读取。当前集群入口明确拒绝旧 Unix socket 沙盒配置，未接通生产沙盒前不得启用 Skill 集群执行。

固定 Pod 配置使用独立 ServiceAccount 且禁用 token 挂载、非 root UID 10001、只读根文件系统、能力全删、RuntimeDefault Seccomp、有限资源、独立 `/tmp`。创建和恢复会核对 kube-system/执行 namespace UID、Pod UID、租约摘要与完整安全配置。仅接受已知 API 默认值，额外容器/卷/环境变量/权限或镜像变动会拒绝；目标集群若有注入 webhook，必须在集成阶段明确协调，不能绕过核验。

新 Pod 带 scheduling gate，完成核验并将 UID 写入账本后，才通过带 UID/resourceVersion 条件的 JSON Patch 移除门禁。采用 [Kubernetes 1.30 起稳定的 Pod Scheduling Readiness](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-scheduling-readiness/)，目标集群需要该特性且后续必须实测；当前没有声称兼容用户尚未提供版本的全部集群。恢复不自动激活、不重试创建。未调度/镜像拉取失败等缺少容器终止记录的路径仍占额，尚需实现从未运行的正向证据与创建关闭协议。

## 依赖与离线契约包

官方 SDK 仓库核验为 7.7k Stars / Apache-2.0。依据 [v36.0.3 源码与许可](https://github.com/kubernetes-client/python/tree/v36.0.3)，使用 `CoreV1Api`、官方模型、in-cluster 配置加载与 TLS/token 刷新机制。SDK 请求不自动重试；有限超时不等于服务端请求已停止，未知启动仍占用配额。

`uv sync --directory core/agent --frozen --extra cluster` 安装固定依赖。SDK 要求 aiohttp ≥ 3.13.5，故更新锁中的 aiohttp 3.14.5、aiosignal 1.4.0、yarl 1.25.1，新增 kubernetes 36.0.3、durationpy 0.11、oauthlib 4.0.1、requests-oauthlib 2.0.0。这改变了新环境的锁，不自动升级既有部署。现有 frozen worker 环境保持自己的发布快照。

新增/更新的 wheel 逐个与 `uv.lock` SHA256 比对，许可全文保留在 wheel 中，见 [制品与依赖清单](contract-artifacts.json)。实际 LICENSE 文件核验：aiohttp Apache-2.0 AND MIT、aiosignal/yarl/Kubernetes Apache-2.0、durationpy MIT、oauthlib BSD-3-Clause、requests-oauthlib ISC。最后一项 wheel classifier 写 BSD，实际 LICENSE 为 ISC，以文件为准。遵守原有版权/许可/NOTICE 随包分发要求。

`package_contract.py` 从 `.local/cluster-wheels` 和显式源码清单生成带摘要的契约归档；它只包含 7 个增量 wheel，依赖展示机既有 Python 3.11 环境，不能用于全新机器完整离线安装。`verify_contract.py` 在 `/opt/open-agent/cluster-contracts/<sha256>` 的专用 vendor 目录离线安装增量，验证安装前后活动 worker 依赖不变，再运行官方 SDK 序列化、故障恢复、真实本机 HTTPS 和 Linux Secret 投射符号链接检查。

这些检查的 Pod、节点和终止状态是合成 API 数据。本机 HTTPS 只证明实际 SDK 不重试丢失的创建回复、不跟随重定向，不证明 kubelet/CRI 已停止进程，也不证明 CNI/Kata 隔离。
