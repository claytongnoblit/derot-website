"""Offline stand-in for the model, used by tests and `--mock` dry runs.
It produces structurally valid outputs that pass the deterministic QA gate,
so the whole pipeline (selection, rendering, publishing state) can be
exercised with no API key and no network."""
from __future__ import annotations

import re

from .util import slugify

MOCK_SOURCES = [
    ("https://pubmed.ncbi.nlm.nih.gov/36630953/", "Brief structured respiration practices enhance mood and reduce physiological arousal", "Cell Reports Medicine", "2023"),
    ("https://www.frontiersin.org/articles/10.3389/fnhum.2018.00353/full", "How breath-control can change your life: a systematic review", "Frontiers in Human Neuroscience", "2018"),
    ("https://www.frontiersin.org/articles/10.3389/fpsyg.2014.00756/full", "Heart rate variability biofeedback: how and why does it work?", "Frontiers in Psychology", "2014"),
    ("https://www.health.harvard.edu/mind-and-mood/relaxation-techniques-breath-control-helps-quell-errant-stress-response", "Relaxation techniques: breath control helps quell errant stress response", "Harvard Health Publishing", "2020"),
]


def _plan(prompt, hint):
    c = hint["candidates"][0]
    return {
        "primary_keyword": c["keyword"], "secondary_keywords": [c["keyword"] + " tips"],
        "cluster": c.get("cluster"), "content_type": c.get("content_type") if c.get("content_type") in
        ("pillar", "how-to", "explainer", "comparison", "list", "faq", "glossary") else "explainer",
        "working_title": c["keyword"].capitalize(), "angle": c.get("angle", "mock angle"),
        "search_intent": "informational", "reader_problem": "feels wired after scrolling",
        "cannibalization_check": "no overlap", "rationale": "top score",
    }


def _research(prompt, hint):
    notes = "SERP ANALYSIS\nmock\nSOURCES\n" + "\n".join(f"{u} | {t} | {p} | {y} | finding" for u, t, p, y in MOCK_SOURCES)
    return notes, [{"url": u, "title": t} for u, t, _, _ in MOCK_SOURCES]


def _brief(prompt, hint):
    return {
        "title_options": ["Mock title"], "search_intent": "informational", "serp_summary": "mock serp",
        "gaps": ["mid-scroll angle"], "outline": [{"h2": f"Section {i}", "points": ["a", "b"]} for i in range(6)],
        "questions": ["Does it work?"],
        "sources": [{"id": f"S{i + 1}", "url": u, "title": t, "publisher": p, "year": y, "key_findings": "finding"}
                    for i, (u, t, p, y) in enumerate(MOCK_SOURCES)]
        + [{"id": "S9", "url": "https://invented.example.com/fake-study", "title": "Invented", "publisher": "x",
            "year": "2024", "key_findings": "should be dropped"}],
        "key_facts": [{"fact": "Five minutes of cyclic sighing improved mood", "source_id": "S1"}],
        "safety_notes": ["stop if dizzy"], "word_count_target": 1600,
    }


def _write(prompt, hint):
    pk = hint["plan"]["primary_keyword"]
    inv = re.findall(r"^- (/[^\s]*) : ", prompt, re.M)
    internal = [p for p in inv if p not in ("/",)][:3] or ["/blog/", "/about/"]
    while len(internal) < 2:
        internal.append("/about/")
    src = MOCK_SOURCES
    para = ("Slow breathing with a longer exhale is one of the simplest ways to feel calmer, and you can do it "
            "anywhere, including halfway through a scroll that has left your shoulders up by your ears. ")
    m = re.search(r"hard minimum (\d+)", prompt)
    n_sections = max(6, int(m.group(1)) // 230 + 2) if m else 6
    sections = []
    for i in range(n_sections):
        u = src[i % len(src)][0]
        sections.append(f"## {pk.capitalize() if i == 0 else 'Section ' + str(i + 1)} in practice {i + 1}\n\n"
                        + (para * 6) + f"Research [supports this approach]({u}). "
                        + f"See also [a related guide on calming down]({internal[i % len(internal)]}).\n")
    body = (f"{pk.capitalize()} works by slowing your breath and lengthening the exhale, which helps your body "
            f"settle in about a minute. Here is how to do it, why it helps, and when to use it.\n\n" + "\n".join(sections))
    title = f"{pk.capitalize()}: a calm, practical guide"
    return {
        "title": title[:70], "meta_title": (pk.capitalize() + " | DeRot")[:60],
        "meta_description": (f"{pk.capitalize()} explained simply: what it is, how to do it in 60 seconds, and what "
                             f"the research says about feeling calmer.")[:155].ljust(115, "."),
        "slug": slugify(pk), "dek": "A plain-spoken guide.",
        "key_takeaways": ["Longer exhales help you settle.", "One minute is enough to notice a shift.",
                          "Stop if you feel dizzy."],
        "body_markdown": body,
        "faqs": [{"question": f"Question {i}?", "answer": "A clear standalone answer that gives the reader a direct, "
                  "useful response in a few sentences without needing the rest of the page for context."}
                 for i in range(4)],
        "sources_used": ["S1", "S2", "S3", "S4"], "howto_steps": ["Breathe in for 4.", "Breathe out for 6."],
        "social": {k: f"mock {k} {{url}}" for k in ["x_post", "threads_post", "linkedin_post", "pinterest_title",
                                                     "pinterest_description", "newsletter_blurb", "short_video_script"]},
    }


def _revise(prompt, hint):
    return _write(prompt, hint)


def _review(prompt, hint):
    return {"scores": {k: 9 for k in ["accuracy", "helpfulness", "intent_match", "geo_structure", "voice", "safety"]},
            "overall": 9, "blocking_issues": [], "improvements": [], "verdict": "publish"}


def _factcheck(prompt, hint):
    return "All claims SUPPORTED (mock).", []


def _triage(prompt, hint):
    return {"keywords": [{"keyword": c, "keep": i < 3, "cluster": "breathing-techniques", "intent": "informational",
                          "volume_tier": 2, "difficulty_tier": 2, "business_fit": 4, "content_type": "how-to",
                          "angle": "mock"} for i, c in enumerate(hint.get("candidates", []))]}


def _social(prompt, hint):
    post = hint["post"]
    takeaways = post.get("key_takeaways") or ["Longer exhales help you settle."]
    return {
        "cover_headline": post["title"][:70],
        "points": [{"headline": t.split(".")[0][:48], "body": t[:190]} for t in (takeaways * 3)[:3]],
        "instagram_caption": post.get("dek", "") + "\n\nA calmer scroll starts with a longer exhale.",
        "instagram_hashtags": ["#breathwork", "nervous system regulation", "breathwork"],
        "threads_text": "Slow breathing with a longer exhale helps your body settle. Here is how to use it mid-scroll.",
        "facebook_message": "A plain-spoken guide to feeling calmer when the feed has wound you up.",
    }


def default_handlers():
    return {"plan": _plan, "research": _research, "brief": _brief, "write": _write, "revise": _revise,
            "review": _review, "factcheck": _factcheck, "triage": _triage, "social": _social,
            "social_revise": _social}
