# 真实 Agent 执行器

2026-11-04：`durable.RuntimeExecutor` 已将固定运行配置、官方 DeepAgents、受限模型传输、Skill 制品与 OpenSandbox 绑定到一条执行路径。展示机上已完成真实 DeepSeek → DeepAgents → Skill → gVisor 的验证，确认脚本以 UID 1000 执行，返回实际输出，并在结束后检查物理容器已不存在。该验证的准入与 journal 是明确标识的测试实现，尚未替代持久配额/清理器，也未开放聊天或公共 API。

## 调用链与契约

受信 activity 取得 `AttemptLease` 和 `RuntimeBundle` → `ExecutionPermit` → 固定 Skill manifest/file → 可信沙盒工厂 → `DeepAgentsRunner` → 官方模型 SDK / 沙盒原生工具 → 有序任务事件。执行器和操作许可已有实现；后续增量已加入 [持久沙盒 journal](sandbox-journal.md)，生产 activity 注册、跨进程终止证明和回收器仍待接通。

运行输入为 `{"message":"...","history":[{"role":"user|assistant","content":"..."}]}`。message 最长 32768 字符，history 最多 32 条、每条最多 8192 字符，账本原有 64 KiB 总输入限制继续有效。禁止输入额外连接信息或插件字段。

不可变部署快照的 `execution` 字段可包含 `engine=deepagents`、instructions、answerInstructions、maxLoops（1–30）、temperature（0–2）、maxTokens（1–16384）。模型连接、固定版本、CA 引用及 Skill 绑定来自受信配置，不接受聊天参数覆盖。配置错误仅返回稳定错误码，不回显 Pydantic 的私有输入。

有 Skill 时必须具备工具能力和绑定当前许可的 OpenSandboxProvider，缺少任一条件直接失败。无 Skill 时不挂载执行插件；本执行器不接受任意宿主 Python/Shell 插件。所有发布文件校验原始摘要、大小、路径和总容量，不根据 Skill 内容安装外网依赖。

## 许可、取消与清理

- 新增 `POST /internal/platform/worker/runs/{runId}/permit`，复用内部服务身份及 `RunLedger.withPermit`，检查 worker、租约、租户、截止时间和绑定版本状态。没有新授权旁路。
- 模型传输通过 HTTPX 官方 AsyncBaseTransport 接口包装既有 PinnedModelTransport，保留管理员地址白名单、DNS 固定、TLS 校验和禁止重定向；每次实际模型请求重新查许可，包括框架内部的上下文压缩请求。
- 独立 monitor 每 5 秒维护数据库心跳和许可检查，并提供官方 Temporal 心跳回调接入点。取消、截止时间或许可查询失败会关闭本次执行并取消等待中的工作；后续调用失败关闭。
- 沙盒创建前、隔离准入后、Skill staging 和每条命令的执行锁内检查许可。已在等待同一沙盒执行锁的命令不能借用先前授权继续运行。
- 沙盒关闭立即禁止新操作；销毁失败允许再次清理。物理销毁已经成功但 journal 暂时失败时，仅重试记录，不再次执行租户命令。`close()` 不充当跨进程物理回收证明，当前仍不暴露释放配额接口。

模型/工具的操作许可和周期检查不能替代生产隔离控制器对进程暂停、失联与物理资源的强制回收。相关 OpenSpec 取消/fencing/资源释放任务保持未完成。

## 事件与验证

执行器保存 execution.notice、message.delta、tool.finished 和 usage。文本拆分后写入账本，复用每事件/每任务限制；事件键在 attempt 内单调生成。默认工具事件只记录名称，工具参数和完整输出不写入诊断事件。模型 usage 包含框架内部模型调用，不把输入文本或密钥放入调度结果。

当前证据见 [验证结果](executor-verification-20261104.json)：54 项执行/传输/沙盒检查、9 项 Java 接口及状态检查通过，既有 Python 运行时回归 209 项通过。实际展示验证固定使用 DeepAgents 0.7.29、OpenSandbox 1.1.0、langchain-openai 1.7.2 和 Temporal SDK 1.35.0；模型为授权的 DeepSeek 测试接口。

`deploy/platform/worker/provision_demo.py` 校验源码包摘要，解包到独立版本目录，用固定 uv 0.10.9 和 uv.lock 安装专用虚拟环境，建立非登录 worker 账号。它不会启动尚未接通的生产 worker。可提供校验过的 uv wheel；下载代理只能是显式管理的 loopback 地址，演示安装完成后临时 SSH 通道关闭。生产离线镜像/完整依赖包及冷启动验收仍属于后续交付工作。

`verify_model_skill.py` 通过标准输入接收演示模型配置，使用静态测试 Skill；密钥不会写入沙盒、Git 或验证报告。宿主 Docker 检查只用于此受控验证脚本，正式 worker 不应获得该权限。验证不声称已完成数据库/Temporal/聊天的完整任务流程。

Context7 当前不可用；复用已安装官方 SDK 源码，并核对 [HTTPX transport 接口](https://www.python-httpx.org/advanced/transports/)、[aiohttp 测试服务器](https://docs.aiohttp.org/en/stable/testing.html)、[uv 配置](https://docs.astral.sh/uv/reference/environment/)。未新增模型网关、推理或 GPU 运维组件。
