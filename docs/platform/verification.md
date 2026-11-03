# 首次导入与规格验证记录

日期：2026-11-03。

| 检查 | 结果及边界 |
|---|---|
| 官方 OpenSpec 工具 | 固定 1.16.0；六个 Codex 技能，附 MIT 许可 |
| 锁定依赖重复安装 | `npm ci --ignore-scripts --no-audit --no-fund --offline` 成功，使用已准备的本机缓存 |
| OpenSpec 严格校验 | 七项变更通过，0 失败；28 个规划文档，35 条需求、70 个场景、51 项未完成任务 |
| 配置安全测试 | `python docker/astronAgent/casdoor/test_certificate_config.py`：2 项通过 |
| 示例工作流凭据 | 232 个 apiKey/apiSecret 字段中，224 个非空字段已改为占位值，其余为空 |
| 新文档相对链接 | 全部能解析到仓库内文件 |
| GitHub CI | 仅启用规格/配置检查；只读权限；checkout/setup-node 固定到核验的官方提交 |
| 上游许可 | LICENSE/NOTICE 字节摘要与导入前相同；OpenSpec 另附原 MIT 许可文本 |
| 私有本机文件 | 连接配置、加密凭据及服务器检查记录位于 Git 忽略的 `.local/`，不纳入源码 |

初次导入前做了文本凭据候选扫描，人工区分变量引用、占位值和测试样例；清理了发现的示例硬编码凭据及历史认证记录。这不是完整漏洞审计或对所有历史代码无秘密的保证。

以上为首次导入阶段记录。后续 Java/前端构建和业务测试进展见 [实施记录](implementation-progress.md)。主规格尚未归档；生产隔离、容量与离线环境仍须单独验收。远端实际 CI 运行状态以 GitHub Actions 结果为准。
