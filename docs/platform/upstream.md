# 上游来源与首次导入

- 目标维护仓库：[jhj8888/open_agent](https://github.com/jhj8888/open_agent)。
- 上游项目：[iflytek/astron-agent](https://github.com/iflytek/astron-agent)。
- 输入为用户提供的本地目录 `astron-agent-main-831e7b1`，导入日期 2026-11-03。
- 输入目录没有 `.git`，未验证与上游某个 commit 完全一致；目录名中的 `831e7b1` 仅为来源标签，不把它当作已核实 Git SHA。
- 上游 LICENSE 与 NOTICE 原文保留。其他目录/文件的单独许可证继续适用；商标、字体、图片和第三方依赖不因主项目许可证自动获得授权。

## 原始许可文件摘要

| 文件 | SHA-256 |
|---|---|
| LICENSE | `0a0bc9d837c53abfd178307b3cf3d41ed5131352c263927b00e09604dfddcb75` |
| NOTICE | `2163e622967254a4fb1db1f2495606d3825da78027d6ba187e751652df1fad25` |

## 首次导入的调整

新增 OpenSpec 官方 Codex 技能、锁定开发工具、七项规格、产品范围/架构文档及规格校验 CI；更新 README 和 AGENTS 以明确 open_agent 的目标与边界。原业务源码主体保留。

上游工作流移动到 `.github/upstream-workflows`，避免新仓库推送触发上游发布、社区奖励或机器人评论。后续逐项迁移适用检查，不视为业务模块测试已经通过。

输入快照含 `console/frontend/public/fonts/PingFang.ttc` 和 `D-DIN-PRO-500-Medium.otf`，没有找到随附再分发许可。文件保留在原电脑且由 `.gitignore` 排除；`global.scss` 对应 font-face 改用本机字体别名，保留原有 CSS 类名。新克隆不需要下载这些字体，字体外观可能随操作系统变化。若后续引入可分发字体，需随包包含许可证并做界面验收。

这次导入不等于依赖与历史代码已完成全面安全审计。生产交付前仍须完成版本级 SBOM、资产授权、默认口令替换和真实环境隔离验收。

## 初始化数据清理

首次导入移除了两份 Casdoor 初始化文件中的历史 tokens、sessions、records，并清空示例应用的硬编码 clientSecret；部署时须配置本环境的客户端凭据。保留上游公开示例账号配置不代表可用于生产，生产必须替换默认口令并关闭无用示例应用。

`V1.16__insert_workflow_data.sql` 的示例工作流 apiKey/apiSecret 改为明确占位值。这是新仓库首次导入的安全清理，不是已部署数据库的升级迁移；若曾使用上游迁移创建数据库，须先比较 Flyway 校验和并制定独立迁移方案，不能直接在生产库执行 repair 掩盖差异。现存数据库中的旧凭据需要单独轮换，源码清理不会吊销它们。

以上变化用初始化配置安全检查验证；未在本次执行完整 Casdoor 登录和业务数据库部署验收。
