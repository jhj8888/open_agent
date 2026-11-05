# 展示 Worker 的环境路由更新

本次更新面向已安装且拥有 owner 标记的展示机，基线源 SHA256 为 `8c64a5475a783efd607f40d86f62e9f30967609309603497f561bd8358fbb7a0`。这是现有部署的受控升级，不是完整离线安装程序。

1. 在项目 Python 环境运行 `package_dispatch_patch.py`，生成 `.local/dispatch-source-patch.tar` 和 JSON。固定六个允许文件，记录字节大小与摘要。不得加入 `.local` 凭据或依赖环境。
2. 将归档、JSON、`stage_dispatch_patch.py` 放到服务器同一个私有验证目录，运行 staging。它核对活动基线并生成新的完整源归档/不可变目录；不切换服务、不安装依赖。摘要与实际结果见 [dispatch-artifacts.json](dispatch-artifacts.json)。
3. 用现有验证解释器运行 `verify_dispatch_patch.py`。它在冻结 worker 解释器中运行测试，启动独立 Temporal 测试服务，并确认活动部署及包版本未变化。日志和 XML 留在私有验证目录。
4. 先构建 V1.64 控制 API JAR、核对并上传到按 SHA256 命名的私有 releases 目录，更新 `control/provision_api.py` 的摘要。上传当前 control 工具包以及已安装的 recovery 工具包后，运行 `upgrade_dispatch.py`。该脚本仅允许匹配基线、空闲任务及已释放 attempt；保存私有配置/SQL 备份，关闭准入、切换源、迁移 API，确认两队列就绪后才开放准入。失败时恢复旧配置/API；新增索引不做破坏性回退。此定点升级不应在已升级服务器再次执行。
5. 先顺序上传验证工具，再同时启动真实测试 Skill 和生产模型接口检查，使两任务具有重叠执行区间；用冻结 worker Python 运行 `control/verify_environment_dispatch.py` 核验实际队列与重叠时间。最后复查 `verify_step_content.py`、`verify_public_console.py`。这些控制验证脚本共享上传目录，传输必须顺序完成，避免同时覆盖文件。

`provision_supervisor.py` 重新生成服务时保留已有显式队列设置；新安装缺省仅启用 test。生产模型队列通过受控升级设置为 `open-agent-demo-prod-v1`。两队列共用主机并发容量，不增加第二套独立配额。生产 Skill 仍需 Kata；展示机的 gVisor 路径只用于 test。

升级证据和限制见 [按环境分派任务](../../../docs/platform/environment-dispatch.md)。新增代码复用官方 Temporal SDK 1.35.0，没有引入新的 Python 包。这里保留原有 LICENSE/NOTICE，源补丁不覆盖它们。
