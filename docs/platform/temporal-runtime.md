# Temporal 调度与 worker 内部接口

2026-11-04 增量：官方 Python SDK `temporalio==1.35.0`、确定性 workflow、outbox 发布器、独立内部服务鉴权和固定资源读取已实现。真实 Temporal 测试使用合成 activity；执行 DeepAgents、沙盒持久清理与统一 HTTP/聊天尚未接通，不能把这些测试解释为平台已经可展示。

后续 RuntimeExecutor 已完成独立真实模型/Skill 联调，并新增 `/worker/runs/{runId}/permit` 操作许可接口，见 [执行器记录](runtime-executor.md)。生产 execute/reconcile activity 注册和持久清理仍未接通，Temporal 测试的合成 activity 边界保持不变。

## 调用链及影响

`RunOutbox → dispatcher 私有 HTTP 客户端 → Temporal Client → AgentRunWorkflow → execute/reconcile activity`。实际 activity 的实现是下一阶段工作。workflow 参数只包含 tenant/environment/run_id，结果只含状态；模型密钥、输入、Skill 文件、运行租约均不进入此调度载荷。

新增 `/internal/platform/**` 专属 Spring Security chain，顺序为 0，只匹配该命名空间，不改变其他登录、模型、workflow、sandbox 路由的匹配规则。内部控制器不使用旧 `ResponseResultBody` 包装，Python 客户端直接读取 JSON DTO。共享 `PlatformExceptionHandler` 只新增 RunFailure 映射；旧错误类型保持原处理。`ModelConnectionService` 新增受租约保护的固定版本读取，既有用户访问与凭据加密方式不变。

## 服务身份与数据读取

控制面从部署环境的 `PLATFORM_SERVICE_KEYS` 读取 JSON 字典，格式为 `{key_id: {role, secret}}`，role 为 `worker` 或 `dispatcher`，secret 为 32–256 个可打印 ASCII 字符；未配置时全部拒绝。实际值由部署系统注入，禁止写入 Git、浏览器或沙盒。

请求使用 `X-Platform-Key-Id` 和 `X-Platform-Service-Key`。worker 身份来自已认证的 key_id，不接受请求体指定 worker_id。两种角色不互通，用户 JWT 也不能代替服务凭据。响应设置 no-store。Python 客户端只接受 HTTPS（校验 CA）或字面量 loopback HTTP，禁止重定向、代理环境继承及非 internal/platform 路径，每次请求总时限 10 秒，响应有大小上限。

| POST 路径（前缀 `/internal/platform`） | 身份 | 行为 |
|---|---|---|
| `/dispatch/claim`、`/dispatch/{id}/ack`、`/dispatch/{id}/retry` | dispatcher | 领取、确认、退避 outbox；令牌和过期时间由数据库校验 |
| `/dispatch/runs/{runId}/status` | dispatcher | 查询明确 tenant/environment 下的任务状态 |
| `/worker/runs/{runId}/status`、`claim`、`heartbeat` | worker | 查询、原子准入及租约心跳 |
| `/worker/runs/{runId}/events`、`finish` | worker | 带 fencing 的事件与终态提交 |
| `/worker/runs/{runId}/runtime` | worker | 读取此部署固定版本的模型配置、输入、快照和 Skill 引用 |
| `/worker/runs/{runId}/artifact` | worker | 读取绑定发布版本的原始 manifest 或索引文件；校验摘要及大小 |

runtime/artifact 均在租户锁内复查任务、租约、worker、截止时间、模型撤销和 Skill 发布状态。对象存储读取在事务外进行，返回前再次检查租约，防止读取期间取消仍返回内容。模型当前版本轮换不会改变部署绑定版本；显式撤销可阻止继续读取。Python 使用 SecretStr 避免 repr/JSON 意外输出凭据。

这是受信平台服务接口，持有 worker 身份的服务仍属于可信计算边界，不能把服务 key 分发给租户。公网入口必须拒绝整个 `/internal/platform/**`；生产以独立网络、TLS 和部署密钥管理约束访问。当前没有暴露释放执行槽位的 HTTP 接口，不能靠任意 worker 声称“已清理”释放资源。

## 重试、取消与故障

- workflow ID 固定为 `open-agent/<run_id>`，开始请求使用 REJECT_DUPLICATE 与 FAIL conflict policy。开始成功但确认丢失时，重投识别已有 workflow；关闭后的同 ID 也不重开。
- outbox 每批最多并行处理 4 条，领取 60 秒，每个网络操作总时限 10 秒；未确认的消息在领取到期后重新可见。失败日志只含 run_id 与异常类别。
- execute activity 不自动重试（maximum_attempts=1）。只有未获得执行槽、没有副作用的 queued 结果允许 2 秒后再次准入；不能把未知工具结果自动再执行。
- execute 的排队等待上限 60 秒、运行上限 3660 秒、心跳上限 30 秒。数据库内的任务截止时间仍是执行器必须遵守的更严格边界。
- 取消信号等待执行 activity 停止，然后进入 reconcile。reconcile 每次上限 90 秒，重试间隔 2–30 秒，总窗口 2 小时；仅允许幂等清理。窗口耗尽仍需要独立补偿扫描，不能静默释放配额。
- execute 正常返回 succeeded、failed 或 timed_out 也进入 reconcile；数据库终态不释放物理资源配额。`reconcile-completed-v1` patch 保留旧历史的重放路径，新执行在独立核验完成后才结束调度。reconcile 必须返回数据库既有终态，不能用清理成功或迟到取消改写任务结果。
- Temporal 心跳与数据库租约是两个不同机制。后续执行 activity 必须同时维护，并在每次模型/沙盒操作前检查许可；当前 workflow 测试不证明真实工具已具备该保证。

## 复现与展示服务

将已校验的官方 Temporal CLI 1.10.0 放在本地，设置 `TEMPORAL_TEST_BINARY` 为其绝对路径，再运行：

```sh
PYTHONPATH=core core/agent/.venv/bin/python -m pytest \
  core/agent/tests/test_durable_control.py core/agent/tests/test_durable_workflow.py \
  -q -p no:cacheprovider
```

用例自身不会下载程序；未提供 CLI 时 Temporal 用例明确跳过，验收和 CI 必须提供它。当前记录为 22 项通过、0 跳过，包括实际 30 秒心跳丢失与 SQLite 服务重启。补充 24 项真实 MySQL 任务用例、内部身份 5 项、模型 29 项及原有 Python 205 项回归，详见 [验证记录](temporal-runtime-verification-20261104.json)。这些组有不同覆盖范围，不能当作完整端到端验收。

`deploy/platform/temporal/provision_demo.py` 安装固定摘要的 Linux CLI，保留 LICENSE；独立系统账号运行 `open-agent-temporal.service`，仅监听 loopback，关闭 UI/新闻拉取，SQLite 位于 `/opt/open-agent/temporal/state/temporal.db`，内存限制 768 MiB。展示机已返回 SERVING，所有实际监听地址均经核对为 loopback。首次安装发现受限系统账号的默认 CLI 配置目录不可读，已通过显式禁用个人配置修复，没有放宽服务的目录隔离。

这是官方开发服务器，仅用于单机展示。生产仍需单独配置 Temporal HA、持久数据库、TLS、命名空间、备份与恢复；`start-dev` 不作为生产部署模板。删除/回滚前应停止新任务、排空 worker 并备份 SQLite，不能直接删除进行中的状态。

Context7 当前不可用，核对已安装 SDK 源码及官方文档：[Python SDK](https://github.com/temporalio/sdk-python)、[开发服务器](https://docs.temporal.io/cli/command-reference/server)、[CLI 全局配置](https://docs.temporal.io/cli/command-reference/env#global-flags)、[Spring 多 SecurityFilterChain](https://docs.spring.io/spring-security/reference/servlet/configuration/java.html)。

2026-11-04 终态清理修复：同一组命令现为 24 项通过、0 跳过（82.72 秒）。新增正常失败/超时也触发清理的检查，成功用例也必须观察一次 reconcile；仍使用真实 Temporal 开发服务验证取消、心跳丢失和服务重启。activity 在这些测试中是明确的测试实现，结果不代表生产物理清理已接通。
