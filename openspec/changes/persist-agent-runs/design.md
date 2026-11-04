# Design

## Context

core/workflow/service/chat_service.py 使用 asyncio.create_task；event_registry.py 状态依赖 Redis TTL。仓库有 Celery 基础设施但不能据此证明主执行链已使用或具备节点级恢复。DeepAgents 使用 StateBackend，未配置持久 checkpointer。

动机见 proposal.md；代码现状与证据见 docs/platform/baseline-assessment.md，实施顺序见 docs/platform/roadmap.md。

## Goals / Non-Goals

**Goals:** 落实 specs/durable-agent-runs/spec.md 的可观察契约，保留上游已有能力并记录跨服务影响。

**Non-Goals:** 不接管模型部署、网关、调度、推理和运维；不将未完成的改造标为生产能力。

## Decisions

1. 控制面持有 runs、attempts、events、tenant_quota、admission_leases 和 outbox。事务写入 run 和 outbox 后返回 run_id，异步启动 Temporal workflow，以 run_id 作稳定 workflow ID 防重复。事务唯一键为 tenant/environment/deployment/idempotency_key，同时保存请求摘要；相同 key 不同内容返回冲突。
2. 状态机 queued→running→succeeded/failed/timed_out；queued/running→cancel_requested→cancelled。取消与成功竞争通过版本化条件更新决定，终态不反转；取消已终态任务返回当前终态。SSE 不驱动执行，客户端断开后任务继续，除非显式取消。
3. Temporal 使用官方 Python SDK 管理执行和心跳，activity 边界包住可恢复步骤；外部 I/O 不放在 deterministic workflow 中。业务事件使用唯一序号和 outbox 重投去重。禁止声称把整个旧 HTTP 调用包装一次就自动得到节点级 checkpoint。
4. 首期对旧 workflow 支持持久跟踪和明确的 attempt 级重试；无法安全恢复的进程崩溃标记 failed(reason=worker_lost 或 outcome_unknown)。需要重试时创建新 attempt，非幂等工具的未知结果进入人工核对/失败，不自动重复。后续才按节点接入 checkpointer，未实现前不宣称精确断点续跑。
5. 任务准入采用数据库原子配额预留，worker 开始前再次确认租约；公平调度按租户轮转，任务在队列等待不占执行槽位，队列长度也有上限。租约带 fencing token，超时释放前必须阻止旧 worker 继续副作用。task 和 sandbox 配额分别计数，不能仅用某个进程的 Semaphore。
6. 运行记录包含 deployment/release/config 摘要、tenant/environment、主体、截止时间、幂等键、trace_id 和 usage。事件保留有期限；客户端重连用 Last-Event-ID，超出保留范围明确报 event_history_expired 并引导查询状态。
7. Temporal 成为新增基础设施依赖，但不替换所有现有 Kafka/Celery 功能。对接任务相关共享层前记录调用链与影响，先灰度迁移一条 Agent 路径，再扩至另一条。
8. 调度载荷只传 tenant/environment/run_id 和安全状态。受信 worker 以独立服务身份及数据库租约按需取得固定模型/Skill 版本，密钥和用户输入不写入 Temporal 参数或结果。租约校验同时检查 worker 身份、取消、截止时间及版本撤销；对象读取后再复查一次。所有 worker 模型/沙盒操作仍须独立校验许可，取得一次配置不代表整个任务持续获准。

## Risks / Trade-offs

[跨数据库/调度器一致性] → outbox+唯一 ID+reconciler；[重复副作用] → operation_id、下游幂等与未知结果失败；[配额泄漏/旧 worker 继续执行] → fencing+心跳+回收；[Temporal 运维成本] → 限定任务用途并纳入离线备份恢复。

## Migration Plan

新增表及独立 worker，测试租户先启用持久模式；只读展示旧运行但不伪造历史事件。回滚先停止接新任务并排空新 workflow，保留查询服务和数据库；禁止删除进行中的持久记录。
