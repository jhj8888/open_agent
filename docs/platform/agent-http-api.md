# 部署与任务 HTTP API

调用链：企业网关 → Spring 用户 JWT/部署凭据验证 → 实时租户权限 → RunLedger 事务准入与 outbox → Temporal → DeepAgents 执行器。事件查询、聊天调试与外部客户端复用同一 run_id。后台 worker 的私有凭据不能调用公开接口；部署服务凭据不能管理租户或发布版本。

当前新增 API 的实现与契约见 [OpenAPI](agent-runs.openapi.yaml)。准入默认关闭（`PLATFORM_RUN_ADMISSION_ENABLED=false`），直到真实 worker、资源回收器与必要环境配置就绪。静态预览地址没有这些接口；不能将示例页面中的聊天视为真实执行验收。

## 凭据和可见性

用户 JWT 经现有认证方验证，并在每次调用时重新检查数据库成员权限。测试环境遵循现有角色权限；生产执行需要单独的部署授权，单凭发布或管理角色不产生执行权限。管理员/运维可按现有租户策略查询和取消任务。

部署令牌包含随机 256 位秘密，仅在签发或轮换时返回一次，数据库只保存 SHA-256。每个令牌固定租户、环境、部署与 create/read/cancel 动作；只能查询或取消同一凭据主体创建的任务。轮换保持主体和原到期时间，旧令牌立即失效。过期、吊销与租户停用会在后续请求和每次 SSE 轮询中生效。原始令牌不进入日志、事件或 OpenSpec。

## 执行示例

在已接通后端的环境中设置 `BASE_URL`、`DEPLOYMENT_ID` 和 `AGENT_TOKEN`。令牌由调用方安全注入环境，勿写入源码。以下变量均为部署环境的实际值，不使用预览站点。

```sh
# 生成一次请求键；网络重试时保持这个键和请求体不变。
REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -sS -X POST "$BASE_URL/api/v1/agent-deployments/$DEPLOYMENT_ID/runs" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Idempotency-Key: $REQUEST_ID" \
  -H 'Content-Type: application/json' \
  -d '{"input":{"message":"整理材料并列出三个行动项","history":[]},"timeoutSeconds":300}'

# 将上一步响应 data.run_id 赋给 RUN_ID。
curl -sS "$BASE_URL/api/v1/runs/$RUN_ID" -H "Authorization: Bearer $AGENT_TOKEN"
curl -N "$BASE_URL/api/v1/runs/$RUN_ID/events" \
  -H "Authorization: Bearer $AGENT_TOKEN" -H 'Accept: text/event-stream'
curl -sS -X POST "$BASE_URL/api/v1/runs/$RUN_ID/cancel" -H "Authorization: Bearer $AGENT_TOKEN"
```

接入成功为 HTTP 202；`data` 包含 run_id、status_url、events_url 与 reused。幂等键限定在部署内，相同主体/输入/超时重试返回同一任务，内容冲突为 409。权限在幂等返回之前重新校验；撤销权限后不能通过旧请求键读取任务。

事件有持久化序号。浏览器重连传 `Last-Event-ID`，它优先于 `after` 查询参数；JSON 历史页通过 `Accept: application/json` 获取，每页最多 100 条。收到 `stream.end` 停止重连；带 `terminal=true` 的 `error` 表示执行失败，随后仍可查询终态。连接约 60 秒轮换，不重复提交任务，不因断线而取消。历史保留期已过返回 410，应读取终态并显示历史不可用。

## 部署管理接口

以下相对路径基于 `/platform/tenants/{tenant}/environments/{test|prod}/agent-deployments`，仅接受用户 JWT。路径中的租户与环境仍需通过服务端授权，客户端不能指定其他租户来扩大权限。

| 方法与后缀 | 行为 |
| --- | --- |
| GET 空后缀 | 最新 100 个部署元数据 |
| POST 空后缀（test） | 固定 Agent 指令、模型配置版本、Skill release 与摘要，创建不可变部署 |
| POST `/promote`（prod） | 依据同快照成功测试 run、生产模型/Skill 授权与发布权限创建生产部署 |
| POST `/{id}/disable` | 停用新准入，保留授权历史查询 |
| GET / POST `/{id}/credentials` | 查询令牌元数据 / 签发一次性可见的令牌 |
| POST `/{id}/credentials/{keyId}/rotate` | 原子轮换，保留主体/动作/到期时间 |
| DELETE `/{id}/credentials/{keyId}` | 吊销 |
| GET `/{id}/grants` | 查看生产用户执行授权 |
| PUT / DELETE `/{id}/grants/{uid}`（prod） | 授予 / 撤销指定成员执行权限 |

测试部署请求字段：`displayName`、`agentVersion`、`modelConnectionId`、`modelVersion`、`skillReleaseIds`、`execution`。execution 包括必填 `instructions`，以及可选 `answerInstructions`、`maxLoops`、`temperature`、`maxTokens`。不允许客户端注入 endpoint、凭据、任意插件或宿主机命令。

生产发布字段：`sourceDeploymentId`、`verificationRunId`、`modelConnectionId`、`modelVersion`。发布固定源版本的执行配置和 Skill 摘要；测试成功后制品被撤销或摘要改变会拒绝发布。模型停止或撤销发生在最后一次模型响应与任务完成之间时，任务不能以成功状态提交。

## 兼容与限制

新增安全链只匹配 `/api/v1/agent-deployments/**` 与 `/api/v1/runs/**`。原 workflow 路径、管理登录链与共享响应实现保持不变。当前新执行入口只启用 DeepAgents；workflow 执行器映射与旧 API 的完整回归仍待交付。列表暂为有界最新记录，后续分页单独扩展。SSE 每个 API 实例限制 128 个连接、每个凭据主体 8 个连接。

验证以真实数据库和 HTTP 层测试为依据；模型、资源回收和浏览器端到端链路需另外完成，不以 mock MVC 或 OpenAPI 文件代替运行验收。
