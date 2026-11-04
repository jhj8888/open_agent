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

新版控制面已安装 V1.55 和独立服务身份，见 [实机接口检查](supervision-deployment-verification-20261105.json)。检查真实接口拒绝 worker/dispatcher 进入回收路由、拒绝 reconciler 读取模型凭据，并拒绝未知尝试。执行准入仍关闭；待完整真实执行与物理沙盒回收联调通过后启用。
