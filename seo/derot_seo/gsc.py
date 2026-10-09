"""Optional Google Search Console integration.

Enabled when GSC_SERVICE_ACCOUNT_JSON (the service account key JSON) is set.
Pulls the last N days of query and page performance into data/gsc_snapshot.json,
which the planner (striking-distance boosts), expansion (new queries), refresh
(pages to improve), and weekly report read.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Optional

import requests

from .util import DATA_DIR, today, write_json

API = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _token() -> Optional[str]:
    raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def _query(token: str, prop: str, body: dict) -> list[dict]:
    url = API.format(site=requests.utils.quote(prop, safe=""))
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.json().get("rows", [])


def pull(cfg: dict) -> Optional[dict]:
    token = _token()
    if not token:
        return None
    sc = cfg["search_console"]
    end = today() - dt.timedelta(days=2)  # GSC data lags ~2 days
    start = end - dt.timedelta(days=sc["lookback_days"])
    base = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": 5000}
    q_rows = _query(token, sc["property"], {**base, "dimensions": ["query"]})
    p_rows = _query(token, sc["property"], {**base, "dimensions": ["page"]})
    qp_rows = _query(token, sc["property"], {**base, "dimensions": ["page", "query"], "rowLimit": 10000})

    def row(r):
        return {"clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0),
                "ctr": round(r.get("ctr", 0), 4), "position": round(r.get("position", 0), 1)}

    pages = {r["keys"][0]: row(r) for r in p_rows}
    page_queries: dict[str, list] = {}
    for r in qp_rows:
        page_queries.setdefault(r["keys"][0], []).append({"query": r["keys"][1], **row(r)})
    for v in page_queries.values():
        v.sort(key=lambda x: -x["impressions"])
    snap = {
        "range": [start.isoformat(), end.isoformat()],
        "pulled": today().isoformat(),
        "totals": {
            "clicks": sum(r.get("clicks", 0) for r in p_rows),
            "impressions": sum(r.get("impressions", 0) for r in p_rows),
        },
        "queries": {r["keys"][0].lower(): row(r) for r in q_rows},
        "pages": pages,
        "page_queries": {k: v[:25] for k, v in page_queries.items()},
    }
    write_json(DATA_DIR / "gsc_snapshot.json", snap)
    return snap
