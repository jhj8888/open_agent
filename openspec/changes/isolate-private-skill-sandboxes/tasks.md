# Tasks

## 2026-11-05 独立资源核验增量

- [x] 使用官方 SDK 独立检查实际沙盒目标/策略/配额和 CR/Pod，接到持久准备/绑定事务；未知字段、身份变化、额外资源或不完整列表拒绝，重复观察不覆盖首次凭据。
- [x] 完成 203 项 Linux overlay、261 项最终镜像及 16 项打包验证；真实零节点 Kubernetes API 的四类策略/标签/额外资源变更均拒绝，固定 CRD 默认值兼容后复验通过并清理 fixture。
- [x] 新镜像归档交付至测试服务器，原任务 58 步/2 个产物/缓存 Token 与公网授权重新核验通过，提交可复核证据。

上述仅完成独立资源核验组件，HTTPS admission、controller 创建收敛、物理停止/回收和真实 Kata/CNI 尚未交付。证据见 `docs/platform/cluster-sandbox-verification-20261105.json`，原复合任务保持未勾选。

本文件中的未勾选项均为待实现工作；规格校验通过不代表功能完成。前置关系见 docs/platform/roadmap.md。

## 1. 隔离后端

- [ ] 1.1 锁定 OpenSandbox/Kata/K8S 兼容版本和镜像 digest，在 CPU 节点验证 RuntimeClass、CNI 与硬件虚拟化；保存创建/执行/销毁证据，未通过不得进入生产。
- [ ] 1.2 扩展 SkillSandboxConfigService 的 provider 枚举与内网配置校验，接口兼容测试覆盖历史 E2B 配置与生产拒绝规则。
- [ ] 1.3 实现 Python 官方 SDK provider 和租约/取消/回收逻辑，测试启动失败、超时、重复取消及控制面重启。

## 2. 安全执行

- [ ] 2.1 将 SkillSandboxRunner 绑定 run/attempt/release 上下文并固定摘要装载；测试同 Agent 并发和双租户文件不可见。
- [ ] 2.2 为 Skill 与 workflow code node 配置受限网络/文件/资源策略，测试公网、DNS 绕过、元数据、K8S API、宿主挂载和凭据不可达。
- [ ] 2.3 实现受限产物导出、日志截断和配额释放；测试符号链接、磁盘/PID/内存耗尽并编写异常处置手册。

## 3. 验收

- [ ] 3.1 在真实离线集群执行隔离与故障注入测试，记录版本、节点、策略、恢复时间与遗留风险；mock 通过不能代替此项。

## 2026-11-04 实机增量

展示机已验证真实 DeepSeek → DeepAgents → OpenSandbox/gVisor Skill 执行，脚本 UID 1000、模型报告实际结果及物理容器删除均有证据。新增执行许可钩子和重复清理逻辑。测试准入/journal 不代替生产租约/配额或 Kata/K8S 验收；任务继续保留未勾选，见 `docs/platform/runtime-executor.md`。

持久 journal 增量已实现 V1.53、环境原子沙盒配额、一次性创建意图、迟到资源观察及可信释放事务；worker 没有配额释放接口。31 项真实 MySQL、44 项其他 Java 和 155 项 Python 检查通过（两项首次 SSH 连接超时已单独重跑并记录），见 `docs/platform/sandbox-journal-verification-20261104.json`。独立物理回收和实际 worker 接线尚未交付，1.3、2.3 继续保留未勾选。

2026-11-05 已接通实际 OIDC/HTTP/S3/数据库/Temporal/systemd/Unix 核验/gVisor Skill 全链路，并验证正常完成、取消及 executor SIGKILL 后的独立物理回收。52 项核验代理及监督测试通过，新增 IPv4/IPv6 与开机防护恢复配置。证据见 `docs/platform/skill-run-verification-20261105.json`、`run-recovery-verification-20261105.json`。无 ID 的未知 creating 仍保留配额，需补足创建服务在途清理；生产 Kata/K8S、完整故障和离线攻击面未验收，因此任务保持未勾选。

2026-11-05 Kubernetes 配置准备增量：固定官方 OpenSandbox server 源码和镜像，验证真实配置模型/模板合并后的 CR；零节点 API 接受 CRD、Pod 与策略并拒绝超额请求。19 项配置测试、33 项 SDK 测试及 196 项离线镜像检查通过；详见 `docs/platform/kata-profile-verification-20261105.json`。API 夹具 1.36.6 不在 BOM 最高 1.34 的矩阵中；集群 Skill admission/UID 绑定/独立清理与 Kata/CNI 实机测试未交付，1.1、1.3、2.2、2.3、3.1 保持未勾选。

2026-11-05 V1.66 增量：在同一任务许可事务预留沙盒配额并固定目标，独立记录 CR/Pod UID，取消后仅允许保留回收观察；同一资源不得被两个任务占有。54 项 Java（含真实 MySQL、竞争和两次强制重启）、72 项 Windows Python及 210 项 Linux 镜像检查通过，Windows 的 5 项 Linux 专属检查已由镜像覆盖。展示机控制 API 升级及原任务/产物/缓存 Token 复核通过。证据见 `docs/platform/cluster-sandbox-binding-verification-20261105.json`；实际 Kubernetes 核验服务、停止证据和 Kata/CNI 验收尚未完成，整体任务继续保留未勾选。
