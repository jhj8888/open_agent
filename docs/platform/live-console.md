# 真实工作台

2026-11-05 展示入口：<https://120.26.81.83/open-agent/>。原 `/open-agent-preview/` 保留为明确标记的静态预览。绿色/白色设计保持一致，真实工作台不使用模拟数据。

浏览器通过 Casdoor 的标准 OIDC code + PKCE S256 登录。issuer 使用 `/identity` 前缀，避免 Casdoor 将同源工作台回调识别为自己的管理页。迁移保留签名密钥、账号和密码；前端令牌只保存在内存，短期 PKCE 状态使用 sessionStorage。模型密钥由已有服务端加密保存。

调用路径：浏览器 → 独立 Caddy HTTPS 443 → Java 租户/API → MySQL/outbox → Temporal → 独立执行进程 → 模型 API / OpenSandbox gVisor。Caddy 仅代理用户 API 与身份路径；内部调度、执行和回收接口返回 404。已有服务器上的其他应用保持原入口。

## 验证

| 项目 | 实际结果 |
|---|---|
| 真实浏览器登录 | 标准授权回调完成，显示演示租户和实际部署 |
| Agent 创建 | 页面创建“会议纪要助手”1.0.0，固定已有模型版本，后端保存后卡片可见 |
| 真实 Skill 聊天 | 任务 `bdbc5772-e492-4984-ac4e-8f67502b33ae` 成功；执行脚本得到 UID 1000、总额 5420、校验标识 SKILL-READY-5420 |
| 事件与用量 | 278 条持久事件；19,594 Token；重新连接仍为同一任务、无重复追加 |
| 独立回收 | Temporal COMPLETED，执行进程/cgroup 消失、物理沙盒消失、沙盒和任务配额释放 |
| 环境切换 | 无生产成员权限时清空测试部署/任务，发布按钮禁用，服务端拒绝读取 |
| HTTPS 与入口 | 公共 CA 校验通过，3 个静态资产摘要一致；匿名/伪造身份拒绝、内部路径拒绝、CSP 生效 |
| 用量回归 | 一项真实 MySQL 用例通过，覆盖列表与详情一致、缺失为 null、旧重复事件不回退用量 |
| 构建 | TypeScript 与 Vite 工作台构建通过；Java 21 独立入口打包通过 |

证据：`console-run-verification-20261105.json`、`public-console-verification-20261105.json`。当前静态包摘要 `d61a56e71a918d1e03db037c263995f25bcdee739eda42138a77ab3c67fbeed4`，Java 制品摘要 `983164da71af4b662f6dfcaa9ef972e3880642183406a53e09a15459fdcdcd6f`。

工作台包含模型、Skill、部署凭据、成员配额和审计表单；本批证据不声称全部表单已完成浏览器验收。自动断线重连、浏览器取消、跨租户流程、登出和生产推广继续验收。统计仅代表每个可见部署最近 50 个任务；CPU/内存监控尚未接入。故障注入产生的失败/取消记录保留，成功率不会为演示而清洗。

## 构建与发布

```sh
cd console/frontend
npm ci --legacy-peer-deps --ignore-scripts
npm run build:workbench
cd ../..
python deploy/platform/ingress/package_console.py
mvn -B -ntp -f console/backend/pom.xml -pl hub -am \
  -Dplatform.main-class=com.iflytek.astron.platform.PlatformApplication -DskipTests package
```

Java 默认仍为上游 HubApplication，部署必须检查 manifest 为 PlatformApplication、内含 platform.yml 及数据库迁移，再更新并核对 `provision_api.py` 的制品摘要。

展示机脚本位于 `deploy/platform/ingress/`：`provision_demo.py` 管理专属入口；`connect_console.py` 接入明确路由；`publish_console.py` 校验每个文件后原子切换 `current`。不可变旧版本保留在 `/opt/open-agent/console/releases/`，回滚只需按已记录摘要切换该链接。脚本要求项目所有权标记，不能用于覆盖无归属服务。

服务器 `verify_public_console.py` 使用正常登录令牌和系统 TLS 信任验证入口；`verify_console_run.py` 只检查指定浏览器任务，不重复创建任务。证书由 Caddy 自动续期；企业内网交付应使用已有网关/内部 CA，不依赖公网 ACME。

## 依赖与边界

- 新增 [oidc-client-ts 3.5.0](https://github.com/authts/oidc-client-ts)，Apache-2.0，官方 UserManager/内存存储实现标准 OIDC。2026-11-05 查询超过 1K stars。
- 复用已有 [react-markdown](https://github.com/remarkjs/react-markdown) 与 remark-gfm，MIT；安全 Markdown 禁用原始 HTML、自动图片请求并保留 URL 校验。
- [Caddy 2.11.8](https://github.com/caddyserver/caddy)，Apache-2.0，官方发行包校验摘要；只用于展示机独立 HTTPS 入口。
- 原前端依赖树仍有上游 npm audit 报告，未用强制升级破坏既有应用；投产前须完成实际工作台依赖和整个项目的供应链扫描。

展示机采用单机 Temporal 开发服务与 gVisor，不代表企业 K8S/Kata、完整断网冷启动、备份恢复及容量压测已验收。完整目标继续按 OpenSpec 推进。
