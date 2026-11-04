# 私有沙盒实施说明

当前增量是官方 OpenSandbox 适配器、DeepAgents 原生沙盒后端以及单机展示环境的部署和验证脚本。持久任务调度、控制面租约/配额和生产 K8S 适配尚在接入；不能把下面的单机验证当作企业离线集群验收。

## 调用链与影响

目标调用链是：Java 授权后的 deployment/run 快照 → Python worker 的固定 Skill manifest/file 客户端 → `platform_runtime.artifacts` 校验 → `OpenSandboxProvider` → 官方 OpenSandbox 1.1.0 SDK/server → 隔离运行时。DeepAgents 的 `BaseSandbox` 原生文件与执行工具通过 `OpenSandboxBackend` 进入该 attempt 的同一个沙盒。模型请求和凭据留在可信 worker。

新适配器不改变旧 Redis、Kafka、MinIO 或认证接口，也不接受租户提交的 sandbox_id、控制端点或资源下载 URL。`ReleaseSource`、`LeaseJournal` 和 `IsolationAdmission` 必须由可信 worker 注入，不能由聊天参数构造。当前验证脚本中的 SQLite journal 和 Docker 检查仅用于验证；它们不提供生产的原子配额、fencing 或跨服务身份。

`DeepAgentsRunner.bind_attempt()` 是可信 worker 的接入点，绑定能力使用 Pydantic PrivateAttr，不接受请求字段注入且不序列化。绑定后启用 `/skills/` 原生发现与执行工具，保留同一预算和工具轨迹，仍禁用隐式 `task` 委派；流结束、模型错误和取消都会进入沙盒销毁路径。HTTP 端点还不能创建这种绑定，需持久任务变更完成授权快照与 worker 接线。

旧 E2B 入口现在默认拒绝，只有 `OPEN_AGENT_DEPLOYMENT_MODE=development` 与 `OPEN_AGENT_ALLOW_PUBLIC_E2B=true` 同时设置才可使用。该门禁在取得外部沙盒凭据或调用公网 SDK 之前执行。正式私有环境缺少沙盒时失败关闭。

## 固定依赖与实机差异

OpenSandbox Python SDK 固定为 1.1.0；服务端、execd、controller 等官方镜像摘要保存在 `deploy/platform/opensandbox/upstream-1.1.0.yaml`，来自官方 release asset，原文件 SHA-256 为 `63f9da3ed067fb422f60baae517ba9287f2229820fbbbf65330316a19d7defcd`。SDK 由 `core/agent/uv.lock` 固定。此次没有可用 Context7 工具，核对依据为官方发布源码及安装后的 SDK 源码。

生产目标仍为 Kata + K8S RuntimeClass + CNI 默认拒绝；必须在实际 CPU 节点验证虚拟化和网络策略。展示服务器没有 KVM/K8S，使用固定 gVisor `20261102.0` 的 systrap 模式。官方安装包的 SHA-512 写入部署脚本并在安装前复验。`runsc` 与随附 `gvisor-bin/` 同目录部署。

OpenSandbox 1.1.0 有两个需要明确处理的差异：

- gVisor 与其基于 iptables NAT 的出口侧车不兼容，展示环境使用独立 internal Docker bridge、禁止容器互访及限定该网桥的宿主/转发规则；每次准入重新检查。
- Docker provider 不支持 `secureAccess` 参数。展示适配器使用 execd 官方 `EXECD_ACCESS_TOKEN`/`X-EXECD-ACCESS-TOKEN`，每次尝试生成独立随机凭据；平台管理密钥和模型密钥不传给沙盒。实测发现其普通命令入口继承 daemon 环境，因此 `RunCommandOpts.envs` 在第一个 UID 1000 进程启动前将 execd token 覆盖为空，再使用 `env -i` 只保留 PATH/HOME/TMPDIR。仅在 Shell 内 unset 不足以避免先前进程环境被读取。生产 Kubernetes 路径保留 `secureAccess=true`。

Docker 24 的 [CVE-2024-29018](https://github.com/moby/moby/security/advisories/GHSA-mq39-4gv4-mvpx) 与宿主 loopback DNS 转发有关。展示准入拒绝该 DNS 配置，并直接探测内置 DNS、指定 DNS、公网、元数据、宿主和另一个任务。OpenSandbox 会发布 execd 端口，因此规则同时覆盖 DNAT 的 FORWARD 与 docker-proxy 的 INPUT 路径。平台服务只监听 loopback，沙盒控制服务不公开到互联网。

## 已实现约束

Manifest 按原始 UTF-8 字节 SHA-256 验证，文件逐一复核大小和摘要；不从 Skill 内提供的地址下载依赖。每次尝试只能使用一个固定镜像摘要，最多 20 个 Skill、总计 20 MiB，输入按 root/0444 写入，普通执行 UID/GID 为 1000。

每个任务尝试创建独立沙盒并绑定租户摘要、环境、run、attempt、lease；租户摘要用完整 SHA-256 的 Base32 编码以满足 Kubernetes label 的 63 字符上限。远端 TTL 最多一小时，命令还受绝对截止时间和单次超时限制。输出每次 64 KiB、整次尝试 1 MiB，HTTP/SSE 响应另设 8 MiB 上限。

DeepAgents 文件上传先进入生成的可信临时路径，再由 UID 1000 写到 `/workspace`，避免 SDK 的 root 权限跟随用户链接写文件。工作区原生文件下载限制为每个 32 KiB，逐级 `openat`/`O_NOFOLLOW` 并拒绝特殊文件。SkillsMiddleware 发现 `SKILL.md` 所需的下载仅按精确路径返回内存中已校验的发布文件，不扩大为 root 文件读取接口。大文件产物 API 尚未接入，不能声称已支持无限制文件导出。

展示实例另有 CPU、内存、宿主 PID 限额，gVisor 文件写入使用内存 overlay；不可信进程硬限制 128 PID 和单文件 64 MiB。完整内存/PID/磁盘耗尽、重启回收及生产配额仍要另行验收。

## 部署与验证入口

`deploy/platform/opensandbox/provision_demo.py` 仅在明确的单机展示路径 `/opt/open-agent/sandbox` 运行；增加 `open-agent-runsc`，合并并备份 Docker daemon 配置，然后 reload。已有项目 runtime/config 不一致时拒绝覆盖；不会重启 Docker 或停止其他应用。

`verify_demo.py` 使用真正的 SDK、平台适配器和两份合成 Skill。验证脚本拥有宿主管理权限以检查容器配置；正式 worker 不应获得 Docker socket。展示采用固定官方 Python 镜像，验证准入会在执行不可信代码之前建立 UID 1000 的 agent 账号，正式内网镜像应在镜像构建时预置该账号。

完整交付还需要：任务 worker 绑定、可信制品客户端、持久 lease/fencing/配额、受限大文件产物通道、K8S admission、IPv6 防护与验收、开机防火墙恢复、离线镜像与依赖包以及故障恢复验证。当前网络实测针对 IPv4，展示平台开放任务前还要完成 IPv6 与开机防护。未完成项保留在 OpenSpec，禁止开启生产代码任务。

2026-11-04 的 13 项实机检查已通过，原始无凭据结果见 [验证记录](sandbox-verification-20261104.json)。仅模型使用确定性合成响应；OpenSandbox 服务、gVisor、SDK、DeepAgents 图、Skill 文件读取与执行均为实际调用。该验证与本地模型协议测试是不同层面的证据，不能合并表述为完整平台真实模型联调完成。

参考：[OpenSandbox 1.1.0 源码](https://github.com/opensandbox-group/OpenSandbox/tree/release-1.1.0)、[gVisor 安装](https://gvisor.dev/docs/user_guide/install/)、[gVisor 文件系统](https://gvisor.dev/docs/user_guide/filesystem/)。
