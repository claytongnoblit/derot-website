"""Minimal clients for the three Meta publishing APIs.

  * Instagram: Graph API "Instagram API with Facebook Login". Carousel = one
    child container per image, a CAROUSEL parent, then media_publish.
  * Facebook Page: a link post (the article's OG card) or a multi-photo post.
  * Threads: graph.threads.net. Image or carousel container, then threads_publish.

All three fetch images by URL, so the images must already be live on derot.org.
Tokens are sent in the request body (POST) or query (GET) and scrubbed from
every error message.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

import requests

GRAPH = "https://graph.facebook.com"
THREADS = "https://graph.threads.net"
# Error codes Meta documents as temporary (rate limits, transient server errors).
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 9004, 9007, 2207001, 2207003}


class MetaError(RuntimeError):
    def __init__(self, message: str, code: Optional[int] = None, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.transient = transient


class _Client:
    base = ""

    def __init__(self, token: str, session=None, sleep: Callable[[float], None] = time.sleep,
                 poll_interval: float = 5.0, poll_timeout: float = 240.0):
        self.token = token
        self.http = session or requests.Session()
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def _scrub(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def _req(self, method: str, path: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.token
        url = f"{self.base}/{path.lstrip('/')}"
        try:
            if method == "GET":
                r = self.http.get(url, params=params, timeout=60)
            else:
                r = self.http.post(url, data=params, timeout=120)
        except requests.RequestException as e:
            raise MetaError(self._scrub(f"network error calling {path}: {e}"), transient=True) from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code")
            sub = err.get("error_subcode")
            msg = err.get("error_user_msg") or err.get("message") or (r.text or "")[:300]
            transient = r.status_code >= 500 or code in TRANSIENT_CODES or sub in TRANSIENT_CODES \
                or bool(err.get("is_transient"))
            raise MetaError(self._scrub(f"{path}: HTTP {r.status_code} code {code}/{sub}: {msg}"),
                            code=code, transient=transient)
        return body

    def _create_with_alt(self, path: str, alt_text: Optional[str], **params) -> dict:
        """Send alt text, but retry without it if this API version rejects the field."""
        if alt_text:
            try:
                return self._req("POST", path, alt_text=alt_text[:1000], **params)
            except MetaError as e:
                if "alt_text" not in str(e).lower():
                    raise
        return self._req("POST", path, **params)


class InstagramClient(_Client):
    def __init__(self, token: str, ig_user_id: str, version: str, **kw):
        super().__init__(token, **kw)
        self.base = f"{GRAPH}/{version}"
        self.user = ig_user_id

    def whoami(self) -> dict:
        return self._req("GET", self.user, fields="username,name")

    def publishing_limit(self) -> dict:
        return self._req("GET", f"{self.user}/content_publishing_limit", fields="quota_usage,config")

    def _wait(self, container_id: str) -> None:
        waited = 0.0
        while True:
            st = self._req("GET", container_id, fields="status_code,status")
            code = st.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise MetaError(f"instagram container {container_id} {code}: {st.get('status', '')}")
            if waited >= self.poll_timeout:
                raise MetaError(f"instagram container {container_id} still {code} after {waited:.0f}s", transient=True)
            self.sleep(self.poll_interval)
            waited += self.poll_interval

    def post(self, caption: str, images: list[dict]) -> dict:
        """images: [{"url", "alt"}]. One image = single post, 2-10 = carousel."""
        images = images[:10]
        if len(images) == 1:
            c = self._create_with_alt(f"{self.user}/media", images[0].get("alt"),
                                      image_url=images[0]["url"], caption=caption)
            creation = c["id"]
        else:
            children = []
            for im in images:
                c = self._create_with_alt(f"{self.user}/media", im.get("alt"),
                                          image_url=im["url"], is_carousel_item="true")
                children.append(c["id"])
            for cid in children:
                self._wait(cid)
            parent = self._req("POST", f"{self.user}/media", media_type="CAROUSEL",
                               children=",".join(children), caption=caption)
            creation = parent["id"]
        self._wait(creation)
        pub = self._req("POST", f"{self.user}/media_publish", creation_id=creation)
        media_id = pub["id"]
        permalink = None
        try:
            permalink = self._req("GET", media_id, fields="permalink").get("permalink")
        except MetaError:
            pass
        return {"id": media_id, "permalink": permalink}


class FacebookPageClient(_Client):
    def __init__(self, token: str, page_id: str, version: str, **kw):
        super().__init__(token, **kw)
        self.base = f"{GRAPH}/{version}"
        self.page = page_id

    def whoami(self) -> dict:
        return self._req("GET", self.page, fields="name,instagram_business_account")

    def post_link(self, message: str, link: str) -> dict:
        res = self._req("POST", f"{self.page}/feed", message=message, link=link)
        return self._finish(res["id"])

    def post_photos(self, message: str, images: list[dict]) -> dict:
        ids = []
        for im in images[:10]:
            r = self._req("POST", f"{self.page}/photos", url=im["url"], published="false",
                          alt_text_custom=im.get("alt"))
            ids.append(r["id"])
        attached = json.dumps([{"media_fbid": i} for i in ids])
        res = self._req("POST", f"{self.page}/feed", message=message, attached_media=attached)
        return self._finish(res["id"])

    def _finish(self, post_id: str) -> dict:
        permalink = None
        try:
            permalink = self._req("GET", post_id, fields="permalink_url").get("permalink_url")
        except MetaError:
            pass
        return {"id": post_id, "permalink": permalink}


class ThreadsClient(_Client):
    def __init__(self, token: str, user_id: str = "me", **kw):
        super().__init__(token, **kw)
        self.base = f"{THREADS}/v1.0"
        self.user = user_id or "me"

    def whoami(self) -> dict:
        return self._req("GET", "me", fields="id,username")

    def _wait(self, container_id: str) -> None:
        waited = 0.0
        while True:
            st = self._req("GET", container_id, fields="status,error_message")
            s = st.get("status")
            if s in ("FINISHED", "PUBLISHED"):
                return
            if s in ("ERROR", "EXPIRED"):
                raise MetaError(f"threads container {container_id} {s}: {st.get('error_message', '')}")
            if waited >= self.poll_timeout:
                raise MetaError(f"threads container {container_id} still {s} after {waited:.0f}s", transient=True)
            self.sleep(self.poll_interval)
            waited += self.poll_interval

    def post(self, text: str, images: list[dict]) -> dict:
        """No images = text post with a link preview; 1 = image post; 2-20 = carousel."""
        if not images:
            c = self._req("POST", f"{self.user}/threads", media_type="TEXT", text=text)
            creation = c["id"]
        elif len(images) == 1:
            c = self._create_with_alt(f"{self.user}/threads", images[0].get("alt"),
                                      media_type="IMAGE", image_url=images[0]["url"], text=text)
            creation = c["id"]
        else:
            children = []
            for im in images[:20]:
                c = self._create_with_alt(f"{self.user}/threads", im.get("alt"), media_type="IMAGE",
                                          image_url=im["url"], is_carousel_item="true")
                children.append(c["id"])
            for cid in children:
                self._wait(cid)
            parent = self._req("POST", f"{self.user}/threads", media_type="CAROUSEL",
                               children=",".join(children), text=text)
            creation = parent["id"]
        self._wait(creation)
        pub = self._req("POST", f"{self.user}/threads_publish", creation_id=creation)
        media_id = pub["id"]
        permalink = None
        try:
            permalink = self._req("GET", media_id, fields="permalink").get("permalink")
        except MetaError:
            pass
        return {"id": media_id, "permalink": permalink}

    def refresh_token(self) -> dict:
        """Long-lived Threads tokens last 60 days; refreshing returns a new 60-day token."""
        saved = self.base
        self.base = THREADS
        try:
            return self._req("GET", "refresh_access_token", grant_type="th_refresh_token")
        finally:
            self.base = saved


# ------------------------------------------------------------ setup helpers
def exchange_facebook(app_id: str, app_secret: str, short_token: str, version: str, session=None) -> dict:
    """Short-lived user token -> long-lived user token -> never-expiring Page tokens.
    Returns {"pages": [{"name", "id", "access_token", "instagram_business_account"}]}."""
    http = session or requests.Session()
    r = http.get(f"{GRAPH}/{version}/oauth/access_token", timeout=60, params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short_token})
    body = r.json()
    if "access_token" not in body:
        raise MetaError(f"token exchange failed: {body.get('error', {}).get('message', body)}")
    long_user = body["access_token"]
    r = http.get(f"{GRAPH}/{version}/me/accounts", timeout=60, params={
        "access_token": long_user, "fields": "name,id,access_token,instagram_business_account{id,username}"})
    body = r.json()
    if "data" not in body:
        raise MetaError(f"listing pages failed: {body.get('error', {}).get('message', body)}")
    return {"pages": body["data"]}


def exchange_threads(app_secret: str, short_token: str, session=None) -> dict:
    """Short-lived Threads token (1 hour) -> long-lived (60 days). Returns {access_token, expires_in, user}."""
    http = session or requests.Session()
    r = http.get(f"{THREADS}/access_token", timeout=60, params={
        "grant_type": "th_exchange_token", "client_secret": app_secret, "access_token": short_token})
    body = r.json()
    if "access_token" not in body:
        raise MetaError(f"threads token exchange failed: {body.get('error', {}).get('message', body)}")
    me = http.get(f"{THREADS}/v1.0/me", timeout=60,
                  params={"fields": "id,username", "access_token": body["access_token"]}).json()
    return {**body, "user": me}
