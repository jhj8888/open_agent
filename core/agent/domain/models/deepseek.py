"""Narrow compatibility shim for langchain-deepseek 1.1.1 tool history.

The official adapter reads reasoning_content but its inherited request serializer
omits it. DeepSeek requires the original field on subsequent requests with tools.
All HTTP, SSE, usage and tool serialization stay in the official integration.
Remove this override when the upstream serializer preserves the field itself.
"""

from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class PlatformChatDeepSeek(ChatDeepSeek):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if payload.get("tools"):
            messages = self._convert_input(input_).to_messages()
            if len(messages) != len(payload["messages"]):
                raise ValueError("DeepSeek message serialization mismatch")
            for original, wire in zip(messages, payload["messages"], strict=True):
                if isinstance(original, AIMessage):
                    reasoning = original.additional_kwargs.get("reasoning_content")
                    if isinstance(reasoning, str):
                        wire["reasoning_content"] = reasoning
        return payload
