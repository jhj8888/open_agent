# 沙盒控制器收敛

`ClusterSandboxBinding.quiesce` 接入回收前的停止创建观察。它从控制 API 读取当前持久租约，只有任务终态、租约清理中、executor 终止凭据与已绑定沙盒身份一致时，才调用 `KubernetesSandboxes.quiesce`。使用现有官方 Kubernetes SDK 36.0.3、HTTPS/CA、有限超时和禁用重试的连接，没有修改模型、公共认证、数据库模式或单机执行路径。

核验器重新读取集群/namespace UID 与固定 CR，校验租约标签、普通模式和清理 finalizer，以 CR UID/resourceVersion 条件将 `replicas` 改为 0。旧版本冲突或响应丢失不会在同次调用中重试；下一次调用先重读。只有同一 generation 的 `observedGeneration` 和 `pauseObservedGeneration` 均确认，才返回不含凭据的 `SandboxQuiescence`。无确认时返回待核验，删除中的 CR 或替换资源拒绝。

该观察不是物理停止或所有在途创建已经结束的证明。代码不删除 Pod/CR、不移除 finalizer，也不调用配额释放 API；现有集群准入继续关闭。未知创建、旧控制器在途请求、沙盒 Pod 终止持久凭据及独立回收仍需后续协议。

## 官方控制器行为与版本

固定 OpenSandbox 1.1.0 官方 controller 镜像运行时报告提交 `27bc1b7c87715fb35ec391b7a55658653dbbf611`；其根目录下 319 个 `kubernetes/` 文件与 umbrella 提交 `b00c96b0a89c07fc18409fcfe72d170b4c96c8a7` 完全一致。两份源码归档、镜像 manifest/config 和离线归档摘要见 [来源记录](../../deploy/platform/kata/controller-source.json)，Apache-2.0 原文随包保留。

官方控制器的普通模式仅按副本数创建缺少的 Pod，`replicas: 0` 不主动删除已有 Pod；CR 已标记删除时不再推进普通模式的 generation 确认。因此必须在删除 CR 前完成停止创建的确认步骤。[固定版本源码](https://github.com/opensandbox-group/OpenSandbox/blob/27bc1b7c87715fb35ec391b7a55658653dbbf611/kubernetes/internal/controller/batchsandbox_controller.go)

## 实机验证

`deploy/platform/cluster/run_controller_fixture.py` 复用有明确所有权的零节点 K3s API fixture，启动固定官方 controller。控制器使用专门 ServiceAccount 的短时 token、leader election、非 root UID 65532、只读根目录、无 capabilities、资源上限和私有网络，不使用 admin 身份运行，也不开放公网端口。原始控制器日志仅保存在测试机私有目录；退出清理容器、网络、卷和临时凭据。

实际测试由官方 controller 创建 Pod，后端完成独立绑定及停止创建：确认 generation 2，验证已有 Pod 保留；只在零节点夹具中删除该从未运行的 Pod；重启 controller 并触发 generation 3，确认无 Pod 重建；旧 resourceVersion 修改返回 409，删除中 CR 的 generation 5 在 10 秒观察窗内不再获确认，后端明确拒绝该资源。

业务租约和 executor 终止凭据在此实验中是合成输入，未连接真实业务数据库，也没有 kubelet、租户脚本或 Kata/CNI。K3s 1.36.6 超出固定 OpenSandbox BOM 的 Kubernetes 上限 1.34；不能将此次 API/controller 验证作为正式生产版本兼容或物理隔离验收。详细结果与镜像交付见 [验证记录](cluster-controller-verification-20261105.json)。

## 复现输入

先用 `package_contract.py` 生成当前契约包，在测试机独立目录运行 `verify_contract.py` 安装并验证；不得修改活动 worker 依赖。固定 controller 镜像已导出到来源记录指定的 `/opt/open-agent/kata-controller/` 归档，可校验 SHA256 后用官方 Docker `load` 导入，核对 config ID 和运行用户。没有 Docker/K3s 的目标机器不自动降级执行。

将 `run_controller_fixture.py`、`controller_fixture.py`、`verify_sandbox_resources.py`、`provision_api_fixture.py`、`cleanup_api_fixture.py`、当前 `cluster-contract-manifest.json`、已有 K3s 制品、生成的 `kata-profile-upstream.json`/`kata-profile-documents.json` 放入同一私有验证目录。另从已核验源码准备三个 CRD（batchsandboxes、pools、sandboxsnapshots）及 `kubernetes/config/rbac/role.yaml`（文件名 `controller-upstream-role.yaml`）。RBAC 仅用于该无业务数据的临时集群，不能直接作为生产权限配置。运行包装脚本将串行创建、验证和清理 fixture；一次仅运行一个临时集群，避免占用展示服务资源。
