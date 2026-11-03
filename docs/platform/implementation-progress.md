# 实施记录

2026-11-03，首个实施变更 `manage-tenant-access`。完整目标仍为企业离线 Agent 平台；这里区分实现、测试和未验收内容。展示部署安排在主要业务链路完成后。

## 已实现的增量

- V1.49 新增租户、环境配额、成员、平台管理员和审计表；稳定映射存量空间/个人用户，不自动授予生产权限。
- `commons.tenant` 统一从已验证 JWT 身份查询成员关系，按环境、角色、租户状态授权。查询同时检查账号状态和旧空间成员是否仍存在。
- Java 控制面加入 `/platform/tenants`，支持租户创建/停用、成员角色、生产环境管理员显式登记、配额及审计查询。变更与审计在同一事务，更新使用版本条件，禁止移除最后一名环境管理员。
- 原 React 控制台增加 `/platform/tenants` 页面与侧栏入口。切换租户/环境会清理旧视图，异步请求忽略已失效结果。
- 修复初始导入的 `loop/` 忽略规则：限定为仓库根目录的 `/loop/`，补回源快照中原有的前端 Loop 组件与 Python LoopNode。保留 Apache-2.0 归属。

## 验证证据

| 检查 | 当前结果 |
|---|---|
| Java 21、Maven 3.9.9，`commons` 编译与租户测试 | 15 项通过，0 失败 |
| Java 全模块 `-pl hub -am` 编译与租户测试 | commons 15 项 + hub 5 项通过，0 失败；toolkit 编译通过 |
| 前端 `npm ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund` | 使用原 Dockerfile/CI 相同 peer 参数，安装成功 |
| 前端 `npm run type-check` | 通过 |
| 前端 `npm run build` | 通过；仍有上游大包/Sass 废弃接口提示 |
| 真实 MySQL 8.4.13 迁移测试 | 首次发现测试就绪检查误命中初始化临时服务器，已改用 TCP；修复后复验待记录 |
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

旧资源入口的统一 tenant/environment 授权、新账号/空间生命周期接入、服务凭据轮换、Java→Python/Go 内部身份、管理 UI 与真实身份服务联调、审计保留期处理及完整双租户矩阵仍未验收。配额当前为配置定义，原子准入和计量随持久任务变更实现。

其余六项 OpenSpec 变更继续保留完整任务：模型配置与两端接入、不可变 Skill 发布、私有隔离沙盒、持久任务、统一 HTTP/聊天、离线部署监控与恢复。未完成项不归档、不以构建通过代替验收。

CI 新增 `.github/workflows/platform-checks.yml`，自动检查 Java 授权、真实 MySQL 迁移和前端构建。远端执行结果需单独检查，配置存在不等于 CI 已通过。
