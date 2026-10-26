"""What the chat log shows: each request's newest message and the model's reply, as events.

The chat log proxy (``runtime.chat_proxy``) passes every byte through unchanged and
hands copies to this module, which never raises into the forwarding path. Requests
are the OpenAI-style chat and completion APIs and the Anthropic-style messages API
(what Claude Code sends); both arrive streamed (server-sent events) or as one JSON
body. Events are small dicts, so they can be sent to the chat log window as JSON lines:

- ``{"type": "request", "id", "time", "path", "summary", "parts": [[label, text], ...]}``
- ``{"type": "text", "id", "kind": "thinking" | "reply" | "tool", "text", "label"?}``
- ``{"type": "end", "id", "status", "seconds", "tokens"?, "rate"?, "stop"?, "error"?}``
"""

from __future__ import annotations

import json
from typing import Any

# The paths whose requests and replies are shown; everything else only passes through.
CHAT_PATHS = {
    "/v1/chat/completions": "openai",
    "/chat/completions": "openai",
    "/v1/completions": "completion",
    "/completions": "completion",
    "/completion": "completion",
    "/v1/messages": "anthropic",
}
USER_LINES = 40  # lines of the user's own text shown per request
TOOL_LINES = 6  # lines of a tool result shown
MAX_BODY = 16 * 1024**2  # a reply body longer than this is not parsed


def api_of(method: str, path: str) -> str | None:
    """The chat API a request uses, or None for a request the chat log skips."""
    if method != "POST":
        return None
    return CHAT_PATHS.get(path.split("?", 1)[0].rstrip("/") or "/")


def clip(text: str, lines: int) -> str:
    """The first ``lines`` lines of ``text``, with a note of how many were left out."""
    rows = text.strip("\n").splitlines()
    if len(rows) <= lines:
        return "\n".join(rows)
    return "\n".join(rows[:lines] + [f"... ({len(rows) - lines} more lines)"])


def request_event(exchange: int, time: str, path: str, api: str, body: bytes) -> dict[str, Any]:
    """The request as the chat log shows it: a one-line summary and the newest message."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        return _event("request", exchange, time=time, path=path, summary="", parts=[])
    facts = []
    if api == "completion":
        prompt = data.get("prompt")
        parts = [["Prompt", clip(_text(prompt), USER_LINES)]] if prompt else []
    else:
        messages = [m for m in data.get("messages") or [] if isinstance(m, dict)]
        facts.append(_count(len(messages), "message"))
        if data.get("tools"):
            facts.append(_count(len(data["tools"]), "tool"))
        parts = _newest_turn(messages)
    if data.get("stream"):
        facts.append("streamed")
    if data.get("model"):
        facts.append(f"model {data['model']}")
    return _event("request", exchange, time=time, path=path, summary=", ".join(facts), parts=parts)


def _newest_turn(messages: list[dict[str, Any]]) -> list[list[str]]:
    """The messages after the model's last turn: what this request adds."""
    turn = []
    for message in reversed(messages):
        if message.get("role") == "assistant":
            break
        turn.append(message)
    parts = []
    for message in reversed(turn):
        role = message.get("role")
        content = message.get("content")
        if role == "tool":
            parts.append(["Tool result", clip(_text(content), TOOL_LINES)])
        elif role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    parts.append(["Tool result", clip(_text(block.get("content")), TOOL_LINES)])
                elif block.get("type") in ("text", "input_text"):
                    parts.append(["You", clip(str(block.get("text") or ""), USER_LINES)])
                elif block.get("type") in ("image", "image_url", "input_image"):
                    parts.append(["You", "[an image]"])
        elif role == "user":
            parts.append(["You", clip(_text(content), USER_LINES)])
    return [p for p in parts if p[1]]


def _text(content: Any) -> str:
    """Plain text from a string, or from a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, str):
                pieces.append(block)
            elif isinstance(block, dict):
                if block.get("type") in ("image", "image_url", "input_image"):
                    pieces.append("[an image]")
                else:
                    pieces.append(str(block.get("text") or ""))
        return "\n".join(p for p in pieces if p)
    return "" if content is None else str(content)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _event(event_type: str, exchange: int, **fields: Any) -> dict[str, Any]:
    return {"type": event_type, "id": exchange, **fields}


class Reply:
    """Turns a reply's bytes, as they arrive, into text events; ``finish`` gives the end event."""

    def __init__(self, exchange: int, api: str, content_type: str) -> None:
        self.exchange = exchange
        self.api = api
        self.streamed = "event-stream" in content_type
        self.buffer = b""
        self.size = 0
        self.tokens: int | None = None
        self.rate: float | None = None
        self.stop: str | None = None
        self.error: str | None = None
        self.tools: dict[int, str] = {}  # OpenAI tool call index -> name already shown

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self.size += len(data)
        if self.size > MAX_BODY:
            self.buffer = b""
            return []
        self.buffer += data
        if not self.streamed:
            return []  # one JSON body, parsed when it is complete
        events = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data:"):
                events += self._chunk(line[5:].strip())
        return events

    def finish(self, status: int, seconds: float) -> list[dict[str, Any]]:
        events = []
        if self.streamed and self.buffer.strip().startswith(b"data:"):
            events += self._chunk(self.buffer.strip()[5:].strip())
        elif not self.streamed and self.size <= MAX_BODY:
            events += self._body(self.buffer)
        end = _event("end", self.exchange, status=status, seconds=round(seconds, 1))
        for key in ("tokens", "rate", "stop", "error"):
            value = getattr(self, key)
            if value is not None:
                end[key] = value
        return [*events, end]

    # Streamed replies ---------------------------------------------------------------------

    def _chunk(self, raw: bytes) -> list[dict[str, Any]]:
        if raw == b"[DONE]":
            return []
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        self._facts(data)
        if self.api == "anthropic":
            return self._anthropic_chunk(data)
        events = []
        for choice in data.get("choices") or []:
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                self.stop = choice["finish_reason"]
            if "text" in choice and choice["text"]:  # the completion API
                events.append(self._text("reply", choice["text"]))
            events += self._openai_message(delta)
        return events

    def _anthropic_chunk(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        kind = data.get("type")
        if kind == "content_block_start":
            block = data.get("content_block") or {}
            if block.get("type") == "tool_use":
                return [self._text("tool", "", label=str(block.get("name") or "tool"))]
            if block.get("type") == "text" and block.get("text"):
                return [self._text("reply", block["text"])]
        if kind == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                return [self._text("reply", str(delta.get("text") or ""))]
            if delta.get("type") == "thinking_delta":
                return [self._text("thinking", str(delta.get("thinking") or ""))]
            if delta.get("type") == "input_json_delta":
                return [self._text("tool", str(delta.get("partial_json") or ""))]
        if kind == "message_delta":
            stop = (data.get("delta") or {}).get("stop_reason")
            if stop:
                self.stop = stop
        return []

    # Whole replies ------------------------------------------------------------------------

    def _body(self, raw: bytes) -> list[dict[str, Any]]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        self._facts(data)
        events = []
        if self.api == "anthropic":
            self.stop = data.get("stop_reason") or self.stop
            for block in data.get("content") or []:
                if block.get("type") == "thinking":
                    events.append(self._text("thinking", str(block.get("thinking") or "")))
                elif block.get("type") == "text":
                    events.append(self._text("reply", str(block.get("text") or "")))
                elif block.get("type") == "tool_use":
                    arguments = json.dumps(block.get("input") or {}, ensure_ascii=False)
                    events.append(
                        self._text("tool", arguments, label=str(block.get("name") or "tool"))
                    )
            return [e for e in events if e["text"] or e.get("label")]
        if self.api == "completion" and isinstance(data.get("content"), str):
            self.stop = data.get("stop_type") or self.stop
            return [self._text("reply", data["content"])]
        for choice in data.get("choices") or []:
            self.stop = choice.get("finish_reason") or self.stop
            if choice.get("text"):
                events.append(self._text("reply", choice["text"]))
            events += self._openai_message(choice.get("message") or {}, whole=True)
        return events

    def _openai_message(self, message: dict[str, Any], whole: bool = False) -> list[dict[str, Any]]:
        events = []
        if message.get("reasoning_content"):
            events.append(self._text("thinking", str(message["reasoning_content"])))
        if message.get("content"):
            events.append(self._text("reply", _text(message["content"])))
        for position, call in enumerate(message.get("tool_calls") or []):
            index = call.get("index", position)
            function = call.get("function") or {}
            arguments = str(function.get("arguments") or "")
            if whole or index not in self.tools:
                self.tools[index] = str(function.get("name") or "tool")
                events.append(self._text("tool", arguments, label=self.tools[index]))
            elif arguments:
                events.append(self._text("tool", arguments))
        return events

    # Both ---------------------------------------------------------------------------------

    def _facts(self, data: dict[str, Any]) -> None:
        """Token counts, speed and errors, wherever the API puts them."""
        usage = data.get("usage") or (data.get("message") or {}).get("usage") or {}
        tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if isinstance(tokens, int) and tokens > 0:
            self.tokens = tokens
        timings = data.get("timings") or {}
        if isinstance(timings.get("predicted_n"), int) and timings["predicted_n"] > 0:
            self.tokens = timings["predicted_n"]
        if isinstance(timings.get("predicted_per_second"), (int, float)):
            self.rate = round(float(timings["predicted_per_second"]), 1)
        error = data.get("error")
        if isinstance(error, dict):
            self.error = str(error.get("message") or error)
        elif isinstance(error, str):
            self.error = error

    def _text(self, kind: str, text: str, label: str | None = None) -> dict[str, Any]:
        event = _event("text", self.exchange, kind=kind, text=text)
        if label is not None:
            event["label"] = label
        return event


class ChatView:
    """Renders events as text for the chat log window, streaming replies as they arrive.

    Up to four requests can run at once, so text from another request starts with a
    line naming which one it belongs to.
    """

    LABELS = {"thinking": "Thinking", "reply": "Reply", "tool": "Tool call"}

    def __init__(self) -> None:
        self.exchange: int | None = None
        self.kind: str | None = None
        self.at_line_start = True

    def feed(self, event: dict[str, Any]) -> str:
        kind = event.get("type")
        exchange = event.get("id")
        if kind == "request":
            return self._request(event)
        if kind == "text":
            return self._stream(exchange, event)
        if kind == "end":
            return self._end(event)
        if kind == "notice":
            return self._line_break() + f"({event.get('text', '')})\n"
        return ""

    def _request(self, event: dict[str, Any]) -> str:
        out = [self._line_break(), "\n"]
        head = f"=== #{event['id']}  {event.get('time', '')}  {event.get('path', '')}"
        summary = f"  ({event['summary']})" if event.get("summary") else ""
        out.append(f"{head}{summary}\n")
        for label, text in event.get("parts") or []:
            out.append(f"{label}:\n{_indent(text)}\n")
        self.exchange, self.kind, self.at_line_start = event["id"], None, True
        return "".join(out)

    def _stream(self, exchange: Any, event: dict[str, Any]) -> str:
        text = str(event.get("text") or "")
        out = []
        if exchange != self.exchange:
            out.append(self._line_break() + f"--- #{exchange} continues\n")
            self.exchange, self.kind = exchange, None
        label = event.get("label")
        if event.get("kind") != self.kind or label:
            name = self.LABELS.get(str(event.get("kind")), "Reply")
            out.append(self._line_break() + (f"{name}: {label}\n" if label else f"{name}:\n"))
            self.kind = event.get("kind")
            self.at_line_start = True
        if not text:
            return "".join(out)
        if self.at_line_start:
            out.append("  ")
            text = text.lstrip("\n")
        body = text.replace("\n", "\n  ")
        if body.endswith("\n  "):
            body = body[:-2]
        out.append(body)
        self.at_line_start = text.endswith("\n")
        return "".join(out)

    def _end(self, event: dict[str, Any]) -> str:
        facts = [f"{event.get('seconds', 0)} s"]
        if event.get("tokens"):
            facts.append(f"{event['tokens']} tokens")
        if event.get("rate"):
            facts.append(f"{event['rate']} tokens/s")
        if event.get("stop"):
            facts.append(f"stop: {event['stop']}")
        status = event.get("status")
        if event.get("error") or (isinstance(status, int) and status >= 400):
            facts.append(f"error {status}: {event.get('error') or 'see the server window'}")
        out = self._line_break() + f"--- #{event['id']} done in {', '.join(facts)}\n"
        if self.exchange == event.get("id"):
            self.exchange, self.kind = None, None
        return out

    def _line_break(self) -> str:
        if self.at_line_start:
            return ""
        self.at_line_start = True
        return "\n"


def _indent(text: str) -> str:
    return "\n".join("  " + line if line else "" for line in text.splitlines())
