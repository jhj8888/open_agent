"""Stable public model errors. Provider response bodies are deliberately omitted."""
from dataclasses import dataclass

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from agent.domain.models.endpoint_policy import ModelEndpointDenied


@dataclass(frozen=True)
class ModelFailure:
    code: str
    message: str
    retryable_before_execution: bool = False


def model_failure(error: BaseException) -> ModelFailure:
    # The SDK may wrap transport errors; inspect types, never response content.
    current: BaseException | None = error
    for _ in range(8):
        if isinstance(current, ModelEndpointDenied):
            return ModelFailure("MODEL_ENDPOINT_DENIED", "模型目的地址未获平台批准")
        if current is None:
            break
        current = current.__cause__
    if isinstance(error, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return ModelFailure("MODEL_TIMEOUT", "模型调用超时")
    if isinstance(error, APIStatusError):
        if error.status_code in {401, 403}:
            return ModelFailure("MODEL_AUTH_FAILED", "模型接口认证失败，请联系管理员检查凭据")
        if error.status_code == 429:
            return ModelFailure("MODEL_RATE_LIMITED", "模型接口限流", True)
        return ModelFailure("MODEL_UPSTREAM_ERROR", "模型接口返回错误")
    if isinstance(error, (APIConnectionError, httpx.NetworkError, httpx.RemoteProtocolError)):
        return ModelFailure("MODEL_CONNECTION_FAILED", "模型连接失败或响应流中断")
    return ModelFailure("MODEL_PROTOCOL_ERROR", "模型响应不符合接口契约")
