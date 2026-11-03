# Tasks

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
