"""Bounded projections of official callback data; never mutate model history.

Only public message content is projected. SDK configs, injected tool state,
exception internals, binary blocks and encrypted reasoning are not serialized.
"""

import hashlib
import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

FIELD_BYTES = 6144
SENSITIVE = re.compile(
    r"^(?:api[_-]?key|authorization|password|passwd|secret|credential|"
    r"access[_-]?token|refresh[_-]?token|cookie|set-cookie|private[_-]?key)$",
    re.I,
)


class ObservationContent:
    def __init__(self, secrets: tuple[str, ...] = ()):
        self.secrets = tuple(value for value in secrets if value)
        self.previous: list[str] = []
        self.previous_step: str | None = None

    def redact(self, text: str) -> str:
        # Remember recognizable user-provided secret values for later bare echoes
        # in model/tool text. This state belongs to one task observer only.
        for match in re.finditer(
            r"""(?i)["']?(?:api[_-]?key|password|passwd|secret|credential|authorization|access[_-]?token|refresh[_-]?token|cookie)["']?\s*[:=]\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s,;}]+))""",
            text,
        ):
            self.remember(
                next((value for value in match.groups() if value is not None), "")
            )
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
        text = re.sub(
            r"""(?i)(["']?(?:api[_-]?key|password|passwd|secret|authorization|access[_-]?token|refresh[_-]?token|cookie)["']?\s*[:=]\s*)(["'])(.*?)\2""",
            lambda match: match[1] + match[2] + "[REDACTED]" + match[2],
            text,
        )
        text = re.sub(
            r"(?i)(\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*)(?![\"'])([^\s,;}]+)",
            r"\1[REDACTED]",
            text,
        )
        text = re.sub(r"data:[^\s;]+;base64,[A-Za-z0-9+/=]+", "[binary omitted]", text)
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    def remember(self, value: Any):
        if (
            isinstance(value, str)
            and 4 <= len(value) <= 4096
            and value != "[REDACTED]"
            and len(self.secrets) < 128
            and value not in self.secrets
        ):
            self.secrets += (value,)

    def safe(self, value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "[depth limit]"
        if isinstance(value, str):
            return self.redact(value)
        if value is None or type(value) in (bool, int, float):
            return value
        if isinstance(value, dict):
            result = {}
            for key, item in list(value.items())[:100]:
                if not isinstance(key, str):
                    continue
                if SENSITIVE.fullmatch(key):
                    self.remember(item)
                result[self.redact(key[:128])] = (
                    "[REDACTED]"
                    if SENSITIVE.fullmatch(key)
                    else self.safe(item, depth + 1)
                )
            if len(value) > 100:
                result["…"] = "[item limit]"
            return result
        if isinstance(value, (list, tuple)):
            selected = (
                list(value[:50]) + ["[item limit]"] + list(value[-50:])
                if len(value) > 100
                else value
            )
            return [self.safe(item, depth + 1) for item in selected]
        return "[unsupported value omitted]"

    @staticmethod
    def text_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        return "\n".join(
            (
                item
                if isinstance(item, str)
                else (
                    item.get("text", "")
                    if isinstance(item, dict)
                    and item.get("type") in {"text", "output_text"}
                    and isinstance(item.get("text"), str)
                    else "[non-text block omitted]"
                )
            )
            for item in value
        )

    def block(self, value: Any, source: str, availability: str = "provided") -> dict:
        safe = self.safe(value)
        text = (
            safe
            if isinstance(safe, str)
            else json.dumps(safe, ensure_ascii=False, indent=2)
        )
        text = self.redact(text)
        encoded = text.encode("utf-8", errors="replace")
        truncated = len(encoded) > FIELD_BYTES
        if truncated:
            # Keep both instructions at the start and latest messages at the end.
            text = (
                encoded[:3000].decode("utf-8", errors="ignore")
                + "\n… [content truncated] …\n"
                + encoded[-3000:].decode("utf-8", errors="ignore")
            )
        return {
            "text": text,
            "source": source,
            "availability": availability,
            "truncated": truncated,
            "originalBytes": len(encoded),
        }

    @staticmethod
    def message(message: BaseMessage) -> dict:
        result = {
            "role": message.type,
            "content": ObservationContent.text_content(message.content),
        }
        if isinstance(message, AIMessage) and message.tool_calls:
            result["toolCalls"] = message.tool_calls
        if getattr(message, "tool_call_id", None):
            result["toolCallId"] = message.tool_call_id
        return result

    def inputs(self, messages: list[BaseMessage], step: str) -> dict:
        # Hash full projected messages before display truncation; truncation must
        # never turn a changed context into an apparent matching prefix.
        values = [self.message(message) for message in messages]
        fingerprints = [
            hashlib.sha256(
                json.dumps(
                    message.model_dump(
                        include={
                            "type",
                            "content",
                            "tool_calls",
                            "tool_call_id",
                            "additional_kwargs",
                        }
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            for message in messages
        ]
        prefix = (
            bool(self.previous_step)
            and fingerprints[: len(self.previous)] == self.previous
        )
        mode = (
            "initial"
            if self.previous_step is None
            else "append" if prefix else "context_changed"
        )
        result = {
            "inputMode": mode,
            "input": self.block(
                values[len(self.previous) :] if prefix else values, "model_messages"
            ),
        }
        if self.previous_step:
            result["previousStepId"] = self.previous_step
        self.previous, self.previous_step = fingerprints, step
        return result

    def model_output(self, response: Any, partial: bool = False) -> dict:
        try:
            message = response.generations[0][0].message
        except (AttributeError, IndexError, TypeError):
            return {
                key: self.block("", source, "not_returned")
                for key, source in (
                    ("thinking", "provider_reasoning"),
                    ("output", "model_message"),
                    ("toolCalls", "model_tool_calls"),
                )
            }
        reasoning = message.additional_kwargs.get("reasoning_content")
        if not isinstance(reasoning, str):
            reasoning = (
                "\n".join(
                    block.get("reasoning", block.get("thinking", ""))
                    for block in message.content
                    if isinstance(block, dict)
                    and block.get("type") in {"reasoning", "thinking"}
                    and isinstance(
                        block.get("reasoning", block.get("thinking", "")), str
                    )
                )
                if isinstance(message.content, list)
                else ""
            )
        present = "partial" if partial else "provided"
        calls = getattr(message, "tool_calls", [])
        self.safe(
            calls
        )  # Discover labeled secrets before projecting any response field.
        return {
            "thinking": self.block(
                reasoning,
                "provider_reasoning",
                present if reasoning else "not_returned",
            ),
            "output": self.block(
                self.text_content(message.content), "model_message", present
            ),
            "toolCalls": self.block(
                calls, "model_tool_calls", present if calls else "not_returned"
            ),
        }
