# Kubernetes 沙盒独立核验

后续已加入实际官方 controller 与后端停止创建确认，见[控制器收敛](cluster-controller-quiescence.md)。下文记录最初独立核验增量，其手工 Pod/API 验证边界和证据保持不变。

本增量实现 `KubernetesSandboxes` 与 `ClusterSandboxBinding`，接到 V1.66 的持久准备/绑定接口。独立核验器从 Kubernetes 实际资源读取身份和配置，不能采用 worker 自报的镜像、安全选项或资源 UID。它是生产准入/回收服务的组件，尚未开放租户执行入口，也不构成物理停止证明。

## 调用与配置

调用方向：受信 reconciler → `ClusterSandboxBinding.prepare/bind` → 官方 Kubernetes SDK 36.0.3 → `ClusterSupervisorClient` → 已部署的 Java 许可/配额事务。复用监督器验证过的 HTTPS API client、企业 CA、无重试与有限超时。原始 JSON 通过官方 SDK 的 `_preload_content=False` 读取，单响应限制 8 MiB；避免类型模型丢弃未知字段后错误接受 Pod。没有新增 HTTP 协议实现、依赖或数据库迁移。

`KataPolicy` 由部署操作者提供：控制面 namespace 名称和 UID、RuntimeClass UID/handler、私有镜像拉取 Secret 名称、固定 execd 镜像和沙盒数量上限。沙盒目标仍为 `ClusterSandboxTarget`，必须与 executor namespace 分离；不能由租户上传的配置覆盖。固定允许项对应 OpenSandbox 1.1.0 commit `b00c96b0a89c07fc18409fcfe72d170b4c96c8a7` 和 `deploy/platform/kata/prepare_profile.py` 生成的配置；初始化命令与实际官方镜像输出的 SHA256 比对，测试夹具保留该输出和合成凭据。

测试夹具中的生成配置及初始化命令来源于 [OpenSandbox 固定源码](https://github.com/alibaba/OpenSandbox/tree/b00c96b0a89c07fc18409fcfe72d170b4c96c8a7)，Copyright 2025 The OpenSandbox Authors，遵循 Apache-2.0；本仓库保留相同许可证全文，夹具不进入分发的运行镜像。官方 SDK wheel 中的版权与许可原文随固定依赖保留。

准备先检查当前租约、实际 executor Pod 和沙盒基础配置，再由控制面的最后事务重新检查许可并预留。绑定检查已创建的 CR/Pod，提交不可变身份凭据。取消或终态后的合法观察只能用于记录已发生的创建；回复丢失后重读持久记录，不能重新创建。重复核验仍检查当前资源配置，但返回首次凭据，不覆盖最初 resourceVersion。SDK/API 异常只有固定分类，不输出响应正文；execd token 只在内存比较，不进入日志、证据 JSON 或其摘要。

## 核验内容

- cluster、executor/sandbox/control namespace UID 和环境；固定 RuntimeClass UID/handler；账号禁用自动挂载且没有 token Secret；ResourceQuota 的实际 hard/used 已初始化。
- 命名空间中必须只有批准的 NetworkPolicy：默认拒绝出口，仅同环境 sandbox server 的 44772/TCP 可入站。网络策略是叠加关系，存在额外策略或未完整列表均拒绝，见 [Kubernetes 官方说明](https://kubernetes.io/docs/concepts/services-networking/network-policies/)。
- 单副本普通 BatchSandbox、完整租约标签、固定 Skill 镜像、截止时间、CR/Pod finalizer；实际 Pod 唯一 ownerReference 指向该 CR，CR/Pod UID 与 executor UID 不同。
- 批准的容器、初始化命令、只读根文件系统、capabilities、挂载、DNS、节点选择、无 token 和每次尝试的 execd 鉴权。额外 sidecar/临时容器、宿主挂载或未知 Pod 字段均拒绝。
- 全命名空间有界 Pod 列表检测同租约或同 CR 属主的额外 Pod，即使额外 Pod 没有标签；CR 列表按租约检查唯一性。分页未完整不视为资源不存在。

只兼容已明确审查的 API 默认值，包括调度器、有限 NoExecute 容忍、镜像拉取/终止消息设置，以及固定 CRD 的 `taskResourcePolicyWhenCompleted: Retain`。该初始配置不接受其他 RuntimeClass overhead/scheduling、运行时注解或 CNI 注解；目标集群需要这些字段时，必须在同一固定配置下评审并扩展验收，不能删去整体检查。

## 验证与边界

真实零节点 K3s API 中创建固定官方 CRD、配额、账号、网络策略、RuntimeClass、CR 和 Pending Pod，核验实际 UID/resourceVersion；重复观察保留首次凭据。实际修改出口规则、增加网络策略、改变租约标签、增加无标签但同属主 Pod，四种情况均拒绝；还原后重新核验成功。官方 CRD 自动增加的 Retain 默认值曾使首轮检查失败，查阅固定 CRD 后显式兼容，并新增反例测试。验证集群及其专用网络、卷和 admin 凭据均已清理。

本轮 Pod 是夹具按固定控制器规则创建，executor 租约为合成输入；未运行 OpenSandbox controller、kubelet、租户脚本或 Kata。K3s 1.36.6 不在所锁定 OpenSandbox BOM 的 Kubernetes 上限 1.34 内，此结果只证明 API/defaulting 行为，不能作为正式版本组合验收。RuntimeClass 声明的含义参见 [官方文档](https://kubernetes.io/docs/concepts/containers/runtime-class/)，读取配置本身不证明独立内核或 CNI 的实际效果。

203 项 Linux overlay、261 项最终镜像检查和 16 项打包检查通过；镜像非 root、只读、无网络，155 个锁定依赖一致。新的模块已进入测试服务器上的约 706 MB 离线镜像归档，活动单机展示仍使用已有 systemd/gVisor 链路。原任务的 58 步、2 个产物下载摘要、缓存/新增 Token 与公网授权重新核验通过。

HTTPS admission 接线、controller 创建收敛、沙盒停止证据、独立物理回收，以及真实 Kata/CNI/断网/容量验收仍待完成；现有集群准入保持关闭。详见 [本轮证据](cluster-sandbox-verification-20261105.json)。
