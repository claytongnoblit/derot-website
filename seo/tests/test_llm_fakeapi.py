"""Exercise the real llm.LLM class (real SDK, real HTTP, real SSE parsing)
against the local fake API."""
import json

import pytest

from derot_seo.generate import PLAN_SCHEMA
from derot_seo.llm import LLM, LLMError
from derot_seo.util import load_config

from fake_anthropic import (FakeAnthropic, search_blocks, search_error_blocks, text_block,
                            thinking_block)

PLAN = {"primary_keyword": "box breathing", "secondary_keywords": [], "cluster": "c", "content_type": "how-to",
        "working_title": "t", "angle": "a", "search_intent": "i", "reader_problem": "p",
        "cannibalization_check": "none", "rationale": "r"}


@pytest.fixture()
def make(monkeypatch):
    servers = []

    def _make(responder):
        fake = FakeAnthropic(responder)
        servers.append(fake)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", fake.url)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        return fake, LLM(load_config())
    yield _make
    for s in servers:
        s.close()


def test_structured_happy_path_and_request_shape(make):
    fake, llm = make(lambda b, h: {"blocks": [thinking_block(), text_block(json.dumps(PLAN))]})
    out = llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA)
    assert out == PLAN
    body, headers = fake.requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["stream"] is True
    assert "thinking" not in body  # Opus 5.5: thinking is always on; never send disabled/budget
    assert llm.usage.input_tokens == 1000 and llm.usage.output_tokens == 500
    assert llm.usage.cost_usd > 0


def test_fallback_param_rejected_then_retried_without(make):
    def responder(body, headers):
        if "fallbacks" in body:
            return {"status": 400, "message": "fallbacks: not available for this organization"}
        return {"blocks": [text_block(json.dumps(PLAN))]}
    fake, llm = make(responder)
    assert llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA) == PLAN
    assert "fallbacks" not in fake.requests[-1][0]
    # remembered for later calls
    llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA)
    assert len(fake.requests) == 3


def test_structured_outputs_rejected_falls_back_to_prompted_json(make):
    def responder(body, headers):
        if "format" in body.get("output_config", {}):
            return {"status": 400, "message": "output_config.format.schema: unsupported keyword"}
        return {"blocks": [text_block("Here you go:\n```json\n" + json.dumps(PLAN) + "\n```")]}
    fake, llm = make(responder)
    assert llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA) == PLAN
    last = fake.requests[-1][0]
    assert "JSON Schema" in last["messages"][0]["content"]


def test_unrelated_400_is_raised(make):
    fake, llm = make(lambda b, h: {"status": 400, "message": "messages: roles must alternate"})
    import anthropic
    with pytest.raises(anthropic.BadRequestError):
        llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA)


def test_missing_required_fields_raise(make):
    fake, llm = make(lambda b, h: {"blocks": [text_block(json.dumps({"primary_keyword": "x"}))]})
    with pytest.raises(LLMError, match="missing required"):
        llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA)


def test_refusal_raises(make):
    fake, llm = make(lambda b, h: {"blocks": [], "stop_reason": "refusal"})
    with pytest.raises(LLMError, match="declined"):
        llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA)


def test_max_tokens_raises(make):
    fake, llm = make(lambda b, h: {"blocks": [text_block('{"primary_keyword": "x"')], "stop_reason": "max_tokens"})
    with pytest.raises(LLMError, match="max_tokens"):
        llm.structured("plan", "planner", "SYS", "pick", PLAN_SCHEMA)


def test_research_collects_urls_and_resumes_pause_turn(make):
    calls = {"n": 0}

    def responder(body, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"blocks": [thinking_block(), text_block("Searching. "),
                               *search_blocks("box breathing study", [("https://pubmed.ncbi.nlm.nih.gov/1/", "Study A")]),
                               *search_error_blocks()],
                    "stop_reason": "pause_turn", "searches": 1}
        return {"blocks": [*search_blocks("more", [("https://www.frontiersin.org/x", "Review B")], "srvtoolu_2"),
                           text_block("SOURCES\nhttps://pubmed.ncbi.nlm.nih.gov/1/ | Study A")],
                "stop_reason": "end_turn", "searches": 1}
    fake, llm = make(responder)
    text, seen = llm.research("research", "research", "SYS", "find sources", max_searches=4)
    assert "Searching." in text and "SOURCES" in text
    assert {s["url"] for s in seen} == {"https://pubmed.ncbi.nlm.nih.gov/1/", "https://www.frontiersin.org/x"}
    assert llm.usage.searches == 2
    first, _ = fake.requests[0]
    assert first["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}]
    second, _ = fake.requests[1]
    # resume = original user turn + the paused assistant turn, no extra "continue" message
    assert [m["role"] for m in second["messages"]] == ["user", "assistant"]
    types = [b["type"] for b in second["messages"][1]["content"]]
    assert "server_tool_use" in types and "web_search_tool_result" in types


def test_research_stops_after_bounded_continuations(make):
    fake, llm = make(lambda b, h: {"blocks": [text_block("still going ")], "stop_reason": "pause_turn"})
    text, seen = llm.research("research", "research", "SYS", "loop", max_searches=2)
    assert len(fake.requests) == 8  # hard cap, never infinite
