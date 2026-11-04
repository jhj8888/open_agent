# 阶段界面预览

这份交付用于评审界面与交互，不代表真实 Agent 平台已经集成完成。页面持续显示示例提示；所有任务、监控指标、租户、发布版本与回复均为合成数据。聊天不调用模型，Skill 选择不上传或执行文件；浏览器刷新恢复初始状态。

## 入口与体验路径

演示入口：`http://120.26.81.83/open-agent-preview/`。

1. 在 Agent 工作台筛选已有示例，或者新建一个示例 Agent 并绑定 Skills。
2. 点击「调试对话」，输入一个任务；查看模拟排队、运行、完成与执行步骤。可以在运行中取消，或新建另一个任务体验并行操作。
3. 进入任务中心查看状态和详情。切换研发中心/运营中心以及测试/生产环境，评审数据视图。
4. 浏览 Skill 资源库、发布与 API、监控概览、租户管理。HTTP 示例使用占位域名和令牌，预览站点没有执行 API。

## 本地构建

```powershell
cd console/frontend
npm ci
npm run build:preview
npm exec tsc -- --project tsconfig.preview.json
npm run dev:preview
```

开发预览为 `http://127.0.0.1:4175`。制品在 `console/frontend/dist-preview`，不提交 Git。没有新增 npm 依赖，使用仓库锁定的 React 18 与 Vite 5；构建不读取主控制台的业务配置。

## 制品与部署

在仓库根目录打包已构建的产物：

```sh
python deploy/platform/preview/build_archive.py --output /tmp/interface-preview.tar.gz
```

命令输出 SHA-256；制品包含静态资源、静态服务脚本、项目 LICENSE/NOTICE 和 React、React DOM、Scheduler 的 MIT 许可原文。脚本不打包环境文件、服务端代码或凭据。

将制品和 `provision_demo.py` 送到授权服务器的 `/opt/open-agent/preview/`，在服务器运行：

```sh
python3 /opt/open-agent/preview/provision_demo.py \
  --archive /opt/open-agent/preview/interface-preview.tar.gz \
  --sha256 <BUILD_SHA256> \
  --static-web-root /var/www/html
```

`--static-web-root` 仅适用于本次已核验的现有静态文件入口。该选项增加项目专属的 `open-agent-preview` 符号链接，指向不可变制品目录；不改原入口代码、主页或配置，也不重启原应用。删除该项目链接即可撤下公网预览，历史 release 仍保留。完整应用上线时会使用正式网关配置替代该临时挂载。

独立服务 `open-agent-preview.service` 使用非登录用户，监听 `127.0.0.1:18080`。`/healthz` 明确返回 `mode=interface-preview`、`model_connected=false`；服务有连接数、超时、内存和进程数限制。公网通过现有静态文件服务读取构建产物，不代理内部 API。页面 CSP 禁止网络请求，不包含外部 CDN、字体或统计脚本。

这里使用 Python 标准库 HTTP 服务只用于临时界面评审。它不是生产 Web 服务选择；参见 [Python HTTP 服务说明](https://docs.python.org/3/library/http.server.html)。正式平台的入口继续复用企业网关。

## 验证记录

2026-11-04：独立预览构建、全前端 `tsc --noEmit` 与独立预览类型检查通过，8 项 OpenSpec 严格校验通过。服务器独立服务健康检查通过。公网构建文件摘要逐项核对通过，许可文件可访问，非公开脚本不可访问。浏览器已核验首页布局和创建对话框，提交交互时自动化超时；完整交互矩阵尚未全部验证。用户已确认前端风格和当前功能符合预期，并要求继续真实平台实现。详细范围见 `interface-preview-verification-20261104.json`。

真实模型/Skill 执行底座的验证见已有执行器与沙盒报告；这些证据不等于当前预览页面已调用后端。统一 API、真实任务聊天联调、持久化、生产隔离与离线集群验收仍按原 OpenSpec 变更推进。
