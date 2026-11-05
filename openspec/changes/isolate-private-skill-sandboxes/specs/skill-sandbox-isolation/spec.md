# Spec Delta

## Purpose

租户可以上传 Python 和 Shell，普通应用容器及现有公共沙盒默认配置不足以构成企业不可信代码隔离。需要把代码执行放入私有、受限、可回收且绑定租户与任务的独立沙盒。 本能力用于定义企业内网 Agent 平台的可验证行为边界，使测试、部署、失败处理和权限控制具有一致的验收依据。

## ADDED Requirements

### Requirement: 沙盒端口隔离不影响宿主返回流量

宿主端口隔离 SHALL 拒绝非 loopback 发起的沙盒端口连接，同时允许由宿主合法出站连接建立的返回流量；更新规则不得重载或清空共享防火墙。

#### Scenario: 出站临时端口与沙盒端口重叠

- **WHEN** 宿主使用沙盒端口范围内的临时源端口连接已授权的私有服务
- **THEN** 已跟踪返回包不被沙盒入站拒绝规则误拦截，而非 loopback 新建连接仍被拒绝

### Requirement: 独立任务隔离

系统 SHALL 将不可信 Skill 代码放在与控制面隔离且具有独立内核边界的沙盒中；不同任务不能共享可写工作区。

#### Scenario: 同 Agent 并发

- **WHEN** 两个任务同时运行同一 Agent 并写入同名文件
- **THEN** 各自只能读取自己的工作区

#### Scenario: 隔离不可用

- **WHEN** 独立内核运行时不可用
- **THEN** 任务以隔离不可用失败，禁止降级为宿主执行

### Requirement: 租户与环境隔离

系统 SHALL 将沙盒控制、文件及产物的权限绑定租户、环境和任务尝试。

#### Scenario: 合法产物

- **WHEN** 任务成功导出受限大小的产物
- **THEN** 只有获授权主体能读取该租户环境下的产物

#### Scenario: 沙盒越权

- **WHEN** 租户 A 尝试控制 B 的 sandbox_id
- **THEN** 拒绝请求并记录审计

### Requirement: 网络与凭据边界

系统 SHALL 默认拒绝沙盒网络，并阻止读取控制面凭据、云元数据和集群管理接口。

#### Scenario: 沙盒内部调用 execd

- **WHEN** 租户代码尝试通过 loopback 调用 execd 控制接口
- **THEN** 缺少本次尝试独立 execd token 的请求被拒绝；网关 secureAccess 不能代替该鉴权，子进程不得继承 token 或平台管理凭据

#### Scenario: Kubernetes 使用服务端代理与 CNI

- **WHEN** 操作者显式选择 cluster-cni 网络模式
- **THEN** 只允许 HTTPS server proxy 和 Kata profile，仍需独立准入验证实际默认拒绝策略；不自动启用额外 gateway/NET_ADMIN 侧车，不允许回落为未经核验的普通容器

#### Scenario: 内部白名单

- **WHEN** 管理员显式允许某内部服务且策略生效
- **THEN** 沙盒仅能访问批准的地址和端口

#### Scenario: 拒绝外联

- **WHEN** 代码访问公网、元数据或未批准内网地址
- **THEN** 连接被阻止，环境中不含平台或模型密钥

### Requirement: 配额和回收

系统 SHALL 强制资源上限、执行截止时间及生命周期回收，失败或取消不能永久占用配额。

#### Scenario: 超额代码

- **WHEN** 代码持续分叉、分配内存或写入磁盘直到超限
- **THEN** 受资源限额终止并记录明确原因

#### Scenario: 取消与孤儿

- **WHEN** 任务取消、服务崩溃或租约过期
- **THEN** 执行停止且沙盒和配额最终被回收

### Requirement: 沙盒创建预留和回收证明

系统 SHALL 在外部创建沙盒前持久预留租户环境配额和一次性创建意图，只有受信独立回收器确认资源及在途创建均已终止后才能释放配额。

#### Scenario: 创建响应丢失

- **WHEN** 创建意图已提交但确认或 SDK 创建响应丢失
- **THEN** 不重复创建，未知资源继续占用配额并按租约元数据进入回收流程

#### Scenario: 集群沙盒目标和资源身份

- **WHEN** 受信核验器为集群任务准备并观察沙盒
- **THEN** 目标 namespace UID 和 RuntimeClass 来自操作者固定配置，CR UID 和 Pod UID 只绑定一次且独立于 executor 身份；同一资源不能归属两个任务

#### Scenario: 取消后收到沙盒资源观察

- **WHEN** 同一租约的 CR／Pod 身份在取消或终态后才被核验器确认
- **THEN** 可以持久登记用于回收，但不能替换已绑定 UID、恢复执行许可或凭资源观察释放配额

#### Scenario: worker 上报销毁

- **WHEN** worker 上报 SDK 销毁成功或心跳已过期
- **THEN** 保存观察记录但不直接释放配额；需独立确认执行进程停止和物理资源不存在

#### Scenario: 迟到资源回调

- **WHEN** 取消或终态后收到同一 worker 的创建或销毁回调
- **THEN** 仅允许记录同一租约资源，禁止恢复执行、替换已绑定沙盒或复活已释放记录

### Requirement: 制品与输出校验

系统 SHALL 只装载经验证且固定摘要的 Skill 制品，并限制输出和产物路径、大小及类型。

#### Scenario: 摘要匹配

- **WHEN** 制品校验通过
- **THEN** 在只读输入和独立工作区中执行并关联 release_id

#### Scenario: 产物逃逸

- **WHEN** 代码以符号链接或绝对路径导出宿主文件
- **THEN** 拒绝导出且不返回越界内容
