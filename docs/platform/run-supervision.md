# 独立执行监督与回收

调用链为 `Temporal activity → 主机 supervisor → reconciler 专用 HTTP → RunSupervision → RunLedger / RunSandboxJournal → MySQL`。另一条链是 `systemd 非 root executor → worker HTTP → 模型/Skill/事件`。supervisor 身份与 worker 身份分别配置；执行器及租户沙盒不得取得 reconciler 凭据或主机 Docker/systemd 权限。

此次共享层影响限于平台内部认证链增加 `reconciler` 角色及 `/internal/platform/reconcile/**` 路由，保持已有 worker、dispatcher、用户 JWT 和旧业务接口的权限不变。`PLATFORM_RECONCILER_WORKERS` 明确绑定回收身份与执行身份；缺失或非法配置拒绝操作。V1.55 新表通过 lease 外键关联既有 attempt，沿用 tenant → environment → run → attempt → supervision / sandbox 锁顺序。所有物理检查均在事务外，事务内仅写记录、审计及配额。

领取任务与登记 supervisor、主机 boot ID、固定 systemd unit 在一个事务中完成；`prepared → launching` 授权仅领取一次。启动后登记 systemd InvocationID；取消后仍允许原 supervisor 补录实体信息。启动响应丢失时必须观察同名 unit，禁止再启动一份。未知启动不以一次空查询推断从未启动。

只有绑定的回收身份可提交停止证明。证明包含 UUID、主机 boot ID、unit、InvocationID、沙盒 ID 和保留证据的 SHA-256；它是受信主机控制器的断言，控制面不能单凭 HTTP 自行验证内核进程。控制器必须先保存证据、停止整个 cgroup 并独立确认物理资源消失。身份密钥只能配置在对应主机控制器；不能把同一身份复制到不同主机或交给 executor。

控制面还会检查既有绑定、终态、一次性启动阶段及沙盒创建阶段。未领取启动授权、已观察并停止的 unit、已核实的原主机重启分别使用不同证明类型。尚未确定 ID 的 `creating` 记录始终保持配额；发现唯一匹配实体后才能补录 ID，再独立删除、核验。worker 的销毁回报和租约过期均不构成独立证明。证明、两类配额与审计在同一事务提交；完全相同的证明可幂等重投，变更证明会冲突。

Context7 在本会话不可用。事务采用 [Spring 官方 TransactionTemplate](https://docs.spring.io/spring-framework/reference/data-access/transaction/programmatic.html)，角色路由沿用 [Spring Security 官方授权接口](https://docs.spring.io/spring-security/reference/servlet/authorization/authorize-http-requests.html)，不新增框架依赖。数据库和接口验证只证明控制面契约；真实 systemd/Docker 核验与 Temporal 接线需要独立实机验收。生产 Kubernetes 须另以 Pod/Job UID、RuntimeClass 和节点状态核验，不能使用主机 systemd 证明替代。

## 已实现的主机进程适配

`core/agent/durable/host_process.py` 使用 [官方 systemd-run](https://github.com/systemd/systemd/blob/v255/man/systemd-run.xml) 和 `systemctl show/stop`。unit 名由 lease UUID 固定推导，执行用户为 `open-agent-worker`，显式设置 `KillMode=control-group`、不自动重启、只读系统、无 capabilities、禁止访问 Docker/system bus。凭据通过 [LoadCredential](https://github.com/systemd/systemd/blob/v255/man/systemd.exec.xml) 传入只读凭据目录，内容不放入命令行或 systemd Environment。unit 保留到回收，校验 InvocationID 和 cgroup 路径后才停止；cgroup v2 的 `populated` 覆盖所有子组。

主机适配本身没有数据库启动授权，调用者必须先完成 `begin`，且不得重试不明结果的启动。调用 `stop` 前必须持久记录观察到的 InvocationID。主机适配输出的材料仅证明执行进程；最终释放还必须独立核验沙盒及在途创建。`SupervisorClient` 检查服务、worker、run、lease、unit 及每一步响应状态；未知或不一致响应不能授权下一步。

测试服务器 systemd 255/cgroup v2 的实际验证见 [进程核验记录](process-verification-20261105.json)：UID 989、无 capabilities、无法访问 Docker/system bus、可读取授权凭据副本但不能读取源文件；错误 InvocationID 和重复启动被拒绝；父进程及其子进程全部被清除，重复停止可再次核实。使用合成子程序，没有据此宣称实际 RuntimeExecutor/Temporal 接线完成，也没有重启共享主机。

新版控制面已安装 V1.55 和独立服务身份，见 [实机接口检查](supervision-deployment-verification-20261105.json)。检查真实接口拒绝 worker/dispatcher 进入回收路由、拒绝 reconciler 读取模型凭据，并拒绝未知尝试。该次检查时准入关闭；下述真实模型任务接线之后，仅在已认证的 loopback API 开启演示准入。

## 真实执行接线的调用边界

后续接线沿用 `OutboxDispatcher → Temporal → supervisor → systemd executor → RuntimeExecutor`。调度载荷只含 RunRef；executor 按租约读取模型及制品。独立 run lookup 包括已释放尝试，避免把扫描中未出现当成释放证明；其他 supervisor 的已准入任务返回不可访问。执行 activity 失败且仍处于未准入 queued 状态时，由新增账本方法在既有锁顺序下提交 failed/outcome_unknown（截止时间已到则 timed_out）。它只处理 attempt_no=0，不改变已开始或已有终态的任务；不涉及旧 Kafka/Redis 或外部 I/O。

主机隔离核验代理使用 Unix socket 和每次尝试专属 capability。executor 只能请求前后核验，不能传入主机命令；root 代理按控制面 binding、当前许可及完整容器标签重新核对。真实调度接线、该代理以及物理沙盒清理需另存联调证据，不能以此前合成子程序验证代替。

## 模型任务的真实闭环

`service.py` 注册真正的 execute/reconcile activities、outbox 调度及独立定时清理；`child.py` 在非 root 进程读取当前租约授权的模型配置，通过现有 RuntimeExecutor 执行。Temporal 仍只保存 ID/状态。启动失联不会重试已授权的执行；supervisor 重启会先 fencing 遗留尝试。执行与回收身份密钥保持独立，任何未确认的物理清理保留配额。

2026-11-05 展示机实际验证：真实 Casdoor PKCE 登录 → Agent HTTP 幂等提交 → MySQL outbox → Temporal → systemd 非 root executor → DeepSeek → 持久消息/usage → 终态 → 独立停止 cgroup → 证明与配额事务提交，耗时 18.556 秒，51 条事件、86 字回答；Temporal COMPLETED/succeeded，attempt 与 supervision 均 released。报告见 `model-run-verification-20261105.json`。首次两轮启动因 supervisor 的 UMask=0077 令参数目录不可遍历而失败，未调用模型；失败已完整回收。修复仅将无敏感内容的参数目录显式设为 0755，凭据及核验材料仍 0700/0600。

展示机 `provision_supervisor.py` 只管理带自身 owner 标记的 systemd 服务及根目录。API 的显式 admission 记录需匹配仍在运行的 ready PID，安装器才开启准入；API 始终绑定 127.0.0.1:18082。本阶段 worker 没有 sandbox 适配配置，绑定 Skill 的执行在模型调用前失败关闭，不能作为 Skill 完整验收。前端仍为原展示页，生产 Kubernetes、多租户故障演练和浏览器完整联调尚待完成。
