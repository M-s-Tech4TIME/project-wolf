"""Structured-output fallback for models without reliable native tool-calling.

When a model cannot reliably emit tool-call JSON natively, the adapter injects
tool schemas into the system prompt and parses the model's plain-text response
as JSON.  On failure the error is fed back for up to MAX_RETRIES attempts.
"""

import json
import re
import uuid
from collections.abc import Awaitable, Callable

from wolf_common.errors import WolfError
from wolf_schema import ChatRequest, ChatResponse, ToolCall, ToolSchema
from wolf_schema.chat import Message, MessageRole

_FALLBACK_INSTRUCTION = """\
You are in tool-call mode.  Respond ONLY with a single JSON object — \
no prose, no code fences, no additional keys.

To call a tool:
  {{"tool": "<tool_name>", "arguments": {{<arguments as a JSON object>}}}}

To give a final answer (no tool call):
  {{"answer": "<your answer>"}}

Available tools:
"""

MAX_RETRIES = 3


def _build_tool_list(tools: list[ToolSchema]) -> str:
    lines: list[str] = []
    for t in tools:
        lines.append(f"- {t.name}: {t.description}")
        lines.append(f"  Input schema: {json.dumps(t.input_schema)}")
    return "\n".join(lines)


def _inject_fallback_system(request: ChatRequest) -> ChatRequest:
    """Prepend the fallback instruction block to the conversation's system prompt."""
    tool_block = _build_tool_list(request.tools) if request.tools else "(none)"
    instruction = _FALLBACK_INSTRUCTION + tool_block

    messages = list(request.messages)
    if messages and messages[0].role == MessageRole.system:
        merged = f"{instruction}\n\n{messages[0].content}"
        messages[0] = messages[0].model_copy(update={"content": merged})
    else:
        messages.insert(0, Message(role=MessageRole.system, content=instruction))

    return request.model_copy(update={"messages": messages, "tools": None})


def _strip_fences(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_fallback_response(raw: str) -> tuple[str | None, ToolCall | None]:
    """Parse a fallback JSON response into (answer, tool_call).

    Exactly one of the two return values is non-None.
    Raises ValueError if the response cannot be parsed or is structurally wrong.
    """
    cleaned = _strip_fences(raw)
    try:
        data: object = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Response JSON must be an object")

    if "answer" in data:
        return str(data["answer"]), None

    if "tool" in data:
        if not isinstance(data.get("arguments"), dict):
            raise ValueError('"arguments" must be a JSON object')
        call = ToolCall(
            id=str(uuid.uuid4()),
            name=str(data["tool"]),
            arguments=data["arguments"],
        )
        return None, call

    raise ValueError('Response must contain "answer" or "tool" key')


ChatFn = Callable[[ChatRequest], Awaitable[ChatResponse]]


async def chat_with_fallback(
    raw_chat: ChatFn,
    request: ChatRequest,
) -> ChatResponse:
    """Drive a model through the structured-output fallback loop.

    Injects tool schemas into the system prompt, calls raw_chat, parses the
    response.  Feeds validation errors back for up to MAX_RETRIES attempts.
    Raises WolfError if all attempts are exhausted.
    """
    modified = _inject_fallback_system(request)
    last_error: str | None = None

    for attempt in range(MAX_RETRIES):
        if attempt > 0 and last_error:
            retry_message = Message(
                role=MessageRole.user,
                content=(
                    f"Your last response was not valid JSON. Error: {last_error}. "
                    "Respond only with a JSON object."
                ),
            )
            messages = list(modified.messages) + [retry_message]
            modified = modified.model_copy(update={"messages": messages})

        response = await raw_chat(modified)

        try:
            answer, tool_call = parse_fallback_response(response.content)
        except ValueError as exc:
            last_error = str(exc)
            continue

        if tool_call:
            return response.model_copy(
                update={"tool_calls": [tool_call], "content": ""}
            )
        return response.model_copy(
            update={"content": answer or "", "tool_calls": []}
        )

    raise WolfError(
        f"Model failed structured-output fallback after {MAX_RETRIES} attempts: {last_error}"
    )
