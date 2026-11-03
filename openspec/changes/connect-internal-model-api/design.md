# Design

## Context

Python provider 已支持自定义 base_url；Java 独立聊天使用 Spring AI。不能只改 Python 路径就认为全平台接入一致。现有实际 endpoint、模型名和鉴权值尚未提供，不需要在规格中猜测。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/model-api-access/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 默认使用现有 OpenAI-compatible 适配路径作为首个协议，明确这是初始接口假设；模型团队若提供其他协议，由 adapter 扩展，不建立新模型网关。首个契约样例使用本地 mock，真实 URL/证书/凭据留给环境配置。
2. 配置包含 endpoint、model_name、protocol、credential_ref、connect/read/total timeout、TLS/CA 和 capability flags（stream/tool_calls/usage）。服务器只允许管理员登记的内网目的地址；阻止访问云元数据、集群管理 API、重定向绕过和 DNS 重绑定。调用凭据只存在可信服务，不向浏览器或沙盒传递。
3. Java 和 Python 均通过现有官方客户端配置调用，固定请求/错误/usage 契约并用同一组 mock 用例验证。工具调用是结构化 function/tool calling；不把模型输出的 Shell 直接放在宿主进程执行。
4. 超时与上游限流归类为稳定错误。连接建立前或明确可重试状态可采用有界重试；已发送且结果未知的请求、已开始流式响应和非幂等工具操作不盲重试。不会故障转发到其他模型、其他租户或公网。
5. 记录 run_id、配置版本、时延、错误类别和服务端 usage；无 usage 时标为 unknown，估计值需单独标注。这里记录平台调用量，不承担模型后端的容量规划、路由负载均衡或 GPU 指标。

## Risks / Trade-offs

[兼容接口不支持 tools] → 发布前能力检测，不能以普通聊天成功代替；[流中断后重放导致副作用] → 不自动重放已发起的工具；[凭据泄漏] → 引用注入及输入/输出日志脱敏测试。

## Migration Plan

保留旧模型配置 ID，回填协议/环境字段，先为测试 Agent 开启新版配置校验；真实内网接口通过契约测试后用于生产。回滚适配器时保留配置版本与审计，不回退到公共 endpoint。
