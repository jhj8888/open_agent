# OpenSpec 开发流程

使用官方 OpenSpec **1.16.0**，通过根目录 `package.json` 和 `package-lock.json` 固定。官方 [CLI 文档](https://openspec.dev/docs/cli) 与 [项目配置文档](https://openspec.dev/docs/project-config) 为配置依据。

## 安装与校验

需要 Node.js >= 22.12。在项目根目录的 PowerShell 中执行：

```powershell
$env:OPENSPEC_TELEMETRY='0'
$env:DO_NOT_TRACK='1'
$env:OPENSPEC_NO_UPDATE_CHECK='1'
npm ci --ignore-scripts --no-audit --no-fund
npm run spec:validate
npm run spec:list
npm exec -- openspec status --change manage-tenant-access --json
```

离线开发机使用企业 npm 镜像或事先完整准备的 npm 缓存；缓存完整时可加 `--offline`。镜像和缓存准备不是生产运行时工作。`npm ci` 仅安装规格开发依赖，前后端业务依赖仍按各模块原有方式安装。

## 修改流程

1. 阅读范围、基线评估和当前变更。新增行为先创建/更新 proposal、design、specs 和 tasks，写出可验证的成功与失败场景。
2. Codex 已生成六个技能，位于 `.agents/skills`。可用 `$openspec-propose` 编写提案、`$openspec-update-change` 修订提案、`$openspec-apply-change` 按任务实施；CLI 环境先查看安装版本的帮助。
3. 按 roadmap 前置顺序实施。逐项完成后记录测试证据并勾选 tasks；仅写了文档不能勾选功能任务。
4. 运行 `npm run spec:validate` 和改动模块的必要测试。OpenSpec 严格校验只检查规格结构与一致性，不证明代码正确或企业隔离安全。
5. 实现和验收全部完成后，用 `$openspec-sync-specs` / `$openspec-archive-change` 同步并归档。没有完成的变更保留在 changes 中，不创建假完成主规格。

本地 Git 开发分支默认 `codex/<change-name>`。首次源码导入使用 main；后续按变更提交。禁止 force push 覆盖远端历史。

## 目录与自动化

- `openspec/config.yaml`：中文上下文、产品边界和规范。
- `openspec/changes/<change>/`：proposal、design、specs、tasks 与 CLI 元数据。
- `openspec/specs/`：未来已交付的主规格，当前没有已验收规格。
- `.github/workflows/openspec.yml`：只读校验，不发布镜像/网站、不部署服务。
- `.github/upstream-workflows/`：保留上游工作流供迁移参考，GitHub 不自动执行此目录内容。

GitHub Actions 是源码协作侧的校验；企业部署仍须使用内网 CI/制品库。迁移到内网 CI 时复用锁定依赖和相同校验命令，不把 GitHub 设为生产可用性的依赖。

SDK/配置查证优先使用仓库要求的 Context7；若连接不可用，记录此限制并查官方文档与固定版本源码/CLI，不凭记忆猜测接口。
