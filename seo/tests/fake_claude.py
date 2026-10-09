#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests. Emits the same output shapes as
`claude -p --output-format json|stream-json` and answers prompts with the
offline mock handlers. Behavior switches via FAKE_CLAUDE_MODE:
  ok (default) | not_logged_in | usage_limit | no_search_results"""
import json, os, re, sys

sys.path.insert(0, os.environ["FAKE_CLAUDE_SEO_DIR"])
from derot_seo import mock as M  # noqa: E402

args = sys.argv[1:]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"args": args, "prompt_head": prompt[:40],
                            "api_key_present": "ANTHROPIC_API_KEY" in os.environ}) + "\n")


def opt(name):
    return args[args.index(name) + 1] if name in args else None


fmt = opt("--output-format") or "text"
usage = {"input_tokens": 1200, "output_tokens": 600, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def result(text=None, structured=None, is_error=False):
    d = {"type": "result", "subtype": "success", "is_error": is_error, "duration_ms": 5, "num_turns": 1,
         "result": text if text is not None else "", "session_id": "s", "total_cost_usd": 0.0123, "usage": usage}
    if structured is not None:
        d["structured_output"] = structured
    return d


if mode == "not_logged_in":
    print(json.dumps(result("Not logged in · Please run /login", is_error=True)))
    sys.exit(1)
if mode == "bad_token":
    d = result("Failed to authenticate. API Error: 401 Invalid bearer token", is_error=True)
    d["api_error_status"] = 401
    print(json.dumps(d))
    sys.exit(1)
if mode == "usage_limit":
    print(json.dumps(result("Claude usage limit reached. Your limit will reset at 5pm.", is_error=True)))
    sys.exit(1)

if fmt == "json":
    schema = json.loads(opt("--json-schema"))
    assert opt("--tools") == "", "structured calls must disable tools"
    if prompt.startswith("Choose the next article"):
        sl = json.loads(prompt.split("Shortlist (best first):\n", 1)[1].split("\n\nClusters:", 1)[0])
        out = M._plan(prompt, {"candidates": sl})
    elif prompt.startswith("Turn these research notes"):
        out = M._brief(prompt, {})
    elif prompt.startswith(("Write the article", "Revise this article")):
        pk = re.search(r"Primary keyword: (.*)", prompt).group(1).strip()
        out = M._write(prompt, {"plan": {"primary_keyword": pk}})
    elif prompt.startswith("You are the senior editor"):
        out = M._review(prompt, {})
    elif "triage" in prompt.lower() or prompt.startswith("You maintain the keyword plan"):
        out = M._triage(prompt, {"candidates": json.loads(prompt.rsplit("Candidates:\n", 1)[1])})
    elif "social" in M.default_handlers():
        # Social copy prompts: answer with the mock social package for a generic post.
        out = M.default_handlers()["social"](prompt=prompt, hint={"post": {
            "title": "A calm, practical guide", "dek": "A plain-spoken guide.",
            "key_takeaways": ["Longer exhales help you settle.", "One minute is enough."]}})
    else:
        print(json.dumps(result("fake_claude: unrecognized prompt", is_error=True)))
        sys.exit(1)
    missing = [k for k in schema.get("required", []) if k not in out]
    if missing:
        print(json.dumps(result(f"schema validation failed: {missing}", is_error=True)))
        sys.exit(1)
    print(json.dumps(result("done", structured=out)))
    sys.exit(0)

# stream-json research / fact-check
assert opt("--tools") == "WebSearch,WebFetch"
events = [{"type": "system", "subtype": "init", "tools": ["WebSearch", "WebFetch"], "model": opt("--model")}]
if mode != "no_search_results":
    links = [{"title": t, "url": u} for u, t, _, _ in M.MOCK_SOURCES]
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "WebSearch", "input": {"query": "q"}}]}})
    events.append({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1",
         "content": "Web search results for query: \"q\"\n\nLinks: " + json.dumps(links)}]}})
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_2", "name": "WebFetch",
         "input": {"url": M.MOCK_SOURCES[0][0], "prompt": "details"}}]}})
    events.append({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_2",
         "content": "Page text mentioning https://unrelated-link-inside-page.example.com/x"}]}})
text = "SERP ANALYSIS\n...\nSOURCES\n" + "\n".join(f"{u} | {t}" for u, t, _, _ in M.MOCK_SOURCES)
if prompt.startswith("You are a fact-checker"):
    text = "1. SUPPORTED"
events.append(result(text))
for e in events:
    print(json.dumps(e))
