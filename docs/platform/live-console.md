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
| 浏览器取消 | `feb1bfb5` 从运行中变为 cancelled；执行进程、沙盒与配额均经独立核验释放 |
| 并发与自动重连 | `6289b572` 与 `4768639c` 同时运行，使用独立进程/租约；专属入口 SIGKILL 后 5.72 秒恢复，原任务继续成功。浏览器无手工重连，最终 284 条事件与数据库一致 |
| 登出 | Casdoor 清除会话并返回工作台登录入口；应用回调白名单同时登记 callback 与登出落点 |
| 生产发布与凭据 | 模型版生产任务 `e21cd081` 成功，219 Token；Skill 同摘要推广成功；旧/撤销凭据被拒绝，轮换凭据保留原任务查询权 |
| 发布事务回归 | 4 项真实 MySQL 测试覆盖失败/取消/未回收/摘要不匹配、指定任务证据及失败回滚 |
| 网络回归 | 源端口重叠误拦截已复现并修复；3 个固定源端口连通，其他网桥新建连接仍被拒绝；修复后真实 Skill 任务 `6562f262` 成功并回收 |
| 构建 | TypeScript 与 Vite 工作台构建通过；Java 21 独立入口打包通过 |

证据：`console-run-verification-20261105.json`、`public-console-verification-20261105.json`、`production-promotion-verification-20261105.json`、`console-recovery-verification-20261105.json` 与 `network-reply-guard-verification-20261105.json`。

当前静态包 SHA256 `4be168bb11daca0978e0593490ff9f9ee38f392d29d77e3140234e47a90e1e29`；Java 制品 `cb25d4d6d604e4ae06bbd650e43b59da7c05973d8e0f13046f251b1f195ff6d9`；worker 源包 `c333a37c94a030b866452e262c6aedde24245f938ede54423b996738cfaad8a6`。

工作台包含模型、Skill、部署凭据、成员配额和审计表单；完整管理表单及跨租户浏览器流程继续验收。统计仅代表每个可见部署最近 50 个任务；CPU/内存监控尚未接入。生产 Skill 已完成发布门禁和制品推广，执行仍要求 Kata，不在 gVisor 展示机放宽。故障注入及修复前真实失败/取消记录保留，成功率不会为演示而清洗。

展示账号按职责分离：`demo` 为测试/生产 tenant_admin，管理模型、凭据与成员；`publisher` 为 operator，负责推广，不能管理密钥。生产执行权限按部署显式登记。凭据只保存在本机私有记录和展示机 root 可读文件，不写入 Git。

## 网络故障与恢复

`b4b5a9f0`、`303eb8c3` 在 runtime 读取阶段失败，另一次验证脚本连接 Casdoor 超时。已定位项目 INPUT 全状态端口规则与 Linux 临时端口范围重叠：固定源端口 46137 超时而 45937 成功，规则累计丢包可见。新增 conntrack 条件并迁移旧重复规则后，46141、46441 回包成功，其他 bridge 对沙盒端口的新连接仍被拒绝并增加 DROP 计数。迁移不 reload/flush 共享防火墙。语义依据 [iptables conntrack 手册](https://man7.org/linux/man-pages/man8/iptables-extensions.8.html)。

执行配置获取另增加一次有限传输重试，重试前必须续约成功；HTTP 拒绝、无效 JSON、取消不重试，模型/工具不重放。只记录固定分类，保留单次总时限，采用 [HTTPX 官方异常类型](https://www.python-httpx.org/exceptions/)。相关传输/核验回归共 63 项通过。

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
