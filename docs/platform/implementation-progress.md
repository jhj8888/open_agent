# 实施记录

## 2026-11-05 当前状态

真实工作台已部署到 HTTPS 展示入口，保留用户认可的设计。标准登录、页面创建 Agent、真实 DeepSeek + DeepAgents + Skill 脚本执行、持久事件/用量、独立沙盒回收及未授权环境切换已验证。详见 [真实工作台与证据](live-console.md)。下文按日期保留早期增量及当时限制，不能将早期“尚未部署”当作当前状态。

项目仍在推进：生产推广完整流程、未知创建恢复、所有管理表单验收、离线冷启动、监控、备份恢复及容量验收尚未全部完成。

2026-11-03，首个实施变更 `manage-tenant-access`。完整目标仍为企业离线 Agent 平台；这里区分实现、测试和未验收内容。展示部署安排在主要业务链路完成后。

## 已实现的增量

- V1.49 新增租户、环境配额、成员、平台管理员和审计表；稳定映射存量空间/个人用户，不自动授予生产权限。
- `commons.tenant` 统一从已验证 JWT 身份查询成员关系，按环境、角色、租户状态授权。查询同时检查账号状态和旧空间成员是否仍存在。
- Java 控制面加入 `/platform/tenants`，支持租户创建/停用、成员角色、生产环境管理员显式登记、配额及审计查询。变更与审计在同一事务，更新使用版本条件，禁止移除最后一名环境管理员。
- 原 React 控制台增加 `/platform/tenants` 页面与侧栏入口。切换租户/环境会清理旧视图，异步请求忽略已失效结果。
- V1.50 新增环境模型连接、配置版本及加密凭据引用，React 租户页新增模型配置/轮换/连接检查。两端使用管理员地址白名单、固定 DNS 结果和无重定向传输。Python 私有入口取消公网 MaaS 回退和跳过 TLS；Java 流取消可释放等待中的订阅。详见 [模型接入说明](model-api-access.md)。
- V1.51 新增 Skill 暂存、发布清单、文件摘要、环境授权、测试证据和引用保留表。支持 ZIP 发布、旧草稿显式导入、授权下载、同版本幂等重试及测试→生产推广。控制台提供版本列表、摘要、导入/上传和文件下载；任务与 Agent 绑定仍待接入。详见 [Skill 发布说明](skill-releases.md)。
- Python 增加 OpenSandbox 1.1.0 官方 SDK 适配、固定制品校验和 DeepAgents `BaseSandbox` 后端。可信 worker 可用 `DeepAgentsRunner.bind_attempt()` 绑定已准入的沙盒，开启原生 Skill 工具并在结束/异常时销毁；租约能力不进入 HTTP 参数或序列化字段。持久任务 worker 尚待接入。旧公网 E2B 默认拒绝。
- 展示机已安装固定 gVisor 20261102.0 和 OpenSandbox 服务；13 项真实沙盒检查通过，包括原生 DeepAgents Skill 发现/执行（模型为合成模型）、双租户文件隔离、网络/DNS/元数据拒绝、凭据与本地控制 API 隔离、输出限额及取消回收。完整应用仍未部署，生产 Kata/K8S 未验收。详见 [私有沙盒说明](private-sandboxes.md)。
- 修复初始导入的 `loop/` 忽略规则：限定为仓库根目录的 `/loop/`，补回源快照中原有的前端 Loop 组件与 Python LoopNode。保留 Apache-2.0 归属。

## 验证证据

2026-11-04 实机执行增量：`RuntimeExecutor` 已通过真实 DeepSeek + DeepAgents + OpenSandbox/gVisor 联调；模型读取 Skill、实际执行脚本得到 UID 1000、报告真实输出，随后确认物理沙盒已删除。新增每操作许可、心跳取消和可重试清理；54 项执行/传输/沙盒、9 项 Java 接口、209 项既有 Python 回归通过。服务器已安装冻结的 Python worker 环境，完整持久 worker 服务尚未启动。见 [执行器说明](runtime-executor.md) 和 [验证结果](executor-verification-20261104.json)。

2026-11-04 后续增量：官方 Temporal SDK 1.35.0 调度骨架、outbox 发布器及独立 worker/dispatcher 身份已实现；执行配置只对有效租约返回固定模型/Skill 版本。22 项 Python 调度/传输测试、24 项真实 MySQL 任务测试及 37 项 Java 模型/内部接口测试通过，既有 Python 205 项回归通过。展示机 Temporal 开发服务已启动并限制 loopback；实际 Agent activity、资源清理补偿和完整应用仍待接通。详见 [接入说明](temporal-runtime.md) 与 [新增证据](temporal-runtime-verification-20261104.json)。

此前 V1.52 和 Java 持久任务账本的独立验证为 21 项真实 MySQL 用例和 36 项契约/既有回归（共 57 项），包括独占测试数据库 SIGKILL/重启；见 [任务实现记录](durable-runs.md) 与 [历史验证结果](run-ledger-verification-20261104.json)。新增用例见上段，完整展示应用仍未部署。

| 检查 | 当前结果 |
|---|---|
| Java 21、Maven 3.9.9，`commons` 编译与租户测试 | 15 项通过，0 失败 |
| Java 全模块 `-pl hub -am` 编译与租户测试 | commons 15 项 + hub 35 项通过，0 失败；toolkit 编译通过 |
| 前端 `npm ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund` | 使用原 Dockerfile/CI 相同 peer 参数，安装成功 |
| 前端 `npm run type-check` | 通过 |
| 前端 `npm run build` | 通过；仍有上游大包/Sass 废弃接口提示 |
| 真实 MySQL 8.4.13 迁移测试 | 2026-11-04 复验 7 项全部通过：空库、存量映射、真实授权查询、个人归属、数据库约束、模型版本环境外键及 Skill 原始推广/删除 SQL；就绪检查使用 TCP |
| Java Skill 发布与导入 | 35 项通过：恶意包、摘要、权限、上传失败清理、幂等、引用保留、推广证据及旧草稿导入 |
| Python 模型、DeepAgents、Skill 与私有沙盒合并回归 | 205 项通过，4 条既有依赖废弃/字段提示；含 26 项沙盒契约和可信 attempt 绑定的成功/异常释放 |
| 展示机真实 OpenSandbox + gVisor | 13 项通过，证据见 `sandbox-verification-20261104.json`；不代表生产集群、配额、断电恢复或真实模型端到端验收 |
| 测试模型基础协议探测 | 已通过工具调用、工具结果回传、流式输出、usage；仅使用合成测试输入 |

模型探测使用用户授权的公网演示接口。请求名称 `deepseek-v4-flash`，返回名称 `deepseek-flash`。这是 SDK 协议探测，不代表已完成 DeepAgents、Java 两端整合，也不代表生产内网模型验收。凭据只存于忽略目录内的本机加密文件，代码和报告不含密钥。

复现 Java 检查（JAVA_HOME 指向 Java 21）：

```sh
mvn -B -ntp -f console/backend/pom.xml -pl hub -am \
  '-Dtest=TenantAccess*Test,TenantGovernanceSecurityTest' \
  -Dsurefire.failIfNoSpecifiedTests=false test
```

复现真实数据库检查（需要 Docker，镜像须先准备；容器无外部网络、端口和宿主挂载，结束自动清理自己创建的容器）：

```sh
export MYSQL_TEST_IMAGE=mysql@sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902
docker pull "$MYSQL_TEST_IMAGE"
python3 console/backend/tests/test_tenant_migration.py
```

## 尚待完成

2026-11-04 统一 API 增量：新增固定版本部署、生产推广校验、部署服务凭据与 runs/SSE；控制台接入真实服务调用。66 项 Java 用例经定向复测全部通过，前端类型检查与生产构建通过。具体失败修复和未验收范围见 [接口验证记录](agent-api-verification-20261104.json)，接口契约见 [HTTP API](agent-http-api.md)。执行准入仍默认关闭，完整实际执行与资源回收链路未交付，公网预览继续使用明确标识的示例数据。

`manage-tenant-access` 任务 1.1 已具备上述证据并完成，其余任务保持未勾选；整体项目尚未完成。

`persist-agent-runs` 任务 1.1 已完成并保留真实数据库证据；配额与 outbox 的存储层已实现，其余端到端任务保持未勾选。

旧资源入口的统一 tenant/environment 授权、新账号/空间生命周期接入、服务凭据轮换、Java→Python/Go 内部身份、管理 UI 与真实身份服务联调、审计保留期处理及完整双租户矩阵仍未验收。队列、执行槽及沙盒 reservation 已有数据库原子准入；HTTP/worker 联调、token 计量及独立资源回收仍待完成。

模型的配置、协议样本、目的地址保护和双端适配已有实现与测试；工具能力探测 UI、发布/任务引用和真实内网验收仍待完成。Skill 变更的 1.2、1.3、2.3 已有验证证据，旧数据全量迁移/回滚、Agent 绑定、浏览器验收及任务联调仍待完成。私有隔离沙盒、持久任务、统一 HTTP/聊天、离线部署监控与恢复继续保留完整任务。未完成项不归档、不以构建通过代替验收。

CI 新增 `.github/workflows/platform-checks.yml`，自动检查 Java 授权、真实 MySQL 迁移和前端构建。远端执行结果需单独检查，配置存在不等于 CI 已通过。

2026-11-04 后续增量：沙盒配额 journal 已接入创建前预留及一次性创建意图；worker 的销毁报告与独立配额释放分开。31 项真实 MySQL、44 项其他 Java 及 155 项 Python 检查通过，包含数据库强制重启、配额竞争及取消后回调。两项首次 SSH 转发连接超时已单独重跑通过并保留记录。见 [实现边界](sandbox-journal.md) 和 [验证材料](sandbox-journal-verification-20261104.json)。实际执行/回收 activity、完整 HTTP/聊天展示与最终平台部署仍未完成。
