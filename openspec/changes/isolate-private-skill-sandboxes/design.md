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

## Risks / Trade-offs

[部署组合未验证] → 第一任务固定版本并验证兼容性；[容器逃逸与网络策略缺陷] → 独立内核+最小权限+攻击性验收；[沙盒泄漏] → 租约与周期回收；[启动延迟] → 后续基于实测决定预热，不预承诺并发量。

## Migration Plan

新增 provider 配置与特性开关，测试租户先行；保留接口契约。生产开关关闭时拒绝代码任务，不能回滚为不安全执行；失败制品和审计仍可查询。
