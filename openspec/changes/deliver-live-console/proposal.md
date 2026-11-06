# Proposal

## Why

用户已认可阶段前端的样式与交互，并要求继续完成平台。当前页面仍使用明确标记的示例数据，需要保留视觉设计并接入已经实测的租户、模型、Skill、部署和持久任务接口。

## What Changes

- 新建独立真实工作台入口，复用绿色/白色设计与布局；原静态预览继续保留。
- 使用标准 OIDC 授权码及 PKCE 登录，不在前端放置服务密钥或模型密钥。
- 实现租户/环境切换、Agent 与 Skill 管理、模型配置、HTTP 调用凭据、聊天/SSE/取消与任务查询。
- 为全部保留任务提供服务端分页、状态筛选与中文名称/任务 ID 搜索，旧任务详情与文件入口不受最近部署列表限制。
- 在展示机使用独立 HTTPS 入口和受限代理，复用现有私有 API/身份服务，不影响其他应用。

## Capabilities

### New Capabilities

- `live-console`: 真实在线平台工作台。

### Modified Capabilities

无；原 interface-preview 保持独立、明确的示例边界。

## Impact

浏览器 → 专属 HTTPS 静态入口/反向代理 → Casdoor OIDC 与 Java platform/API → 现有持久执行链。只新增项目工作台构建和入口配置，不将内部 worker/reconciler 路由暴露公网，不涉及模型推理或网关。
