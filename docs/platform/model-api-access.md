# 模型接口接入

本平台只调用已有的 OpenAI Chat Completions 兼容接口。模型部署、网关、调度、推理和 GPU 运维由模型团队负责。

## 调用路径与兼容性

新管理入口：React 租户/环境页 → `PlatformModelController` → `TenantAccessService` → 模型版本表与 JCE 凭据解密 → Spring AI 官方客户端 → 已批准模型接口。

原执行入口：Workflow Agent → `BaseApiBuilder` → LangChain OpenAI SDK/DeepAgents；独立 Agent → `ChatModelFactory` → Spring AI。两端均接入目的地址校验；不修改共享 Kafka、Redis 或对象存储协议。Java 流取消直接释放订阅，不必等待模型再输出一个 token。

私有平台入口目前只开放 OpenAI-compatible 协议。上游 Anthropic/Google provider helper 保留在源码和独立适配测试中，但没有经过本平台安全传输层验证，不能从私有执行入口启用。缺少凭据时不再自动访问公网 MaaS；`SKIP_SSL_VERIFY` 不再作用于模型调用。迁移旧部署前必须配置下面的白名单、CA 和显式模型凭据。旧模型 ID 与原表保持原样；新的版本化连接由管理员在相应环境显式登记，不自动授予生产访问权。

## 运维配置

Java 和 Python 使用相同的目的地环境变量，值为 JSON 数组：

```dotenv
MODEL_ALLOWED_ORIGINS=["https://llm.example.internal:8443"]
MODEL_ALLOWED_CIDRS=["10.40.0.0/16"]
MODEL_DENIED_ADDRESSES=["10.40.0.1"]
MODEL_PUBLIC_DEMO_ORIGINS=[]
```

URL 必须没有用户名、密码、查询参数和片段。origin 与 IP 范围同时校验；DNS 返回混合安全/危险地址时拒绝。Python 通过 HTTPX 公开 transport 和 `sni_hostname` 扩展固定已验证 IP，保留原 Host/SNI；Java 通过 Reactor Netty 公开 resolver API 固定 DNS 结果，两个 Spring AI 客户端共用同一策略。禁用代理环境变量、重定向与自动请求重放。元数据地址、环回、link-local、多播、Kubernetes API 地址不能靠登记 URL 放行。运维应同时以 NetworkPolicy 限制可达网络。

公网展示接口必须同时列入 `MODEL_ALLOWED_ORIGINS` 和 `MODEL_PUBLIC_DEMO_ORIGINS`，仅允许 HTTPS。该配置仅用于已授权测试环境，不是生产默认值，也不是错误回退目标。

Python 的额外 CA 文件使用 `MODEL_CA_FILE`；Java 新连接使用 `MODEL_CA_FILES` 映射，例如 `{"corp":"/run/model-ca/corp.pem"}`，界面只填写 `corp`。CA 文件由运维挂载，用户不能提交任意服务器路径。

Java 凭据存储使用 JCE AES-256-GCM。`MODEL_CREDENTIAL_KEYS` 为 key id 到 Base64 编码 32 字节随机密钥的 JSON 映射，`MODEL_CREDENTIAL_ACTIVE_KEY` 指定新版本使用的 key id（默认 `v1`）。通过 Kubernetes Secret 或权限为 0600 的服务环境文件注入；没有默认密钥。备份数据库时需单独备份密钥。旧 master key 在所有关联版本迁移完前不能移除。

每次更新创建模型配置与凭据的新版本。密文的附加认证数据绑定 tenant/environment/connection/version；跨租户、环境或版本搬移密文会解密失败。勾选“吊销旧版本”会标记所有旧版本不可用。平台 API 不返回 API key、密文或 nonce，只返回 credential_ref。解析只发生在可信服务，沙盒不接收这些字段。

## API 示例

请求都需要已有 Casdoor JWT。环境角色经过服务端实时检查。读取使用成员权限；保存/轮换需要租户管理员；测试调用需要测试环境执行权限。生产模型调用须通过后续的发布执行授权入口。

```http
POST /platform/tenants/space-10/environments/test/models
Content-Type: application/json
Authorization: Bearer <user-token>

{
  "displayName": "内部测试模型",
  "endpoint": "https://llm.example.internal:8443/v1",
  "modelName": "team-provided-model",
  "apiKey": "<通过安全渠道配置的密钥>",
  "supportsStream": true,
  "supportsTools": true,
  "supportsUsage": true,
  "connectTimeoutSeconds": 10,
  "readTimeoutSeconds": 90,
  "totalTimeoutSeconds": 180,
  "caRef": "corp",
  "expectedVersion": 0,
  "revokePrevious": false
}
```

`GET .../models` 返回脱敏配置；`PUT .../models/{connectionId}` 带当前 `expectedVersion` 更新，版本不匹配返回 409；密钥留空继承当前密钥并重新加密。`POST .../models/{connectionId}/probe` 使用合成提示检查流式连接，并显示 reported/unknown 用量。当前 probe 不证明工具调用能力，界面明确显示“工具调用尚未验证”；发布前工具能力探测仍待接入。

## 验证记录与剩余工作

- [共享 HTTP/SSE 样本](../../tests/model_contract/README.md)：离线文本、SSE、分片 tool_calls、401/429、超时、畸形数据、中途断流和重定向。
- Python 共享契约与原 BaseBuilder/LLMCompletion/DeepAgents 回归合计 118 项通过；Java 共享契约与地址/凭据作用域测试通过。
- MySQL 8.4.13 六项迁移/约束/授权测试通过，包含 V1.50 模型版本及跨环境外键拒绝。
- 仍需将模型 connection/version 引用绑定到不可变 Agent/Skill 发布和持久任务，完成内部服务凭据引用解析、能力验证持久化及完整 UI 联调。旧资源入口统一租户授权随 `manage-tenant-access` 继续完成。
- 已授权公网 DeepSeek 探测不替代生产禁外网环境的真实模型验收；OpenSpec 的生产验收项保留未完成。

官方 API 依据：[HTTPX transports](https://www.python-httpx.org/advanced/transports/)、[HTTPX SNI 扩展](https://www.python-httpx.org/advanced/extensions/#sni_hostname)、[Reactor Netty HTTP Client](https://projectreactor.io/docs/netty/release/reference/http-client.html)。具体构造参数另通过锁定版本安装包核对：HTTPX 0.28.1、Reactor Netty 1.2.8、Spring AI 1.1.2。Context7 在本次环境中未提供工具，使用上述官方文档与安装源码。
