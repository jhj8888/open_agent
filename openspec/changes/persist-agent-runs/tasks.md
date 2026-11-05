# Tasks

本文件中的未勾选项均为待实现工作；规格校验通过不代表功能完成。前置关系见 docs/platform/roadmap.md。

## 1. 持久模型与准入

- [x] 1.1 新增 run/attempt/event/outbox/配额租约迁移与状态转换层；测试重复键、冲突、非法状态迁移及数据库重启。
- [ ] 1.2 实现跨 API 实例的原子准入与队列背压；并发测试证明活动槽位不超上限并提供配额配置说明。
- [ ] 1.3 实现 outbox 发布和 reconciler，模拟提交前后服务崩溃及重复投递，验证不丢任务、不重复启动。

## 2. 执行与恢复

- [ ] 2.1 接入官方 Temporal SDK 和内网 worker，明确每个 activity 的重试/超时/幂等边界；测试服务/worker 断连及心跳丢失。
- [ ] 2.2 迁移一条 Python Agent 执行路径到 run/attempt 上下文，再迁移 Java/工作流入口；分别测试安全重试与未知副作用失败，更新恢复语义文档。
- [ ] 2.3 实现取消、fencing、截止时间和沙盒释放；故障注入验证迟到 worker 不再发起后续操作且终态不反转。

- [x] 2.4 为运行配置的短暂传输错误提供有限重试和安全分类，验证撤销、取消、无效响应及重试上限。

## 3. 查询与联调

- [ ] 3.1 提供持久任务/事件查询和 SSE 重连，覆盖越权、事件过期、重复事件与浏览器断线测试并补充 API 文档。
- [ ] 3.2 进行双租户并发和控制面/调度器/worker 重启演练，保存实际吞吐、排队时间、恢复时间及配额泄漏检查结果。

## 2026-11-04 实现证据

V1.52、RunLedger/RunOutbox 与状态转换层已通过 21 项真实 MySQL 8.4.13 用例，含幂等并发、原子队列/执行槽、取消竞态、过期租约、outbox 重投和独占数据库 SIGKILL/重启；另外 3 项状态/输入契约及 33 项既有租户/Skill 回归通过。详见 `docs/platform/run-ledger-verification-20261104.json` 与 `docs/platform/durable-runs.md`。

1.2 的数据库并发机制、1.3 的消息领取/确认已有证据，但 API 多实例联调、Temporal 发布/补偿及公平调度尚未完成，保持未勾选。数据库重启检查不等于完整 worker、沙盒或生产集群故障验收。

后续增量已加入官方 Temporal SDK 1.35.0、稳定 workflow ID 的 outbox 发布器、独立内部服务身份及租约绑定模型/Skill 读取。22 项 Python 调度/传输测试通过（含真实 Temporal 服务、30 秒心跳丢失、SQLite 服务重启）；MySQL 增至 24 项；Java 模型和内部接口 37 项通过。展示机开发服务已返回 SERVING，监听均为 loopback。见 `docs/platform/temporal-runtime.md` 和对应验证记录。实际 execute/reconcile activity、清理补偿及公共 API/聊天未接通，1.3、2.1–2.3 仍不勾选。

真实执行器增量：RuntimeExecutor 已在展示机用真实 DeepSeek 驱动 DeepAgents 和隔离 Skill，观察到 UID 1000 执行及物理沙盒删除；操作许可、心跳取消和重复清理已有测试。54 项执行/传输/沙盒、9 项 Java 接口与 209 项既有 Python 回归通过，见 `docs/platform/executor-verification-20261104.json`。这次实机准入/journal 是测试实现，完整数据库/Temporal/执行器接线与跨进程强制回收仍未完成，相关任务保持未勾选。

沙盒 journal 已增加数据库原子预留、一次性 creating 状态、取消后仍可补报资源以及独立核验后释放的事务边界。MySQL 检查增至 31 项，含创建意图跨 SIGKILL/重启保存、配额竞争、未知创建持续占额和审计失败回滚。另有 44 项 Java 与 155 项 Python 检查通过，首轮两项 SSH 连接超时的重跑记录保留。见 `docs/platform/sandbox-journal.md`；实际回收器与完整 worker/API 接线仍未完成，不据此勾选 1.3 或 2.3。

终态调度修复：正常 execute 结果也进入 reconcile，并保留旧历史重放兼容。真实 Temporal SDK/开发服务器及控制面传输用例现为 24 项通过、0 跳过，涵盖成功、失败、超时、取消、心跳丢失和服务重启。独立物理回收实现仍是前置工作，任务 2.3 不据此勾选。

## 2026-11-05 执行监督增量

V1.55 和独立 reconciler HTTP 已部署到展示机，包含准入与监督绑定同事务、一次性启动授权、按 supervisor 限定访问、未知创建保留配额、核验材料与两类配额/审计原子提交。80 项 Java 检查通过；随后扩展数据库 SIGKILL/重启用例验证 boot/unit/InvocationID 保留并禁止重复启动，该变更用例单独重跑通过。Python 监督/传输 24 项通过。实机 systemd 非 root 进程及子进程已核实停止，且无 Docker/system bus 权限；使用合成子程序，未重启共享服务器。证据见 `docs/platform/supervision-verification-20261105.json`、`run-supervision.md` 及链接的实机记录。真实 activity、物理沙盒回收、双租户与浏览器完整联调仍未完成，因此 1.3、2.1–2.3 保持未勾选。

真实模型任务接线已完成首轮闭环，见 `docs/platform/model-run-verification-20261105.json`：实际用户 OIDC、HTTP 提交/幂等、MySQL、Temporal、非 root executor、DeepSeek、消息事件及独立 cgroup 回收均联通。新增 lookup/未准入失败事务通过 3 项真实 MySQL 和 10 项内部 HTTP 权限测试。首次两轮启动权限错误已保留失败记录并修复；本阶段不含 Skill 沙盒、双租户故障演练或真实浏览器验收，因此上述整项任务仍未勾选。

## 2026-11-05 集群执行控制协议增量

- [x] V1.62 将 Kubernetes 目标、namespace UID、Pod 名/UID 与同一 attempt 原子绑定，启动授权仅可成功一次；单机和集群监督身份不混用，跨身份/环境与配置漂移拒绝。
- [x] 终止证据先于最终释放持久保存，证据不替换且重放幂等；创建未知或无终止证据继续占额，不能凭 404/删除成功释放。
- [x] 单机与集群通过各自核验后，复用同一个配额、Skill 验证和审计事务；审计失败整体回滚。104 项 Java 检查最终通过，含真实隔离 MySQL 与 SIGKILL/重启，首轮两项测试准备/断言修正后定向复验。
- [x] Python 集群传输核对目标、租约与状态，不重试丢失的启动授权；与单机传输合计 27 项通过。新增检查纳入 CI，远端 CI 结果另行确认。
- [x] 展示 API 更新到 V1.62，更新前保存私有 SQL 备份；真实 Skill 任务和物理回收、旧步骤/文件与 HTTPS 入口复查通过。集群身份保持未配置，6 类实际 HTTP 访问拒绝通过。

实现边界见 `docs/platform/cluster-execution.md`，证据见 `docs/platform/cluster-control-verification-20261105.json`。Pod 元数据测试为合成契约数据；Kubernetes SDK 执行适配器、Pod/finalizer、OpenSandbox/Kata 物理核验、节点/未知创建恢复及生产集群验收仍待完成。专用 chart 继续关闭集群任务准入，原 1.3、2.1–2.3 与系统验收大项保持未勾选。

## 2026-11-05 Kubernetes SDK 过程适配增量

- [x] 接入官方 SDK 36.0.3，固定镜像与租约配置，校验集群/namespace UID；带 scheduling gate 创建，UID 持久确认后才允许调度，不重放未知创建。
- [x] 非零宽限停止与 UID/resourceVersion 前置条件，终止证据提交后再移除自身 finalizer；保留其他 finalizer，替换 Pod、配置注入、PodGC Failed/404 与节点失联不能作为停止证明。
- [x] 集群 executor 从固定 Secret 投射目录读取配置，绑定当前 worker/租约，复用 RuntimeExecutor；拒绝旧主机 Unix socket 沙盒配置，不绕过未实现的集群 admission。
- [x] 298 项兼容回归通过；最后定向检查 Windows 64 通过/1 项 Linux 专属跳过、Linux 65 通过/0 跳过。真实 loopback HTTPS 与投射形式符号链接已核验；Pod/节点状态仍为合成响应。
- [x] 7 个增量 wheel 与锁定摘要逐项核对、保留 LICENSE/NOTICE，在展示机独立目录离线安装验证；活动 worker 依赖前后不变。

详见 `docs/platform/cluster-sdk-verification-20261105.json` 和 `deploy/platform/cluster/README.md`。完整集群服务/镜像、未启动 Pod 的正向证据与未知创建关闭、OpenSandbox/Kata/CNI 及真实集群故障矩阵仍待交付，以上未勾选的整项任务保持未完成。

## 2026-11-05 未启动 Pod 正向证据增量

- [x] V1.63 保存独立 `not_started` 证据，与实际终止证据互斥；先 fencing、核对未绑定节点与删除屏障，证据提交后才移除自身 finalizer。平台门禁可以尚在或已解除。
- [x] 43 项 Java 检查通过，包含 15 项真实隔离 MySQL 检查；未启动证据跨 SIGKILL/重启保留，重放幂等、跨身份拒绝、实际终止冲突拒绝、已授权沙盒创建拒绝及审计失败回滚通过。
- [x] Windows 定向 77 通过/1 项 Linux 专属跳过，Linux 78 通过/0 跳过；活动 worker 依赖未修改。
- [x] 真实 K3s 1.36.6 API 验证有/无门禁两种未绑定节点的回收路径，过期激活拒绝、删除后绑定 409、清空已分配节点 422；fixture 无节点、kubelet、CRI 或 CNI，不能代表物理执行隔离。
- [x] 展示 API 更新到 V1.63，维护前私有 SQL 备份已保存；真实 Skill 任务成功、200 条事件和资源回收核验通过。原任务 58 步、2 个文件及下载摘要保留，公网 TLS/权限/20 项列表详情 usage 一致性通过。验证脚本环境问题从原任务恢复，未重新执行模型或工具。

详见 `docs/platform/cluster-unstarted-pods.md` 与 `docs/platform/cluster-unstarted-verification-20261105.json`。未知创建、已绑定节点但缺少实际终止记录、完整 Temporal 集群服务、Kata/CNI 与生产故障验收保持未完成，原有大项不据此勾选。


## 2026-11-05 环境队列与准入增量

- [x] V1.64 增加环境索引，SQL 在领取前过滤 test/prod；过期领取、取消消息、默认 test 和无效输入检查通过，避免出队后才过滤导致跨环境消息被占用。
- [x] 官方 Temporal 双队列、分派器/传输/活动环境核验接通，共用展示主机最大并发；Linux 63 项检查通过，包含真实服务双队列、定向取消与重投不重复。
- [x] 按环境开放准入和元数据提示；默认 test，错误配置或未开放环境不产生 run/outbox。Java 两轮共 43 项定向检查、Helm/交付 29 项通过。
- [x] 展示 API/worker 已更新，私有 SQL/配置备份与维护记录保留；真实 test Skill 与 prod 模型任务在不同队列完成，有 22.667 秒工作流重叠，资源均释放。旧 58 步、2 文件、Token 汇总及公网 HTTPS 检查通过。

见 `docs/platform/environment-dispatch.md` 与 `environment-dispatch-verification-20261105.json`。展示机两个队列属于同一主机/namespace，不能代替生产基础设施隔离；完整集群服务、未知创建关闭、Kata/CNI/HA、离线冷启动及容量验收继续推进，原有整项任务保持未勾选。
