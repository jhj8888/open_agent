# Design

## Context

core/workflow/service/chat_service.py 使用 asyncio.create_task；event_registry.py 状态依赖 Redis TTL。仓库有 Celery 基础设施但不能据此证明主执行链已使用或具备节点级恢复。DeepAgents 使用 StateBackend，未配置持久 checkpointer。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/durable-agent-runs/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 控制面持有 runs、attempts、events、tenant_quota、admission_leases 和 outbox。事务写入 run 和 outbox 后返回 run_id，异步启动 Temporal workflow，以 run_id 作稳定 workflow ID 防重复。事务唯一键为 tenant/environment/deployment/idempotency_key，同时保存请求摘要；相同 key 不同内容返回冲突。
2. 状态机 queued→running→succeeded/failed/timed_out；queued/running→cancel_requested→cancelled。取消与成功竞争通过版本化条件更新决定，终态不反转；取消已终态任务返回当前终态。SSE 不驱动执行，客户端断开后任务继续，除非显式取消。
3. Temporal 使用官方 Python SDK 管理执行和心跳，activity 边界包住可恢复步骤；外部 I/O 不放在 deterministic workflow 中。业务事件使用唯一序号和 outbox 重投去重。禁止声称把整个旧 HTTP 调用包装一次就自动得到节点级 checkpoint。
4. 首期对旧 workflow 支持持久跟踪和明确的 attempt 级重试；无法安全恢复的进程崩溃标记 failed(reason=worker_lost 或 outcome_unknown)。需要重试时创建新 attempt，非幂等工具的未知结果进入人工核对/失败，不自动重复。后续才按节点接入 checkpointer，未实现前不宣称精确断点续跑。
5. 任务准入采用数据库原子配额预留，worker 开始前再次确认租约；公平调度按租户轮转，任务在队列等待不占执行槽位，队列长度也有上限。租约带 fencing token，超时释放前必须阻止旧 worker 继续副作用。task 和 sandbox 配额分别计数，不能仅用某个进程的 Semaphore。
6. 运行记录包含 deployment/release/config 摘要、tenant/environment、主体、截止时间、幂等键、trace_id 和 usage。事件保留有期限；客户端重连用 Last-Event-ID，超出保留范围明确报 event_history_expired 并引导查询状态。
7. Temporal 成为新增基础设施依赖，但不替换所有现有 Kafka/Celery 功能。对接任务相关共享层前记录调用链与影响，先灰度迁移一条 Agent 路径，再扩至另一条。
8. 调度载荷只传 tenant/environment/run_id 和安全状态。受信 worker 以独立服务身份及数据库租约按需取得固定模型/Skill 版本，密钥和用户输入不写入 Temporal 参数或结果。租约校验同时检查 worker 身份、取消、截止时间及版本撤销；对象读取后再复查一次。所有 worker 模型/沙盒操作仍须独立校验许可，取得一次配置不代表整个任务持续获准。
9. execute 正常返回任何终态也必须进入 reconcile；终态提交不等于物理资源已释放。reconcile 读取数据库最终状态，独立确认 worker 与沙盒停止后才能释放配额，迟到取消不覆盖既有成功。旧 Temporal 历史通过 patch 标记保持可重放；独立补偿扫描负责旧历史及超过 activity 重试窗口的清理。
10. 单机真实执行由受信 supervisor 调用官方 systemd CLI 为每次租约启动独立非 root unit。数据库在准入同一事务登记 supervisor/worker/host boot/unit，启动授权仅可领取一次。executor 只有 worker 身份，不持有回收身份或 Docker/systemd 管理权。新增独立 reconciler 服务角色仅访问自己绑定的尝试，先 fencing，再凭内核进程组、主机启动身份及 Docker 实体核验结果调用控制面事务释放；worker 报告仍不能释放配额。启动或沙盒创建处于未知状态且没有观察到实体时保留配额，不从 TTL/空列表推断无在途操作。已知唯一沙盒 ID 可按一次性创建记录独立核验并删除；核验材料与最终释放审计持久保存。生产 Kubernetes 使用 Pod/Job UID 和 RuntimeClass 的对应核验适配，不能沿用单机 systemd 证据。
11. 实际 activity 只接收 RunRef，通过受信查询恢复自己绑定的尝试（包括已释放记录），不依赖仅包含未释放记录的扫描推断清理成功。执行 activity 失联且任务尚未准入时，以账本锁检查 queued 且 attempt_no=0 后结束；与领取竞争时不得覆盖已开始的执行。非 root executor 通过每次尝试专属凭据访问主机 Unix socket 隔离核验代理；代理不执行租户命令，只核验固定运行时与按完整 lease 标签绑定的容器。host supervisor 在活动取消和后台补偿中共用同一清理路径，先保存核验材料，再提交释放证明。

## Risks / Trade-offs

### Kubernetes SDK 执行实体适配

未启动 Pod 的回收采用独立正向证据：监督器先 fencing、核对同 UID 的 Pod 尚未绑定节点且无任何容器状态，再用 UID/resourceVersion 条件请求删除；重新读取未绑定节点、无容器状态、无 nominatedNodeName 且已设置 deletionTimestamp 的资源（gate 可以尚在或已移除），持久登记 `not_started` 记录，最后移除自身 finalizer。删除请求构成资源版本屏障，旧版本的在途激活不能通过 CAS；Kubernetes 不允许已绑定节点被清空，也拒绝对删除中的 Pod 进行绑定，故此组合证明它没有进入节点执行；恢复读取持久证据，不能仅凭 Pending/404/空列表或时间推断从未执行。账本对 `not_started` 和实际容器 termination 使用互斥记录，但复用同一最终释放事务。没有观察到任何 Pod 的未知创建仍保留配额；本增量不把这类未知状态改为已释放。

集群监督器通过官方 Kubernetes Python SDK 操作 executor Pod，控制面 API 仍是任务与资源绑定的唯一账本。SDK 使用独立 TLS 配置、有限请求超时、禁止自动重试创建；不读取用户上传的 kubeconfig。每次操作验证 kube-system UID 与执行 namespace UID，Pod 以完整租约摘要、唯一名称、固定镜像和专用无 token ServiceAccount 绑定。受信 executor 与不可信 Skill 沙盒分离。

新建 Pod 带平台 finalizer 和 scheduling gate、非 root/只读根文件系统、能力全删、Seccomp、有限临时盘/CPU/内存，禁止额外容器、自动 token 与宿主挂载。先核对配置、将 UID 写入账本，再通过 UID/resourceVersion 条件移除 scheduling gate；创建/绑定回复丢失时不能先运行 executor。恢复只查询同名资源并核对 UID，不重试创建、不自动激活。停止请求携带 UID 与 resourceVersion 前置条件、非零宽限时间；只有 kubelet 已报告唯一 executor 容器终止、节点身份可核验，且终止证据已持久提交后，才移除平台自身的 finalizer。其他 finalizer 保留，404、节点失联、PodGC 的 Failed 状态或删除请求成功均不能单独成为终止证据。此适配不替代 OpenSandbox/Kata 的独立物理清理，完整集群服务与真实节点验收仍需接通。从未绑定节点的 Pod 可使用上述独立未启动证据；已绑定但镜像拉取失败等缺少容器终止记录的情况仍保留配额，不能凭等待时间释放。未启动证据与实际终止证据互斥，兼容字段 terminationId 在 never_started_pod 证明中引用 notStarted.id。

集群执行使用独立 `oa_run_cluster_execution` 资源绑定，复用同一 RunLedger、租约、终态、SandboxJournal 与最终释放事务，不把 Pod 名或 UID 填进 boot/unit/cgroup 字段。调用链为 Temporal → 集群监督器 → 独立 reconciler HTTP → 账本 → Kubernetes 官方客户端 → 单次 executor Pod → 既有模型/Skill 执行器。集群身份、namespace UID、环境、worker 身份和固定镜像由管理员配置，不能来自租户请求；systemd 与集群监督身份不得混用。一次性 launch 授权在创建 Pod 前提交，Pod UID 只绑定一次；重启读取持久绑定，不重放创建或工具。记录可信监督器观察到的容器终止状态并持久化后才允许删除 Pod、提交独立回收证明和释放配额。删除请求成功、API 404、TTL、NodeLost、空列表和未经核验的强删均不能代替物理终止证据；未知创建继续占额。已有单机协议和部署保持兼容，集群 chart 在执行适配器及目标集群验证完成前仍关闭准入。

集群控制面分增量交付：先实现资源/终止证据账本和严格身份接口，随后接官方 Kubernetes 客户端、finalizer 生命周期、Pod 运行配置和 OpenSandbox/Kata 核验。前一增量的 MySQL/API 检查只证明账本契约，不代表 Pod、Kata、CNI 或完整执行已验收。共同的 Skill 验证、资源释放和审计逻辑提取为同一个事务内实现，单机与集群都必须先通过各自的核验门禁，不能增加第二条 worker 自行释放通道。

2026-11-05 运行配置读取诊断：展示机两次在模型/沙盒调用前出现 CONTROL_UNAVAILABLE；现有日志抹除了网络异常类别。仅在受信日志记录固定分类（连接、读取、总时限、传输、响应格式），不记录异常文本、URL、响应或凭据。只读 runtime 读取允许一次传输故障重试；重试前必须续约成功，HTTP 拒绝、响应格式错误和取消均不得重试。该机制不重放模型、工具、沙盒创建或任意有副作用操作，不把缓解视为已经证明根因。

[跨数据库/调度器一致性] → outbox+唯一 ID+reconciler；[重复副作用] → operation_id、下游幂等与未知结果失败；[配额泄漏/旧 worker 继续执行] → fencing+心跳+回收；[Temporal 运维成本] → 限定任务用途并纳入离线备份恢复。

## Migration Plan

新增表及独立 worker，测试租户先启用持久模式；只读展示旧运行但不伪造历史事件。回滚先停止接新任务并排空新 workflow，保留查询服务和数据库；禁止删除进行中的持久记录。
