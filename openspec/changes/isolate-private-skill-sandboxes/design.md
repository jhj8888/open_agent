# Design

## Context

SkillSandboxRunner 当前固定 E2BSandboxProvider，AsyncSandbox.create/run/kill 每次执行生命周期；配置 normalizeProvider 固定 E2B。Pyodide/Deno code node 是另一条受限路径，不等于支持任意 Shell 的安全沙盒。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/skill-sandbox-isolation/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 在现有 provider 抽象处新增 OpenSandbox 官方 SDK 适配，服务部署在内网 CPU 节点；生产使用通过验证的 Kata RuntimeClass。OpenSandbox 管生命周期，Kata 提供独立内核边界，两者职责不同。不选择普通 runc 作为不可信生产代码隔离方案。
2. OpenSandbox 版本与 Kata/K8S/CNI 组合先做兼容性门禁，固定版本后测试创建、执行、取消、回收和出口控制。缺少硬件虚拟化或 RuntimeClass 时任务失败关闭；不自动回落到宿主执行、普通容器或公网 E2B。
3. 首期一个 run 的一个 attempt 一个沙盒；同一 Agent 的不同并发任务不共享可写目录，父子任务也不共享默认写卷。不提供跨任务常驻 Shell 会话，工作区需要持久化时作为带权限的产物导出和显式导入。多 Agent 并发受此同样约束，不等同于新增自主任务委派。
4. 禁用 privileged、hostPath、hostPID/IPC/network、Docker socket、服务账号 token 和无边界设备挂载；只读镜像/制品，限制 CPU、内存、PID、临时盘、输出大小和绝对截止时间。控制面凭据与模型密钥不能进入沙盒。只通过可信资源通道注入有限文件。
5. 网络默认拒绝，仅按审批的内部目标、端口和期限放行；网络策略由可信控制器安装，隔离 CNI/egress 与 DNS 解析要在真实节点验证，不能只依赖应用层 allow_internet_access 字段。阻断元数据、K8S API、控制面数据库、其他租户和公网。
6. executor 记录 sandbox_id/run_id/attempt/lease 和 owner；TTL 到期控制器回收，启动失败也释放配额。取消先停止执行再销毁，产物先验证路径/类型/大小再存入租户前缀，不能信任代码自报成功。
7. 保留现有 skill_resource_security 检查。旧 E2B provider 仅显式开发配置可选，离线生产不允许。workflow 的有限 Pyodide 路径保留已有限制，所有可执行不可信本机代码的路径必须进入隔离后端。
8. 无 KVM/K8S 的单机展示使用显式 `gvisor-demo` profile，禁止该 profile 承接 prod。固定 OpenSandbox SDK/server 1.1.0 与 gVisor 20261102.0。Docker provider 不支持 secureAccess，使用 execd 官方每次尝试独立认证 token；该 token 不得进入不可信子进程。gVisor 不兼容官方 egress 侧车，必须验证独立网桥/宿主规则/DNS，不能将空 networkPolicy 当作默认拒绝的证据。具体差异、调用链和未完成接线见 docs/platform/private-sandboxes.md。

### Kubernetes 模板与 execd 鉴权接线

锁定 OpenSandbox 1.1.0 commit `b00c96b0a89c07fc18409fcfe72d170b4c96c8a7`。官方 `secureAccess` 保护 gateway 入口，不能代替沙盒内部 loopback 的 execd 鉴权。所有 profile 均使用每次尝试独立的 `EXECD_ACCESS_TOKEN`，SDK 携带对应头，UID 1000 子命令继续清空该值。保留现有 gateway/egress 模式；新增显式 `cluster-cni` 模式要求 HTTPS、server proxy、Kata，使用可信准入确认的默认拒绝 CNI，关闭需要额外 gateway 的 secureAccess 和带 NET_ADMIN 的 egress 侧车，不允许静默降级。

`deploy/platform/kata/prepare_profile.py` 生成无密钥、固定摘要的官方 server TOML、BatchSandbox 模板、独立 test/prod namespace、无 token ServiceAccount、ResourceQuota 与仅服务端代理可入站的 NetworkPolicy。官方模板合并会替换容器/init/volumes 列表；主容器安全上下文与额外挂载通过上游专门合并保留，不能凭模板原文宣称最终 Pod 安全。需在固定官方 server 镜像中验证实际输出，并持久保留 CR/Pod 的独立清理 finalizer。仅有生成配置和 API 测试不等于运行时/CNI/PID/磁盘/硬件虚拟化验收；execd installer 的固定受信镜像仍需在实际 Kata 节点验证。生产沙盒准入、持久 Pod/CR UID 绑定与回收服务尚未接通前，集群 Skill 入口继续关闭。

### 持久资源预留与独立回收

独立核验器使用固定官方 Kubernetes SDK，读取原始 JSON 以保留模型尚未识别的字段；不重新实现 HTTP 或鉴权。核验调用链为 `ClusterSandboxBinding` → `KubernetesSandboxes` → 同一已校验 API client → `ClusterSupervisorClient` 的准备/绑定事务。核验目标 namespace UID、RuntimeClass UID/handler、无 token ServiceAccount、已初始化配额及命名空间内唯一网络策略；核验固定版本实际生成的 CR/Pod、完整租约标签、镜像、ownerReference、finalizer 和 Pod 权限/挂载/环境，拒绝同租约多个资源及不完整分页。execd token 仅用于内存比较，不能进入凭据摘要或日志。重复观察仍重新检查配置，但保留首次持久 UID/resourceVersion；取消后补记不授予执行许可。该组件只提供资源身份观察，不提供租户代码入口、物理运行/停止证明或配额释放；Kata/CNI 实机与完整回收服务仍是后续准入前置。该增量不修改模型、公共认证、单机 provider 或数据库模式。

集群绑定的调用方向为独立核验服务 → reconciler 专属 `sandbox-prepare` → `RunClusterSupervision` → 同一任务许可锁内的 `RunSandboxJournal.reserve` 和沙盒目标快照；创建后经 `sandbox-resource` 原子绑定官方 BatchSandbox UID 与其唯一 Pod UID。沙盒 namespace UID/RuntimeClass 只来自 `PLATFORM_CLUSTER_SANDBOXES`，与受信 executor namespace 分离；运行镜像仍必须匹配已发布 Skill。V1.66 保存目标及资源观察，模型、Redis、Kafka 和单机接口不变。取消后允许记录已经发生的创建，不恢复执行许可；绑定后不允许同名不同 UID 替换。没有独立停止证据前，已绑定资源不能通过旧的 sandboxId-only 释放证明释放配额。此持久绑定是准入和回收服务的前置，不能单独开启集群 Skill。

沙盒创建之前在同一租户/环境锁下预留 `max_sandboxes`，记录一次性的创建意图；SDK 操作在事务之外执行。创建确认丢失不重试创建，所有未知/清理中记录继续占用配额。worker 的创建/删除观察绑定认证身份及完整租约，取消后允许补报资源但不恢复执行许可。只有独立核验执行进程终止、物理沙盒不存在且没有在途创建后，受信回收器才能在同一事务释放沙盒/执行槽并写审计；worker 没有配额释放接口。详见 `docs/platform/sandbox-journal.md`。

单机展示的独立适配器由 root supervisor 持有，仅通过 Unix socket 对外提供 before/after 两个核验操作。每个尝试使用不同 capability，读取控制面绑定与当前许可后才检查固定 gVisor、镜像 digest、Docker 网络、双栈宿主规则和完整租约标签；不接受任意命令。root 只执行固定的展示镜像账号初始化，生产镜像在构建时预置账号。清理先停止 executor cgroup，再按全部租约标签和 Docker 完整容器 ID 核验/删除。固定 OpenSandbox 1.1.0 在端口冲突时可能使用同一 sandbox UUID 重建 Docker 容器，因此只有 SDK 完整返回后持久记录的 ID 可直接进入物理清理；未知 creating 即使发现实体也需先独立确认创建服务已停止原在途创建，不能仅凭观察补记 ID 后释放。若版本、标签、数量或在途创建无法确认则失败关闭，不以 SDK 空列表或 TTL 代替证明。

## Risks / Trade-offs

2026-11-05 展示机网络修复：宿主临时端口范围 32768–60999 与沙盒发布端口 46000–46499 重叠。原 INPUT 非 lo 的全状态 DROP 会拦截宿主主动连接私有容器时的 SYN-ACK/回包，已通过绑定源端口 45937 成功、46137 超时复现。发布端口规则增加 conntrack 非 ESTABLISHED,RELATED 条件；所有沙盒 FORWARD 和沙盒主动访问宿主的禁止规则保留。迁移先安装 IPv4/IPv6 新规则，再精确删除带项目 comment 的旧规则（含重复项和 firewalld runtime/permanent direct 规则），不 reload 共享防火墙。主机准入核验、初始安装与独立验证同步修改。验收必须覆盖重叠源端口回包成功以及其他 bridge 发起新连接仍被拒绝。

[部署组合未验证] → 第一任务固定版本并验证兼容性；[容器逃逸与网络策略缺陷] → 独立内核+最小权限+攻击性验收；[沙盒泄漏] → 租约与周期回收；[启动延迟] → 后续基于实测决定预热，不预承诺并发量。

## Migration Plan

新增 provider 配置与特性开关，测试租户先行；保留接口契约。生产开关关闭时拒绝代码任务，不能回滚为不安全执行；失败制品和审计仍可查询。
