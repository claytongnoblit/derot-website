"""Thin Claude API wrapper used by every pipeline stage.

Two call shapes:
  * structured(): one request whose reply is JSON validated against a schema
    (structured outputs), streamed so long articles never hit HTTP timeouts.
  * research(): an agentic request with the server-side web search tool. It
    resumes `pause_turn` and returns the final prose plus every URL the search
    tool actually returned, so later stages can reject invented sources.

A MockLLM with the same interface powers the offline test suite.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"

# USD per million tokens / per search, for the run cost log (Opus 5.5 list prices).
PRICES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
}
SEARCH_PRICE = 10.0 / 1000


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    searches: int = 0
    cost_usd: float = 0.0
    calls: list = field(default_factory=list)

    def add(self, stage: str, model: str, u: Any) -> None:
        if u is None:
            return
        it = getattr(u, "input_tokens", 0) or 0
        ot = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        se = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        p = PRICES.get(model, PRICES["claude-opus-5-5"])
        cost = (it * p["in"] + ot * p["out"] + cr * p["cache_read"] + cw * p["cache_write"]) / 1e6
        cost += se * SEARCH_PRICE
        self.input_tokens += it
        self.output_tokens += ot
        self.cache_read += cr
        self.cache_write += cw
        self.searches += se
        self.cost_usd += cost
        self.calls.append({"stage": stage, "in": it, "out": ot, "searches": se, "usd": round(cost, 4)})


def _text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text")


class LLM:
    def __init__(self, config: dict):
        import anthropic  # imported lazily so the mock path needs no key

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=4, timeout=900.0)
        self.models = config["models"]
        self.usage = Usage()
        self._fallbacks_ok = True
        self._structured_ok = True

    # -- low level ---------------------------------------------------------
    def _call(self, stage_cfg: dict, stage: str, **kwargs) -> Any:
        model = stage_cfg["model"]
        kwargs.setdefault("max_tokens", 64000)
        output_config = dict(kwargs.pop("output_config", {}) or {})
        output_config["effort"] = stage_cfg.get("effort", "high")
        attempt = 0
        while True:
            attempt += 1
            params = dict(model=model, output_config=output_config, **kwargs)
            if self._fallbacks_ok:
                params["betas"] = [FALLBACK_BETA]
                params["fallbacks"] = "default"
            try:
                with self.client.beta.messages.stream(**params) as stream:
                    msg = stream.get_final_message()
            except self._anthropic.BadRequestError as e:
                if self._fallbacks_ok and "fallback" in str(e).lower():
                    # Account or SDK without the fallback beta: run without it.
                    self._fallbacks_ok = False
                    continue
                raise
            except (self._anthropic.APIConnectionError, self._anthropic.APIStatusError) as e:
                status = getattr(e, "status_code", None)
                body = str(getattr(e, "body", "") or e).lower()
                transient = (isinstance(e, self._anthropic.APIConnectionError)
                             or status is None or status == 200 or status == 429 or status >= 500
                             or "overloaded" in body)
                # Mid-stream errors arrive with status 200 and are never retried by the SDK;
                # 429/5xx/529 were already retried by the SDK. Give transient failures a few
                # slow retries; anything else (auth, billing, permissions) fails immediately.
                if not transient or attempt >= 4:
                    if transient:
                        raise LLMError(f"{stage}: API unavailable after retries: {e}") from e
                    raise
                time.sleep(45 * attempt)
                continue
            self.usage.add(stage, model, getattr(msg, "usage", None))
            if msg.stop_reason == "refusal":
                raise LLMError(f"{stage}: model declined the request ({getattr(msg, 'stop_details', None)})")
            return msg

    # -- public ------------------------------------------------------------
    def structured(self, stage: str, role: str, system: str, prompt: str, schema: dict,
                   max_tokens: int = 64000, mock_hint: Optional[dict] = None) -> dict:
        cfg = self.models[role]
        if self._structured_ok:
            try:
                msg = self._call(
                    cfg, stage,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    output_config={"format": {"type": "json_schema", "schema": schema}},
                )
            except self._anthropic.BadRequestError as e:
                text = str(e).lower()
                if not any(t in text for t in ("schema", "output_config", "format")):
                    raise
                # Structured outputs rejected (schema feature or account): fall back to
                # instruction-following JSON, validated below.
                self._structured_ok = False
                return self.structured(stage, role, system, prompt, schema, max_tokens, mock_hint)
        else:
            msg = self._call(
                cfg, stage, system=system, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt + "\n\nReply with ONLY a JSON object (no prose, no code "
                           "fences) that validates against this JSON Schema:\n" + json.dumps(schema)}],
            )
        if msg.stop_reason == "max_tokens":
            raise LLMError(f"{stage}: response hit max_tokens")
        text = _text_of(msg).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            try:
                data = json.loads(text[start:end + 1]) if start >= 0 else None
            except json.JSONDecodeError:
                data = None
            if data is None:
                raise LLMError(f"{stage}: invalid JSON from model: {text[:300]}")
        missing = [k for k in schema.get("required", []) if k not in data]
        if missing:
            raise LLMError(f"{stage}: JSON missing required fields {missing}")
        return data

    def research(self, stage: str, role: str, system: str, prompt: str,
                 max_searches: int = 8, mock_hint: Optional[dict] = None) -> tuple[str, list[dict]]:
        cfg = self.models[role]
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}]
        messages: list = [{"role": "user", "content": prompt}]
        seen: dict[str, dict] = {}
        final_text = ""
        for _ in range(8):
            msg = self._call(cfg, stage, system=system, messages=messages, tools=tools,
                             max_tokens=32000)
            for block in msg.content:
                btype = getattr(block, "type", "")
                if btype == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for r in content:
                            url = getattr(r, "url", None)
                            if url:
                                seen[url] = {"url": url, "title": getattr(r, "title", "") or "",
                                             "page_age": getattr(r, "page_age", None)}
                elif btype == "text":
                    for c in getattr(block, "citations", None) or []:
                        url = getattr(c, "url", None)
                        if url and url not in seen:
                            seen[url] = {"url": url, "title": getattr(c, "title", "") or ""}
            final_text += _text_of(msg)
            if msg.stop_reason == "pause_turn":
                # Resume: resend everything so far; consecutive assistant turns are merged by the API.
                messages = messages + [{"role": "assistant", "content": msg.content}]
                continue
            break
        return final_text.strip(), list(seen.values())


class MockLLM:
    """Deterministic stand-in for tests. Each stage is answered by a handler
    registered in tests/mock_handlers.py (or any callable mapping)."""

    def __init__(self, handlers: dict[str, Callable[..., Any]]):
        self.handlers = handlers
        self.usage = Usage()
        self.calls: list[str] = []

    def structured(self, stage, role, system, prompt, schema, max_tokens=64000, mock_hint=None):
        self.calls.append(stage)
        return self.handlers[stage](prompt=prompt, hint=mock_hint or {})

    def research(self, stage, role, system, prompt, max_searches=8, mock_hint=None):
        self.calls.append(stage)
        return self.handlers[stage](prompt=prompt, hint=mock_hint or {})


class ClaudeCLI:
    """Backend that runs headless Claude Code (`claude -p`) on a Claude Pro/Max
    subscription instead of an API key. Auth comes from CLAUDE_CODE_OAUTH_TOKEN
    (create it once with `claude setup-token`) or a local `claude` login.

    Same interface as LLM: structured() uses --json-schema with all tools off;
    research() allows only WebSearch/WebFetch and reads the stream-json event log
    to collect every URL the search tool actually returned."""

    EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "max"}

    def __init__(self, config: dict):
        import tempfile

        self.models = config["models"]
        self.usage = Usage()
        self.bin = os.environ.get("CLAUDE_BIN", "claude")
        self.timeout = int(os.environ.get("DEROT_SEO_CLI_TIMEOUT", "2400"))
        # Run from an empty directory so no project CLAUDE.md, hooks, or MCP config load.
        self.cwd = tempfile.mkdtemp(prefix="derot-seo-claude-")
        env = dict(os.environ)
        # In -p mode an API key silently wins over the subscription token; drop it.
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(k, None)
        self.env = env

    def _base_args(self, cfg: dict, system: str, stream: bool) -> list[str]:
        args = [self.bin, "-p", "--model", cfg["model"], "--system-prompt", system,
                "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
                "--output-format", "stream-json" if stream else "json"]
        if stream:
            args.append("--verbose")
        effort = self.EFFORTS.get(cfg.get("effort", ""))
        if effort:
            args += ["--effort", effort]
        return args

    def _exec(self, stage: str, args: list[str], prompt: str) -> str:
        import subprocess

        for attempt in (1, 2, 3):
            try:
                proc = subprocess.run(args, input=prompt, capture_output=True, text=True, cwd=self.cwd,
                                      env=self.env, timeout=self.timeout)
            except FileNotFoundError as e:
                raise LLMError("Claude Code CLI not found. Install it: npm install -g @anthropic-ai/claude-code") from e
            except subprocess.TimeoutExpired as e:
                if attempt == 3:
                    raise LLMError(f"{stage}: claude CLI timed out after {self.timeout}s") from e
                continue
            out = proc.stdout
            final = self._final_event(out)
            if proc.returncode == 0 and final and not final.get("is_error"):
                return out
            msg = ((final or {}).get("result") or proc.stderr or out or "")[:500]
            low = msg.lower()
            status = (final or {}).get("api_error_status")
            if (status in (401, 403) or "not logged in" in low or "/login" in low or "authenticat" in low
                    or "oauth" in low or ("token" in low and ("invalid" in low or "expired" in low or "revoked" in low))):
                raise LLMError(f"{stage}: Claude Code is not authenticated ({msg}). Set the CLAUDE_CODE_OAUTH_TOKEN "
                               "secret (run `claude setup-token` on your Mac to create one).")
            if "limit" in low and ("usage" in low or "session" in low or "weekly" in low or "reset" in low):
                raise LLMError(f"{stage}: subscription usage limit reached ({msg}). The next scheduled run will retry.")
            if attempt == 3:
                raise LLMError(f"{stage}: claude CLI failed (exit {proc.returncode}): {msg}")
            time.sleep(30 * attempt)
        raise LLMError(f"{stage}: unreachable")

    @staticmethod
    def _final_event(out: str) -> Optional[dict]:
        out = out.strip()
        if not out:
            return None
        try:
            d = json.loads(out)
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            pass
        for line in reversed(out.splitlines()):  # stream-json: last "result" event
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("type") == "result":
                return d
        return None

    def _account(self, stage: str, model: str, final: dict) -> None:
        u = final.get("usage") or {}
        it, ot = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
        cr, cw = u.get("cache_read_input_tokens", 0) or 0, u.get("cache_creation_input_tokens", 0) or 0
        st = u.get("server_tool_use") or {}
        se = st.get("web_search_requests", 0) or 0
        # On a subscription this is Claude Code's list-price ESTIMATE, not a bill.
        cost = float(final.get("total_cost_usd") or 0)
        self.usage.input_tokens += it
        self.usage.output_tokens += ot
        self.usage.cache_read += cr
        self.usage.cache_write += cw
        self.usage.searches += se
        self.usage.cost_usd += cost
        self.usage.calls.append({"stage": stage, "in": it, "out": ot, "searches": se, "usd_estimate": round(cost, 4)})

    def structured(self, stage, role, system, prompt, schema, max_tokens=64000, mock_hint=None):
        cfg = self.models[role]
        args = self._base_args(cfg, system, stream=False) + ["--tools", "", "--json-schema", json.dumps(schema)]
        out = self._exec(stage, args, prompt)
        final = self._final_event(out) or {}
        self._account(stage, cfg["model"], final)
        data = final.get("structured_output")
        if not isinstance(data, dict):
            text = final.get("result") or ""
            start, end = text.find("{"), text.rfind("}")
            try:
                data = json.loads(text[start:end + 1]) if start >= 0 else None
            except json.JSONDecodeError:
                data = None
        if not isinstance(data, dict):
            raise LLMError(f"{stage}: no structured output from claude CLI: {str(final.get('result'))[:300]}")
        missing = [k for k in schema.get("required", []) if k not in data]
        if missing:
            raise LLMError(f"{stage}: JSON missing required fields {missing}")
        return data

    def research(self, stage, role, system, prompt, max_searches=8, mock_hint=None):
        cfg = self.models[role]
        tools = "WebSearch,WebFetch"
        args = self._base_args(cfg, system, stream=True) + ["--tools", tools, "--allowedTools", tools,
                                                            "--permission-mode", "dontAsk"]
        prompt = prompt + f"\n\nUse the WebSearch tool (at most {max_searches} searches) and WebFetch when you need a page's details."
        out = self._exec(stage, args, prompt)
        tool_names: dict[str, str] = {}
        fetched: list[str] = []
        seen: dict[str, dict] = {}
        final: dict = {}
        url_re = re.compile(r"https?://[^\s\"'<>)\]}]+")
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "result":
                final = ev
            msg = ev.get("message") or {}
            blocks = msg.get("content") if isinstance(msg, dict) else None
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_names[block.get("id", "")] = block.get("name", "")
                    if block.get("name") == "WebFetch":
                        u = (block.get("input") or {}).get("url")
                        if u:
                            fetched.append(u)
                elif block.get("type") == "tool_result" and tool_names.get(block.get("tool_use_id", "")) == "WebSearch":
                    content = block.get("content")
                    text = content if isinstance(content, str) else json.dumps(content)
                    for u in url_re.findall(text):
                        u = u.rstrip(".,;")
                        seen.setdefault(u, {"url": u, "title": ""})
        for u in fetched:
            seen.setdefault(u, {"url": u, "title": ""})
        self._account(stage, cfg["model"], final)
        if not self.usage.calls[-1]["searches"]:  # usage block may not report tool searches
            n = sum(1 for name in tool_names.values() if name == "WebSearch")
            self.usage.searches += n
            self.usage.calls[-1]["searches"] = n
        if not seen:
            raise LLMError(f"{stage}: web search returned no results through Claude Code (is WebSearch available "
                           "on this account?). Nothing was published.")
        return (final.get("result") or "").strip(), list(seen.values())


def make_llm(config: dict, mock: bool = False):
    if mock or os.environ.get("DEROT_SEO_MOCK") == "1":
        from .mock import default_handlers
        return MockLLM(default_handlers())
    backend = os.environ.get("DEROT_SEO_BACKEND") or (config.get("llm") or {}).get("backend", "api")
    if backend == "subscription":
        return ClaudeCLI(config)
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise LLMError("ANTHROPIC_API_KEY is not set (or set llm.backend: subscription in config.yaml)")
    return LLM(config)
