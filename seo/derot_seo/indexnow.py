"""IndexNow: tells Bing (which powers ChatGPT search and Copilot), Yandex,
Seznam, and Naver that URLs changed, so new posts are crawled in hours."""
from __future__ import annotations

import requests

from .util import load_config


def ping(urls: list[str]) -> int:
    cfg = load_config()
    ix = cfg.get("indexnow", {})
    if not ix.get("enabled") or not urls:
        print("IndexNow: nothing to send")
        return 0
    base = cfg["site"]["base_url"]
    host = base.split("://", 1)[1]
    body = {"host": host, "key": ix["key"], "keyLocation": f"{base}/{ix['key']}.txt", "urlList": urls}
    try:
        r = requests.post("https://api.indexnow.org/indexnow", json=body, timeout=30)
        print(f"IndexNow: HTTP {r.status_code} for {len(urls)} URL(s)")
    except requests.RequestException as e:
        print(f"IndexNow failed (non-fatal): {e}")
    return 0  # never fail a publish over a ping
