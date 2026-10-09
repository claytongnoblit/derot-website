"""Full `publish` runs through the REAL client code (no --mock) against the
fake API, including a writer that first breaks the rules and must be revised."""
import json
import re
import sys
import subprocess
import os

import pytest

from derot_seo import mock as M

from fake_anthropic import FakeAnthropic, search_blocks, text_block, thinking_block
from test_pipeline import site  # noqa: F401  (fixture)


def _user_text(body):
    c = body["messages"][0]["content"]
    return c if isinstance(c, str) else " ".join(b.get("text", "") for b in c)


def make_responder(state):
    def responder(body, headers):
        p = _user_text(body)
        state.setdefault("stages", []).append(p[:30])
        if p.startswith("Choose the next article"):
            sl = json.loads(p.split("Shortlist (best first):\n", 1)[1].split("\n\nClusters:", 1)[0])
            return {"blocks": [thinking_block(), text_block(json.dumps(M._plan(p, {"candidates": sl})))]}
        if p.startswith("Research an article"):
            return {"blocks": [*search_blocks("q", [(u, t) for u, t, _, _ in M.MOCK_SOURCES]),
                               text_block("SERP ANALYSIS ... SOURCES ...")], "searches": 1}
        if p.startswith("Turn these research notes"):
            return {"blocks": [text_block(json.dumps(M._brief(p, {})))]}
        if p.startswith("Write the article") or p.startswith("Revise this article"):
            pk = re.search(r"Primary keyword: (.*)", p).group(1).strip()
            art = M._write(p, {"plan": {"primary_keyword": pk}})
            if p.startswith("Write the article") and state.get("bad_first_draft"):
                art["title"] = art["title"] + " — the truth"
                art["body_markdown"] += "\n\nAs [one study](https://not-in-sources.example.org/x) shows, DeRot locks your apps."
            if p.startswith("Revise this article"):
                state["revised_with"] = p.split("CURRENT ARTICLE", 1)[0]
            return {"blocks": [text_block(json.dumps(art))]}
        if p.startswith("You are a fact-checker"):
            return {"blocks": [text_block("1. SUPPORTED: ...")], "searches": 1}
        if p.startswith("You are the senior editor"):
            ok = not state.get("always_reject")
            return {"blocks": [text_block(json.dumps({
                "scores": {k: 9 if ok else 5 for k in ["accuracy", "helpfulness", "intent_match", "geo_structure", "voice", "safety"]},
                "overall": 9 if ok else 5, "blocking_issues": [] if ok else [{"issue": "thin evidence in section 2", "find": "", "replace": ""}],
                "improvements": [], "verdict": "publish" if ok else "revise"}))]}
        return {"status": 400, "message": "unexpected prompt: " + p[:60]}
    return responder


def run_real(site, fake, *args, today="2026-10-12"):
    env = {**os.environ, "DEROT_SEO_TODAY": today, "PYTHONWARNINGS": "ignore",
           "ANTHROPIC_BASE_URL": fake.url, "ANTHROPIC_API_KEY": "test-key", "DEROT_SEO_BACKEND": "api"}
    for k in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "DEROT_SEO_MOCK"):
        env.pop(k, None)
    return subprocess.run([sys.executable, "-m", "derot_seo", *args], cwd=site / "seo", env=env,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture()
def fake_api():
    state = {}
    fake = FakeAnthropic(make_responder(state))
    yield fake, state
    fake.close()


def test_real_client_publish(site, fake_api):  # noqa: F811
    fake, state = fake_api
    r = run_real(site, fake, "publish", "--no-link-check")
    assert r.returncode == 0, r.stdout + r.stderr
    post = json.loads(next((site / "seo/content/posts").glob("*.json")).read_text())
    assert post["review_score"] == 9
    assert "invented.example.com" not in json.dumps(post)
    runs = (site / "seo/data/runs.jsonl").read_text().strip().splitlines()
    rec = json.loads(runs[-1])
    assert rec["result"] == "published" and rec["cost_usd"] > 0 and rec["searches"] >= 2
    assert (site / "blog" / post["slug"] / "index.html").exists()


def test_real_client_revises_bad_draft(site, fake_api):  # noqa: F811
    fake, state = fake_api
    state["bad_first_draft"] = True
    r = run_real(site, fake, "publish", "--no-link-check")
    assert r.returncode == 0, r.stdout + r.stderr
    issues = state["revised_with"]
    assert "not from research sources" in issues and "lock" in issues
    post = json.loads(next((site / "seo/content/posts").glob("*.json")).read_text())
    assert "not-in-sources" not in post["body_markdown"] and "—" not in post["title"]
    rec = json.loads((site / "seo/data/runs.jsonl").read_text().strip().splitlines()[-1])
    assert rec["stages"][-2:] == ["revise1", "approved@1"]


def test_real_client_parks_after_max_revisions(site, fake_api):  # noqa: F811
    fake, state = fake_api
    state["always_reject"] = True
    r = run_real(site, fake, "publish", "--no-link-check")
    assert r.returncode == 20, r.stdout + r.stderr
    assert not list((site / "seo/content/posts").glob("*.json"))
    assert len(list((site / "seo/content/drafts").glob("*.json"))) == 1
    reviews = sum(1 for s in state["stages"] if s.startswith("You are the senior editor"))
    assert reviews == 3  # initial + 2 revisions (config max_revisions: 2)


def test_api_outage_fails_cleanly(site):  # noqa: F811
    fake = FakeAnthropic(lambda b, h: {"status": 400, "message": "credit balance is too low"})
    try:
        r = run_real(site, fake, "publish", "--no-link-check")
    finally:
        fake.close()
    assert r.returncode not in (0, 10, 20)
    assert not list((site / "seo/content/posts").glob("*.json"))
    rec = json.loads((site / "seo/data/runs.jsonl").read_text().strip().splitlines()[-1])
    assert rec["result"] == "error" and "credit balance" in rec["error"]


def test_editor_precise_edits_applied_without_rewrite(site, fake_api):  # noqa: F811
    """First review returns exact find/replace fixes: they are applied and the
    article publishes without a costly revise round."""
    fake, state = fake_api
    orig = make_responder(state)
    n = {"review": 0}

    def responder(body, headers):
        p = body["messages"][0]["content"]
        if isinstance(p, str) and p.startswith("You are the senior editor"):
            n["review"] += 1
            draft = json.loads(p.split("DRAFT:\n", 1)[1])
            sentence = draft["body_markdown"].split(". ")[0]
            return {"blocks": [text_block(json.dumps({
                "scores": {k: 7 for k in ["accuracy", "helpfulness", "intent_match", "geo_structure", "voice", "safety"]},
                "overall": 7, "improvements": [], "verdict": "revise",
                "blocking_issues": [{"issue": "overstated opener", "find": sentence,
                                     "replace": sentence + " for many people"}]}))]}
        return orig(body, headers)
    fake.responder = responder
    r = run_real(site, fake, "publish", "--no-link-check")
    assert r.returncode == 0, r.stdout + r.stderr
    post = json.loads(next((site / "seo/content/posts").glob("*.json")).read_text())
    assert "for many people" in post["body_markdown"]
    rec = json.loads((site / "seo/data/runs.jsonl").read_text().strip().splitlines()[-1])
    assert rec["stages"][-1] == "approved-with-edits@0" and n["review"] == 1
    assert not any(s.startswith("Revise this article") for s in state.get("stages", []))
