"""The subscription backend (headless Claude Code) tested against a fake
`claude` executable that emits the CLI's real JSON / stream-json shapes."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from derot_seo.generate import PLAN_SCHEMA
from derot_seo.llm import ClaudeCLI, LLMError, make_llm
from derot_seo.util import load_config

from test_pipeline import site  # noqa: F401  (fixture)

SEO = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).resolve().parent / "fake_claude.py"


@pytest.fixture()
def cli_env(monkeypatch, tmp_path):
    wrapper = tmp_path / "claude"
    wrapper.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n")
    wrapper.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("CLAUDE_BIN", str(wrapper))
    monkeypatch.setenv("FAKE_CLAUDE_SEO_DIR", str(SEO))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-stripped")
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    return {"bin": wrapper, "log": log}


def calls(env):
    return [json.loads(l) for l in env["log"].read_text().splitlines()]


def test_backend_selection(cli_env, monkeypatch):
    cfg = load_config()
    monkeypatch.delenv("DEROT_SEO_BACKEND", raising=False)
    assert isinstance(make_llm({**cfg, "llm": {"backend": "subscription"}}), ClaudeCLI)
    assert not isinstance(make_llm({**cfg, "llm": {"backend": "api"}}), ClaudeCLI)
    monkeypatch.setenv("DEROT_SEO_BACKEND", "subscription")  # env override wins
    assert isinstance(make_llm({**cfg, "llm": {"backend": "api"}}), ClaudeCLI)


def test_structured_call_shape(cli_env):
    llm = ClaudeCLI(load_config())
    prompt = ("Choose the next article for derot.org.\nShortlist (best first):\n"
              + json.dumps([{"keyword": "box breathing", "cluster": "c", "content_type": "how-to"}])
              + "\n\nClusters: {}")
    out = llm.structured("plan", "planner", "SYSTEM PROMPT", prompt, PLAN_SCHEMA)
    assert out["primary_keyword"] == "box breathing"
    c = calls(cli_env)[0]
    a = c["args"]
    assert a[0] == "-p" and a[a.index("--model") + 1] == "claude-opus-5-5"
    assert a[a.index("--tools") + 1] == "" and "--json-schema" in a
    assert a[a.index("--effort") + 1] == "medium"
    assert a[a.index("--system-prompt") + 1] == "SYSTEM PROMPT"
    assert "--strict-mcp-config" in a and "--no-session-persistence" in a
    assert c["api_key_present"] is False  # API key must never shadow the subscription token
    assert llm.usage.input_tokens == 1200 and llm.usage.cost_usd > 0


def test_research_collects_only_search_urls(cli_env):
    llm = ClaudeCLI(load_config())
    text, seen = llm.research("research", "research", "SYS", "Research an article", max_searches=5)
    urls = {s["url"] for s in seen}
    assert "https://pubmed.ncbi.nlm.nih.gov/36630953/" in urls and len(urls) == 4
    assert not any("unrelated-link-inside-page" in u for u in urls)  # links inside fetched pages don't count
    assert "SOURCES" in text
    a = calls(cli_env)[0]["args"]
    assert a[a.index("--output-format") + 1] == "stream-json" and "--verbose" in a
    assert a[a.index("--permission-mode") + 1] == "dontAsk"
    assert a[a.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert llm.usage.searches == 1


@pytest.mark.parametrize("mode", ["not_logged_in", "bad_token"])
def test_auth_failure_is_clear(cli_env, monkeypatch, mode):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(LLMError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        ClaudeCLI(load_config()).structured("plan", "planner", "S", "Choose", PLAN_SCHEMA)
    assert len(calls(cli_env)) == 1  # no pointless retries


def test_usage_limit_is_clear(cli_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "usage_limit")
    with pytest.raises(LLMError, match="usage limit"):
        ClaudeCLI(load_config()).structured("plan", "planner", "S", "Choose", PLAN_SCHEMA)
    assert len(calls(cli_env)) == 1


def test_no_search_results_stops_run(cli_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "no_search_results")
    with pytest.raises(LLMError, match="no results"):
        ClaudeCLI(load_config()).research("research", "research", "S", "Research", 5)


def test_missing_cli_is_clear(cli_env, monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", "/nonexistent/claude")
    with pytest.raises(LLMError, match="npm install"):
        ClaudeCLI(load_config()).structured("plan", "planner", "S", "Choose", PLAN_SCHEMA)


def _run(site, cli_env, *args, today="2026-10-12", mode=None):  # noqa: F811
    env = {**os.environ, "DEROT_SEO_TODAY": today, "PYTHONWARNINGS": "ignore", "DEROT_SEO_BACKEND": "subscription"}
    for k in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "DEROT_SEO_MOCK"):
        env.pop(k, None)
    env["FAKE_CLAUDE_SEO_DIR"] = str(site / "seo")
    if mode:
        env["FAKE_CLAUDE_MODE"] = mode
    return subprocess.run([sys.executable, "-m", "derot_seo", *args], cwd=site / "seo", env=env,
                          capture_output=True, text=True, timeout=300)


def test_full_publish_on_subscription_backend(site, cli_env):  # noqa: F811
    r = _run(site, cli_env, "publish", "--no-link-check")
    assert r.returncode == 0, r.stdout + r.stderr
    post = json.loads(next((site / "seo/content/posts").glob("*.json")).read_text())
    assert (site / "blog" / post["slug"] / "index.html").exists()
    stages = [c["prompt_head"][:20] for c in calls(cli_env)]
    assert any(s.startswith("Choose the next") for s in stages)
    assert any(s.startswith("Research an articl") for s in stages)
    assert any(s.startswith("You are a fact-che") for s in stages)
    assert not any(c["api_key_present"] for c in calls(cli_env))


def test_full_publish_usage_limit_fails_cleanly(site, cli_env):  # noqa: F811
    r = _run(site, cli_env, "publish", "--no-link-check", mode="usage_limit")
    assert r.returncode not in (0, 10, 20)
    assert "usage limit" in (r.stdout + r.stderr)
    assert not list((site / "seo/content/posts").glob("*.json"))
