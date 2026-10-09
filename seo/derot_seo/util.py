"""Shared helpers: paths, config, JSON IO, text utilities."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

SEO_DIR = Path(__file__).resolve().parent.parent          # derot-website/seo
SITE_DIR = SEO_DIR.parent                                  # derot-website (site root)
DATA_DIR = SEO_DIR / "data"
POSTS_DIR = SEO_DIR / "content" / "posts"
DRAFTS_DIR = SEO_DIR / "content" / "drafts"
OUT_DIR = SEO_DIR / "out"
TEMPLATES_DIR = SEO_DIR / "templates"
BRAND_DIR = SEO_DIR / "brand"
ASSETS_DIR = SEO_DIR / "assets"
PAGES_DIR = SEO_DIR / "pages"


def load_config() -> dict:
    with open(SEO_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    """Atomic, stable (sorted-free but indented) JSON write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def today() -> dt.date:
    override = os.environ.get("DEROT_SEO_TODAY")  # for tests and backfills
    if override:
        return dt.date.fromisoformat(override)
    return dt.datetime.now(dt.timezone.utc).date()


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def slugify(text: str, max_len: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text or "post"


STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "with", "is", "are",
    "do", "does", "how", "what", "why", "when", "can", "my", "your", "you", "i", "me",
    "it", "vs", "versus", "best", "way", "ways",
}


def stem(w: str) -> str:
    """Crude stemmer so breathe/breathing/breaths and exercise/exercises collide."""
    for suf in ("ing", "es", "ed", "s"):
        if len(w) > 4 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def tokens(text: str) -> set[str]:
    # Hyphens count as spaces, so "4-7-8" matches "4 7 8".
    words = re.findall(r"[a-z0-9]+", text.lower().replace("\u2019", "'").replace("'", ""))
    return {stem(w) for w in words if w not in STOPWORDS}


def parse_ts(value: str) -> dt.datetime:
    """ISO timestamp -> aware UTC datetime (naive values are treated as UTC)."""
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def word_count(markdown_text: str) -> int:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown_text)
    return len(re.findall(r"[A-Za-z0-9']+", text))


def load_posts(include_drafts: bool = False) -> list[dict]:
    posts = []
    dirs = [POSTS_DIR] + ([DRAFTS_DIR] if include_drafts else [])
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            posts.append(read_json(p))
    posts.sort(key=lambda p: (p.get("date_published", ""), p.get("slug", "")), reverse=True)
    return posts


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
