# 实施记录

## 2026-11-06 当前增量

模型连接能力诊断已部署：按实际配置验证流式文本、固定工具调用和结果回传，保留 DeepSeek 思考模式所需历史字段；展示每步耗时/用量并保存脱敏环境审计。53 项 Java、48 项 Python、前端构建和 11 项规格检查通过。真实 DeepSeek 思考模式三步共 4.38 秒，审计逐字段一致；Chrome 再次点击通过，生产切换清空结果并禁用无权限验证。维护前私有备份，API 维护 45.595 秒，原 58 步/2 文件/缓存和周月统计、公网权限、服务健康复查通过。当前集群准入仍关闭，完整模型接入复合任务继续保留未完成，见[模型能力诊断](model-capability-probe.md)。

任务历史分页与检索已交付：不再受最近 100 部署/每部署 50 任务限制，每次重新执行租户、环境与生产自有任务授权；支持中文名称、ID 前缀、状态及每页数量选择。23 项相关 Java 检查、前端构建、11 项规格检查通过；真实 MySQL 同毫秒 116 条任务和 105 个更新部署覆盖旧列表上限。公网 31 条任务分 5 页与数据库逐条一致，Chrome 实测翻页、中文搜索、环境切换、旧任务预览及两份文件下载摘要通过。初次公网中文搜索暴露 ASCII ID 参数转换问题，已修复并回归；最终 API 维护 44.201 秒且预先备份。旧 58 步/2 文件/缓存统计与健康复查通过，详见[任务历史](task-history.md)。不替代完整管理表单、工作流或生产集群验收。

SQL/HTTP/Temporal 联合投递故障验收通过：观察真实 InnoDB 锁等待后强杀提交前 API、提交后重启、领取后调度进程退出、Temporal 接收后确认前退出、确认后退出和投递前取消均有证据。完整用例重跑 202.1 秒通过，4 条投递记录、3 个 attempt 与 3 条合成执行事件符合预期；两次实际等待 60 秒领取到期，无重复执行。新增 CI 门禁与复现文档，既有双 API 回归通过。运行时没有改动，演示站原 58 步/2 文件/Token/公网权限及服务状态复查通过；完整独立回收故障矩阵仍未关闭，见[投递故障验收](dispatch-recovery.md)。

多 API 实例准入已完成验收：两个实际 JAR/JVM 经真实 JWT/HTTP 共享 MySQL，12 次重复提交只产生一个任务与 outbox；队列上限、429 重试提示、执行槽位、待回收占额、跨租户/环境和 API 强杀重启均通过。20 项 Java 检查、11 项 OpenSpec 校验通过。新 API 已部署，维护前私有备份，维护 32.234 秒；旧 58 步、2 文件摘要、缓存/新增 Token、四个统计周期及公网权限复查通过。关闭 `persist-agent-runs` 1.2，其余完整调度故障、物理集群及容量验收继续推进。见[多实例准入](multi-api-admission.md)。

V1.69 已部署展示 API，维护前私有 SQL 备份，维护 62.507 秒。增加旧备份恢复时的删除清单合并与快照之外任务的原请求键拒绝记录。26 项 Java 检查、21 阶段真实 MySQL/S3 演练通过；原 58 步、2 文件、缓存 Token、周/月统计及公网授权复查通过。隔离 Restic 恢复及生产切换边界见[删除恢复](deletion-restore.md)，不扩大为生产零 RPO/完整灾备承诺。

工作台公共管理表单修复同步校验失败后提交按钮无法恢复的问题，并加入同步重复提交拦截。TypeScript/Vite 构建、11 项规格检查及部署后 HTTPS/资产/21 项用量对照通过，静态包已发布到展示机。浏览器交互仍未验收，未关闭完整管理表单复合任务，见 [工作台说明](live-console.md)。

任务删除生命周期已交付展示 API V1.68 与工作台：当前环境管理员确认删除已终结且资源释放的任务，隐藏详情/事件/步骤/文件，精确键异步清理，成功后释放产物逻辑配额，历史计量和审计保留。新增 6 项真实 MySQL 用例、CORS/HTTP/启动回归及 17 阶段 MySQL/S3 演练通过；公网仅删除新建合成任务，幂等、入口拒绝、后台回执验证通过。维护 41.568 秒，旧 58 步/2 文件/Token/公网复查通过。浏览器交互、旧备份恢复时后续删除记录合并及生产验收仍待完成，见 [任务删除](task-deletion.md)。

未完成 multipart 上传自动清理已部署到展示 API：复用已有 AWS Java SDK，精确键中止、残余分片检查、截断/错误退避接入现有对账。29 项 Java 检查、11 项规格校验与 14 阶段真实 MySQL/S3 演练通过；5 MiB 首分片写入后强杀 JVM，实际生产对账清理后独立 boto3 确认不存在，保留文件摘要正确。维护前私有备份，维护 41.879 秒，原 58 步/2 文件/缓存统计及公网权限复查通过。未覆盖所有并发传输/中止竞态、任务删除及生产集群验收，见 [分片清理](output-multipart-cleanup.md)。

V1.67 已部署到展示 API：修复对账完成后旧上传迟到写入的孤立对象漏清理，新增按日复查和失败退避调度。旧制品真实复现失败，新制品九阶段 MySQL/S3 演练通过，最终 4 个失败上传已清理、2 个已发布文件保留；27 项 Java 检查通过。维护前保存私有 SQL 备份，维护 41.65 秒；更新后原任务 58 步/2 文件下载摘要、缓存 Token、四个统计周期及公网权限复查通过。真实 multipart 中断/分片治理、任务删除和生产集群验收仍未完成。见[对账恢复说明](output-reconciliation.md)。

任务产物存储新增真实 S3 故障演练：固定已部署 JAR/官方 SDK，临时 S3 强杀及重启、读写/删除不可用错误、对象写入后上传 JVM 强杀、独立进程读取与精确键幂等清理均通过；保留样本内容/摘要一致。复现并修复演练空卷 copy-up 将属主改回 1000 的问题，测试容器/卷/凭据已清理。没有修改生产执行类或业务数据；数据库自动对账、multipart 中断及任务删除仍待完成。见[演练说明](output-store-recovery.md)。

任务产物 reader 并发修改检查已交付测试服务器：真实 Linux 管道背压下 30 次追加/截断/覆盖（恢复原 mtime）均拒绝，稳定 4 MiB 文件内容/摘要正确；4 项测试通过并加入 CI。源码 SHA256 与此前 gVisor 验收相同，生产执行代码未变。该证据覆盖 reader，不替代完整父进程/SDK/Kata 或对象存储中断验收。见[文件竞态证据](output-races-verification-20261106.json)。

周/月统计新增真实数据库逐条独立核算，四个自然周期的任务数、Token、缓存/新增、每日 UTC+8 归属、平均/P95 与线上接口一致；本周/月各 31 任务、49 模型调用。只读脚本已交付展示机，不重放业务。浏览器工具初始化超时，CSV 下载仍待补验，不关闭统计复合任务。见[核算证据](period-reports-verification-20261106.json)。

后端已加入已绑定沙盒的控制器停止创建确认：读取当前租约与 executor 终止凭据，按固定 CR UID/resourceVersion 条件将副本数设为 0，仅接受当前 generation 的完整确认，不删除 Pod 或释放配额。固定官方 controller 已实际创建 Pod，后端完成确认、重启后不重建、过期版本冲突和删除中 CR 拒绝验证；业务账本/终止凭据为合成输入，无 kubelet/Kata/CNI。70 项本地、222 项 Linux overlay、280 项最终镜像、16 项打包与 11 项规格检查通过（重叠用例不相加）。新镜像约 706 MB 已交付测试服务器，原 58 步/2 文件/缓存 Token/公网权限复查通过，活动展示 worker 保持原执行链。物理停止及持久回收、HTTPS admission、真实目标集群和离线容量验收仍在推进。见[控制器收敛](cluster-controller-quiescence.md)及[UTC 2026-11-05 验证记录](cluster-controller-verification-20261105.json)。

## 2026-11-05 当前状态

沙盒独立资源核验适配器已接到 V1.66 准备/绑定事务：实际 namespace/RuntimeClass 身份、唯一网络策略、配额、CR/Pod 标签/ownerReference/权限和固定镜像均检查；重复观察保留首次 UID/版本，取消后不恢复执行。203 项 Linux overlay、261 项新离线镜像和 16 项打包检查通过。真实零节点 K3s 验证实际 UID 和四类资源变更拒绝，发现并显式兼容固定官方 CRD 的 Retain 默认值，临时集群已清理。镜像交付至测试服务器，旧 58 步/2 文件/Token 与公网授权复查通过。HTTPS admission、controller 收敛/物理停止、真实 Kata/CNI 和完整离线/容量验收仍待完成，活动展示执行链未切换。见[独立核验说明](cluster-sandbox-verification.md)和[本轮证据](cluster-sandbox-verification-20261105.json)。

集群创建明确拒绝恢复已加入，展示 API 更新至 V1.65：完整核心 401/403/422 与 Audit-ID 可形成不可替换的拒绝凭据，任务失败和执行许可撤销原子提交，独立核验资源不存在后释放。46 项 Java、Linux overlay 138 项及新离线镜像 163 项检查通过；真实零节点 K3s 验证三类拒绝，使用合成账本并已清理夹具。原任务 58 步、2 文件下载、缓存 Token 和公网权限复查通过。集群身份仍未配置，未知创建、Kata/CNI 与生产联合验收仍待完成。见[恢复边界](cluster-create-rejection.md)和[验证记录](cluster-rejection-verification-20261105.json)。

缓存 Token 明细已部署：模型步骤折叠摘要、详情、任务汇总和周/月每日表格区分缓存与新增，CSV 增加对应列；未知保留为未知，原总量不变。7 项真实 MySQL、5 项 Spring、3 项前端格式检查及构建通过。旧任务 20 页、四个自然周期、越权与公网用量复查通过；真实浏览器已确认任务和周报显示，CSV 下载事件超时未计入实测通过。见[缓存明细证据](cache-tokens-verification-20261105.json)。

集群执行部署接线已加入：独立 test/prod supervisor/executor 身份、RBAC、网络策略、Pod 准入和原生 API TLS；42 项 Helm 与 8 项 Java 检查通过。真实零节点 K3s API 的双环境共 48 个越权/不合规请求被拒绝，两个正常未启动 Pod 回收路径通过且夹具已清理。新版离线镜像 137 项检查通过，演示站旧 58 步/2 文件/Token 与授权复查通过。镜像和部署包作为独立交付制品保存在测试服务器，活动演示执行链未切换。真实节点/CNI/Kata、未知创建恢复、企业证书与完整离线/容量验收仍未完成，集群准入关闭。见[安装接线](../../helm/open-agent-execution/README.md)和[验证记录](execution-chart-verification-20261105.json)。

集群 Python 镜像已完成独立构建与验证：155 个 Linux wheel 按锁文件核验并断网安装，官方基础镜像与 Registry 对应，生成约 706 MB 镜像归档。最终镜像在 UID 10001、只读根目录、无网络条件下通过 130 项回归；缺包/错误哈希的实际 pip 安装均拒绝。原任务 58 步及 2 个文件、公开入口再次核验通过。交付制品位于展示服务器，活动 worker 未切换；实际 K8S/CNI/Kata、空节点冷启动、最小运行依赖及完整许可审查继续推进。见[镜像说明](../../deploy/platform/worker_image/README.md)和[验证证据](worker-image-verification-20261105.json)。

常驻集群监督服务与 Temporal 活动已实现，在测试服务器独立 overlay 中 105 项检查通过。真实 Temporal + 零节点 K3s API 验证创建、第二副本保留健康任务、取消、未启动证据、finalizer 和释放；API 实验使用合成账本，不代表完整业务数据库/K8S 联调或物理隔离。临时容器、卷、网络和 admin 凭据已清理，展示入口复查通过。集群镜像/Helm/RBAC/NetworkPolicy、Kata admission/独立回收和真实节点验收仍未交付，集群总准入保持关闭。见[监督服务实现与证据](cluster-supervisor.md)。

环境路由增量已部署到展示 API V1.64：SQL 领取、分派器和 activity 校验 test/prod，未配置执行路由的环境拒绝提交且不创建排队任务。两个实际 Temporal 队列共用主机容量，真实测试 Skill 与生产模型任务均成功并释放资源，工作流重叠 22.667 秒。43 项 Java、冻结 Linux 63 项与 Helm/交付 29 项检查通过；维护 80.14 秒，旧任务 58 步和 2 个下载文件保留。仅证明同主机逻辑队列分离，生产 Kata/K8S 及集群总准入保持未验收；详见[环境路由与证据](environment-dispatch.md)。

未启动 Pod 的正向删除屏障已在 V1.63 完成账本与真实无节点 K3s API 检查，见[未启动回收证据](cluster-unstarted-pods.md)。未知创建、已绑定节点缺少真实终止记录、完整集群执行服务及 Kata/CNI 仍待交付。

集群执行 SDK 适配继续推进：官方 Kubernetes Python 36.0.3、Pod 调度门禁、UID/配置核验、终止证据先提交与 finalizer 恢复、受信集群 executor 入口已加入。298 项兼容回归通过，最终 Linux SDK/入口检查 65 项通过；7 个增量 wheel 离线安装在展示机专用目录，活动 worker 环境未更改。完整集群 Temporal 服务、未知创建回收协议、OpenSandbox/Kata 物理核验和真实 K8S/CNI 验收尚未完成，集群任务准入仍关闭。见[SDK 验证记录](cluster-sdk-verification-20261105.json)。

集群执行控制协议已加入并部署到展示 API V1.62：目标/namespace UID/Pod UID 与租约绑定、单次启动、不可替换的终止证据及原子回收事务通过 104 项 Java 检查，含隔离数据库强制重启；Python 传输与单机兼容 27 项通过。更新后真实 Skill 任务成功并释放沙盒/执行配额，旧任务步骤和文件仍可访问。集群执行身份未启用，实际 Pod/finalizer、OpenSandbox/Kata 核验与 K8S 故障矩阵仍在推进；详见[集群执行接入](cluster-execution.md)。

控制 API/工作台镜像与专用 test/prod Helm 包已生成：固定输入摘要、离线构建/导出、私有仓库引用核验工具、独立 Secret/命名空间、资源/探针/PDB/HPA 与默认拒绝的 NetworkPolicy 已接通，29 项检查通过。新镜像在独立恢复数据上通过真实工作台登录、197 个步骤查询和 12 个产物摘要验证。展示入口仍使用此前已验收的运行部署；新交付包没有替换线上执行链。集群执行器未完成，因此 chart 强制关闭新任务准入；实际 K8S/CNI/Kata、私有仓库导入、完整离线冷启动及容量验收继续推进。见[交付说明](../../deploy/platform/images/README.md)与[验证记录](platform-delivery-verification-20261105.json)。

展示环境加密冷备份与隔离恢复已通过：44,255 个文件全仓库校验，业务/身份库内容一致，真实登录后 12 个任务产物下载摘要一致、197 个步骤可读且跨租户访问拒绝。维护 132 秒，恢复至接口验收 143 秒；四个 SQLite 库含 MLflow 202 个 span 完整性通过。使用固定 Restic 0.19.1/BSD-2-Clause，备份与演练均有持久维护记录和失败恢复。见[备份手册](../../deploy/platform/backup/README.md)。这是单机数据恢复实测，定时/PITR、跨机完整离线安装、生产 K8S/HA 与容量验收继续推进。

逐步正文已部署：真实 Skill 任务 `e6eaaf2e` 的 58 步、6 次模型、4 次工具，展示增量输入、实际 think、工具参数/结果与输出；2 个文件可预览下载。模型连接可选择 DeepSeek 思考模式，旧不可变版本保持原行为。自然周/月、每日趋势、P95 与 CSV 已接入。私有 MLflow 3.18.0 与持久 exporter 已接通；97 个依赖 wheel 离线安装、实际网络隔离和重启保留通过。停服期间真实任务 `423ee867` 成功，52 步 / 5 次模型 / 17,432 Token，恢复补发不重复计量或执行业务。见 [逐步观测](agent-execution-observations.md) 和 [MLflow 后端](mlflow-observability.md)。

新增任务产物持久化已部署：逐工具归档 StateBackend/沙盒交付文件、任务详情列表、文本预览与受权下载。真实成功/取消/失败任务及 2 MiB 浏览器下载已验证；物理沙盒回收和 API 重启后摘要保持一致。当前边界及证据见 [任务产物](task-outputs.md) 与 [验收记录](task-output-verification-20261105.json)。

真实工作台已部署到 HTTPS 展示入口，保留用户认可的设计。标准登录、页面创建 Agent、真实 DeepSeek + DeepAgents + Skill 脚本执行、持久事件/用量、独立沙盒回收及未授权环境切换已验证。详见 [真实工作台与证据](live-console.md)。下文按日期保留早期增量及当时限制，不能将早期“尚未部署”当作当前状态。

生产推广、独立回收后生成同版本 Skill 证据、生产 HTTP 凭据轮换/撤销已通过实机验证。浏览器取消、自动断线重连和双任务并发已完成；入口中断 5.72 秒后原任务继续成功，284 条事件与持久记录一致。已复现并修复沙盒端口规则误拦截宿主返回流量，63 项 Python 回归及修复后真实 Skill 任务通过。

项目仍在推进：未知创建恢复、所有管理表单及跨租户浏览器验收、离线冷启动、监控、备份恢复及容量验收尚未全部完成。生产 Kata/K8S 仍需目标环境验收。

补充完成会议纪要 Skill 的浏览器上传、模板预览、部署与真实模型执行；首次登录登记和无权限页面已实测。独立第二租户的 13 项 HTTPS 越权检查通过，浏览器刷新后只见授权租户，生产切换明确拒绝。登录刷新 5 项测试、账号接入 7 项 Java 测试通过；详见 [展示验收](console-acceptance.md)。这些结果不替代旧 Hub 全部资源入口和所有管理写操作的完整矩阵验收。

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
