# Spec Delta

## Purpose

现有公开 API 发布主要面向 workflow，独立 Agent 与工作流的执行入口不同。每个部署的 Agent 都需要稳定、可鉴权、可追踪的 HTTP 访问方式，聊天调试应复用同一执行与权限语义。 本能力用于定义企业内网 Agent 平台的可验证行为边界，使测试、部署、失败处理和权限控制具有一致的验收依据。

## ADDED Requirements

### Requirement: 每个部署可 HTTP 访问

系统 SHALL 为独立 Agent 和 workflow Agent 的每个获发布部署提供稳定 HTTP 任务接口。

#### Scenario: 两类 Agent

- **WHEN** 调用者分别向两类有效部署提交合法请求
- **THEN** 均返回 run_id 及状态/事件地址

#### Scenario: 未发布部署

- **WHEN** 调用者提交未发布或已停用部署
- **THEN** 返回明确错误且不创建可运行任务

### Requirement: 部署授权

系统 SHALL 验证用户或服务凭据的租户、环境、部署与动作权限。

#### Scenario: 限定服务凭据

- **WHEN** 凭据只允许调用部署 A
- **THEN** 可创建 A 的任务并只查询获授权运行

#### Scenario: 跨部署访问

- **WHEN** 同一凭据尝试调用部署 B 或生产环境
- **THEN** 拒绝访问且不泄露配置或结果

### Requirement: 一致任务协议

系统 SHALL 在 HTTP 与聊天窗口之间使用相同的任务标识、状态、取消和事件语义。

#### Scenario: 聊天调试

- **WHEN** 开发者在聊天窗口启动测试任务
- **THEN** 显示 run_id、环境、版本、流式事件和状态，可通过 HTTP 查询相同任务

#### Scenario: 断线后查询

- **WHEN** 聊天浏览器断线重开
- **THEN** 仍可查询任务并继续订阅，不自动重复执行

### Requirement: 可诊断错误

系统 SHALL 对接入错误和执行中错误提供稳定错误类型、关联标识及脱敏信息。

#### Scenario: 准入背压

- **WHEN** 租户队列已满
- **THEN** 返回 429 和可识别错误码，不展示为模型回答

#### Scenario: 流中执行失败

- **WHEN** 工具或模型在 SSE 开始后失败
- **THEN** 发送关联 run_id 的失败事件并可查询终态

### Requirement: 版本兼容

系统 SHALL 保留既有 workflow 发布接口契约，并为新部署接口提供可执行调用文档。

#### Scenario: 旧客户端

- **WHEN** 既有客户端使用原 workflow 路径和请求格式
- **THEN** 通过兼容性用例且没有静默破坏

#### Scenario: 新部署示例

- **WHEN** 用户复制部署页的示例并填入授权凭据
- **THEN** 能在内网创建、查询和取消该部署的任务
