# Kata 沙盒部署准备

此目录固定企业 K8S 沙盒的公开配置输入，并用官方 OpenSandbox 实现验证最终生成的工作负载。**当前还不是可开启任务准入的沙盒部署包**：集群准入/独立回收服务、CR/Pod UID 持久绑定、真实节点隔离和故障验收仍需接通。`cluster_child` 继续拒绝沙盒配置，不回落为普通容器或宿主执行。

调用方向：平台 `OpenSandboxProvider` → 官方 Python SDK → 通过 HTTPS 访问 Lifecycle Server 的代理 → 独立 namespace 中的 BatchSandbox/Kata Pod。模型凭据只在受信 executor；租户代码仍以 UID/GID 1000 执行。原演示环境使用既有 gVisor/宿主隔离，不随这个准备包切换。

## 生成公开配置

先将固定镜像导入企业仓库并验证 manifest digest。`upstream.json` 与上级 `opensandbox/upstream-1.1.0.yaml` 记录固定版本、源码和许可；不要替换成浮动 tag。配置不含任何密钥：

```json
{
  "environment": "test",
  "registry": "registry.internal",
  "server_image": "registry.internal/opensandbox/server@sha256:68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1",
  "execd_image": "registry.internal/opensandbox/execd@sha256:e11fdd6fa641329a1fe5298e71c8dd2afca742674fe48bc855b2ff6715512829",
  "controller_image": "registry.internal/opensandbox/controller@sha256:7299b94ab1382be4adb4fd41209ea60e16f66fcd963af64345b1106ac434957e",
  "skill_image": "registry.internal/open-agent/skill@sha256:<已构建且含 agent 账号的镜像摘要>",
  "runtime_class": "kata-qemu",
  "image_pull_secret": "open-agent-registry",
  "maximum_sandboxes": 16
}
```

示例不是实际企业仓库配置，Skill 摘要占位符必须替换。镜像需要预置 UID/GID 1000 的 agent 账号、`/skills` 和 `/workspace`，参照 `../opensandbox/Dockerfile.skill-python`，依赖在构建期安装。执行平台不联网补包。

```sh
python deploy/platform/kata/prepare_profile.py --configuration /private/profile.json --output /private/new-kata-profile
```

输出包含 server TOML、BatchSandbox 模板、namespace、ServiceAccount、NetworkPolicy、ResourceQuota 和逐文件 SHA256。目标目录必须不存在，避免覆盖其他交付。`test` 与 `prod` 使用独立 namespace；Secret 由企业流程另行创建，不放入 Git、values 或制品。

Server 的 API key 使用官方 `OPENSANDBOX_SERVER_API_KEY` 环境变量从 Secret 注入。server 的公开配置挂载在 `/etc/open-agent-sandbox`，SQLite 路径 `/var/lib/opensandbox/opensandbox.db` 需要持久存储；单实例准备配置不代表生产 HA。HTTPS 证书、企业 CA、server/controller 的 RBAC 和网络出口、Kata RuntimeClass/节点池须在完整部署阶段配置。这里不生成私钥或自动设置生产可用标记。

## 网络、身份与资源

新增 SDK `network_enforcement="cluster-cni"` 只允许 Kata 和 HTTPS；仍强制调用独立 admission 的 before/after 核验。它使用 server proxy，关闭依赖额外 ingress gateway 的 `secureAccess` 和带 NET_ADMIN 的 egress 侧车。原 `opensandbox-egress` 模式保留；缺少核验不能把两种模式互相降级。

所有 profile 都设置独立的 `EXECD_ACCESS_TOKEN`，只在 SDK 请求头中携带并在启动 UID 1000 子进程时清空。网关认证不能阻止沙盒内部 loopback 调用 execd，故不能代替此 token。控制面 key、模型 key 和 Kubernetes token 均不传入沙盒。

NetworkPolicy 对整个沙盒 namespace 默认拒绝双向连接，仅允许同环境指定 Lifecycle Server Pod 访问 execd 的 TCP 44772；沙盒没有 DNS 或其他出口。K8S NetworkPolicy 具有叠加语义，部署核验必须拒绝额外放宽策略并实测 CNI、IPv4/IPv6、宿主/元数据/API 访问。存储了 NetworkPolicy 对象不等于网络隔离已生效。

模板使用 Kata 节点池、无 ServiceAccount token、无 host namespace/hostPath、固定镜像、`restartPolicy=Never`、一小时执行硬截止和 30 秒停止宽限。受信 execd 主进程以 root 装载文件和切换 UID，只保留 CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID；主容器根目录只读，工作区、Skill 和 tmp 有独立 emptyDir 限额。execd installer 是固定受信 init 镜像，仍需在实际 Kata 节点完成权限与文件系统验证。

Kata 请求包含 `ephemeral-storage=512Mi`；namespace 同时限制 Pod/BatchSandbox 数和 CPU、内存、临时盘总量。Kubernetes 默认补齐 requests，RuntimeClass overhead 也可能计入预算，所以 Pod 数上限不承诺实际并发容量。emptyDir 和 ephemeral-storage 的限制包含 kubelet 驱逐语义，不是任意瞬间的磁盘硬限额；PID/内存/磁盘耗尽还需实机测试。部署后必须等待 ResourceQuota 的 status.hard/status.used 初始化，创建失败不能被当作已分配资源。

CR 与 Pod 均预留平台清理 finalizer。必须先接通持久终止证据及独立清理服务，才能在任务环境使用该模板；不能手工清除生产 finalizer 来规避未知创建或节点失联。

## 验证边界

`verify_upstream_profile.py` 在固定官方 server 镜像中使用真实配置模型、请求模型、创建上下文和 BatchSandbox provider，捕获实际 CR 声明，核对安全字段确实在模板合并后保留。K8S 写入被显式捕获，不执行容器。`verify_server_image.py` 以非 root、只读、无网络容器运行它，并核验输入摘要。

固定官方镜像的 Python 包元数据为 `0.1.0.dev0`。已核对实际源文件与 umbrella 1.1.0 commit 一致，验证同时固定镜像和源码摘要；不把这个元数据值当作实际发布版本。对应上游行为见 [固定版本源码](https://github.com/opensandbox-group/OpenSandbox/tree/b00c96b0a89c07fc18409fcfe72d170b4c96c8a7/server/opensandbox_server/services/k8s)。

`verify_api_profile.py` 再将官方生成的 CR、Pod 和配套对象提交给独立零节点 K3s，核验 CRD/Pod 字段、资源默认值和配额拒绝。它手工提交生成的 Pod，没有部署 OpenSandbox controller，不证明控制器或物理回收运行。夹具完成后执行 `../cluster/cleanup_api_fixture.py`。当前 API 夹具为 1.36.6，超出 OpenSandbox 1.1.0 BOM 所列最高 1.34，结果只用于 API 声明校验，不能作为该发布的兼容性验收。

实际企业 K8S 版本、Kata/KVM、CNI、配额、控制器、TLS 和回收联调未完成前，生产准入保持关闭。验证证据记录在 `docs/platform/kata-profile-verification-20261105.json`。
