# 离线模型契约

这是合成测试服务，不连接真实模型，也不需要真实密钥。Python 与 Spring AI 共用此 HTTP/SSE 样本。

```sh
core/agent/.venv/bin/python tests/model_contract/server.py
# Windows: core\agent\.venv\Scripts\python.exe tests/model_contract/server.py
```

默认仅监听 `127.0.0.1:18800`，使用 `/v1/chat/completions`；端口可由 `MOCK_MODEL_PORT` 设置。公开测试 token 为 `contract-test-key`。

| model | 行为 |
|---|---|
| contract-text | 普通文本、SSE、完成标记和 usage |
| contract-tools | 分片结构化工具参数；收到工具结果后回答 5 |
| contract-no-usage | 不返回 usage，调用方应显示未知 |
| contract-unauthorized | 401 |
| contract-rate-limit | 429，含 Retry-After |
| contract-timeout | 延迟 10 秒 |
| contract-malformed-sse | 畸形 JSON 事件 |
| contract-disconnect | 先返回部分文本，再中断连接 |
| contract-redirect | 307 指向元数据地址；客户端必须拒绝，服务不会访问该地址 |

运行 `core/agent/.venv/bin/python -m pytest tests/model_contract -q -p no:cacheprovider`。
测试启动临时 TCP 服务，通过官方 OpenAI SDK 验证，既检查内容也检查失败、无隐式重试和未知用量。
`test_provider.py` 进一步通过平台的 LangChain/DeepAgents 适配器检查同一服务。
