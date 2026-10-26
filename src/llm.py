"""Provider layer: the only module that talks to a language model (decision D9).

The agent speaks one neutral message format; each provider translates it.

    neutral messages:
      {"role": "user", "content": str}
      {"role": "assistant", "content": str, "tool_calls": [ToolCall...], "raw": provider-specific}
      {"role": "tool", "tool_call_id": str, "name": str, "content": str}

    neutral tool schema: {"name", "description", "parameters": JSON schema}

Providers:
    OllamaProvider     free, local (default)
    AnthropicProvider  optional paid backend (`uv sync --extra claude`)
    ReplayProvider     plays back a saved run's model responses; no model needed
"""

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    raw: dict | list | None = None  # provider's own assistant message, echoed back verbatim

    def to_dict(self) -> dict:
        d = asdict(self)
        d["raw"] = None  # not needed for replay; keeps saved runs provider-neutral
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LLMResponse":
        return cls(text=d["text"], tool_calls=[ToolCall(**t) for t in d["tool_calls"]],
                   input_tokens=d.get("input_tokens", 0), output_tokens=d.get("output_tokens", 0),
                   latency_s=d.get("latency_s", 0.0))


class LLMProvider(Protocol):
    name: str
    model: str

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


# --- Ollama (default, free) ------------------------------------------------------

class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None, think: bool = False, num_ctx: int = 16384):
        import ollama
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        self.client = ollama.Client(host=host or os.getenv("OLLAMA_HOST", "http://localhost:11434"))
        self.think = think
        # Ollama's default context window is small; tool results need room.
        self.options = {"num_ctx": num_ctx, "temperature": 0}

    def _to_ollama(self, system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant":
                out.append({"role": "assistant", "content": m.get("content", ""),
                            "tool_calls": [{"function": {"name": t.name, "arguments": t.arguments}} for t in m.get("tool_calls", [])]})
            elif m["role"] == "tool":
                out.append({"role": "tool", "content": m["content"], "tool_name": m["name"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def chat(self, system, messages, tools) -> LLMResponse:
        t0 = time.perf_counter()
        resp = self.client.chat(
            model=self.model, messages=self._to_ollama(system, messages),
            tools=[{"type": "function", "function": t} for t in tools] or None,
            think=self.think, options=self.options,
        )
        msg = resp.message
        calls = [ToolCall(name=c.function.name, arguments=dict(c.function.arguments or {})) for c in (msg.tool_calls or [])]
        return LLMResponse(text=msg.content or "", tool_calls=calls,
                           input_tokens=resp.prompt_eval_count or 0, output_tokens=resp.eval_count or 0,
                           latency_s=time.perf_counter() - t0)

    def is_available(self) -> tuple[bool, str]:
        try:
            names = [m.model for m in self.client.list().models]
        except Exception as e:  # server not running
            return False, f"Ollama server not reachable ({e.__class__.__name__}). Start it with `ollama serve`."
        if not any(n == self.model or n.split(":")[0] == self.model for n in names):
            return False, f"Model {self.model} not downloaded. Run `ollama pull {self.model}`."
        return True, "ok"


# --- Anthropic (optional, paid) ---------------------------------------------------

class AnthropicProvider:
    """Claude via the official SDK. Uses server-side refusal fallbacks ("default"
    routing) so a declined request is retried on a fallback model automatically."""
    name = "anthropic"

    def __init__(self, model: str | None = None, effort: str = "medium"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL") or "claude-opus-5-5"
        self.effort = effort

    def _to_anthropic(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "assistant":
                # Echo the model's own content blocks unchanged (keeps thinking blocks valid).
                content = m.get("raw") or (
                    ([{"type": "text", "text": m["content"]}] if m.get("content") else [])
                    + [{"type": "tool_use", "id": t.id, "name": t.name, "input": t.arguments} for t in m.get("tool_calls", [])])
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                         "is_error": m.get("is_error", False)}
                # All results for one assistant turn go in a single user message.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def chat(self, system, messages, tools) -> LLMResponse:
        t0 = time.perf_counter()
        resp = self.client.beta.messages.create(
            model=self.model, max_tokens=16000, system=system,
            messages=self._to_anthropic(messages),
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return LLMResponse(text="[The model declined to answer this request.]", tool_calls=[],
                               latency_s=time.perf_counter() - t0)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text=text, tool_calls=calls, input_tokens=resp.usage.input_tokens,
                           output_tokens=resp.usage.output_tokens, latency_s=time.perf_counter() - t0,
                           raw=[b.to_dict() for b in resp.content])


# --- Replay (no model) --------------------------------------------------------------

class ReplayProvider:
    """Returns a saved run's model responses in order. The agent still executes
    every tool call live, so the evidence is recomputed from the database."""
    name = "replay"

    def __init__(self, responses: list[dict], model: str = "replay"):
        self._responses = [LLMResponse.from_dict(r) for r in responses]
        self.model = model
        self._i = 0

    def chat(self, system, messages, tools) -> LLMResponse:
        if self._i >= len(self._responses):
            return LLMResponse(text="[Replay ended: the saved run has no further responses.]", tool_calls=[])
        r = self._responses[self._i]
        self._i += 1
        return r


def get_provider(name: str | None = None, **kw) -> LLMProvider:
    name = name or os.getenv("LLM_PROVIDER", "ollama")
    if name == "ollama":
        return OllamaProvider(**kw)
    if name == "anthropic":
        return AnthropicProvider(**kw)
    raise ValueError(f"unknown LLM_PROVIDER {name!r}; expected 'ollama' or 'anthropic'")


def dumps(obj) -> str:
    """Compact, deterministic JSON for tool results sent to the model."""
    return json.dumps(obj, separators=(",", ":"), sort_keys=False, default=str)
