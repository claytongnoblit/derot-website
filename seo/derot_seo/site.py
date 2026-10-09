"""Static site builder. Post JSON in seo/content/posts is the source of truth;
every build re-renders all generated pages so related links, hubs, the
sitemap, feeds, and llms.txt always agree with each other."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
from email.utils import format_datetime
from pathlib import Path
from typing import Optional

import markdown as md_lib
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .util import (ASSETS_DIR, DATA_DIR, PAGES_DIR, SITE_DIR, TEMPLATES_DIR, load_posts, read_json,
                   read_text, today, word_count, write_json)

MANIFEST = DATA_DIR / "generated_files.json"
HOME_START = "<!-- seo:latest-posts:start -->"
HOME_END = "<!-- seo:latest-posts:end -->"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)),
                      autoescape=select_autoescape(["html"]), trim_blocks=False, lstrip_blocks=False)
    return env


def _human(d: str) -> str:
    x = dt.date.fromisoformat(d)
    return f"{x.strftime('%B')} {x.day}, {x.year}"


def _jsonld(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1).replace("</", "<\\/")


def reading_minutes(post: dict) -> int:
    return max(1, round(word_count(post.get("body_markdown", "")) / 230))


# ------------------------------------------------------------ markdown
def render_markdown(text: str) -> tuple[str, list[dict]]:
    md = md_lib.Markdown(extensions=["extra", "sane_lists", "toc"],
                         extension_configs={"toc": {"toc_depth": "2-3", "permalink": False}})
    # Model text is untrusted: neutralize any raw HTML so only markdown renders.
    text = re.sub(r"<(?=\s*/?\s*[a-zA-Z!?])", "&lt;", text)
    out = md.convert(text)
    toc = [{"id": t["id"], "name": html.unescape(t["name"])} for t in md.toc_tokens]
    # External links: safe rel. Internal links: leave as-is.
    out = re.sub(r'<a href="(https?://(?!derot\.org)[^"]+)"', r'<a href="\1" rel="noopener"', out)
    out = out.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    return out, toc


# ------------------------------------------------------------ inventory
def tool_pages() -> list[dict]:
    tools_dir = SITE_DIR / "tools"
    pages = []
    if not tools_dir.exists():
        return pages
    for idx in sorted(tools_dir.glob("**/index.html")):
        rel = "/" + str(idx.parent.relative_to(SITE_DIR)).replace("\\", "/") + "/"
        text = idx.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"<title>(.*?)</title>", text, re.S)
        title = html.unescape(m.group(1).strip()) if m else rel
        title = re.sub(r"\s*[|·]\s*DeRot\s*$", "", title)
        d = re.search(r'<meta name="description" content="([^"]*)"', text)
        pages.append({"url": rel, "title": title, "description": html.unescape(d.group(1)) if d else ""})
    return pages


def inventory(posts: list[dict], clusters: dict, extra_cluster: Optional[str] = None) -> dict:
    inv = {
        "/": {"title": "DeRot home: a calm reset for the apps that wind you up", "type": "home"},
        "/blog/": {"title": "DeRot blog", "type": "hub"},
        "/about/": {"title": "About DeRot and our editorial standards", "type": "about"},
    }
    for t in tool_pages():
        inv[t["url"]] = {"title": t["title"], "type": "tool"}
    used = {p.get("cluster") for p in posts}
    if extra_cluster:
        used.add(extra_cluster)
    for cid in used:
        if cid in clusters:
            inv[f"/blog/topics/{cid}/"] = {"title": f"{clusters[cid]['name']} (topic hub)", "type": "hub"}
    for p in posts:
        inv[f"/blog/{p['slug']}/"] = {"title": p["title"], "type": "post", "slug": p["slug"]}
    return inv


def tool_for(post: dict, cfg: dict) -> dict:
    hay = (post.get("primary_keyword", "") + " " + post.get("cluster", "")).lower()
    available = {t["url"] for t in tool_pages()}
    for rule in cfg["tool_links"]:
        if (not rule["match"] or any(m in hay for m in rule["match"])) and (rule["url"] in available or not available):
            return rule
    return {"url": "/tools/", "label": "Free breathing exercises"}


# ------------------------------------------------------------ JSON-LD
def org_ld(cfg: dict) -> dict:
    s = cfg["site"]
    return {"@type": "Organization", "@id": f"{s['base_url']}/#org", "name": "DeRot", "url": s["base_url"] + "/",
            "logo": {"@type": "ImageObject", "url": s["logo"]}, "email": "support@derot.org",
            "parentOrganization": {"@type": "Organization", "name": s["publisher"]}}


def post_jsonld(post: dict, cfg: dict, cluster_name: str, og_image: str) -> list[str]:
    base = cfg["site"]["base_url"]
    url = f"{base}/blog/{post['slug']}/"
    article = {
        "@context": "https://schema.org", "@type": "BlogPosting", "@id": url + "#article",
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "headline": post["title"][:110], "description": post["meta_description"],
        "image": [og_image], "datePublished": post["date_published"],
        "dateModified": post.get("date_modified") or post["date_published"],
        "author": {"@type": "Organization", "name": "DeRot Editorial", "url": f"{base}/about/"},
        "publisher": org_ld(cfg), "inLanguage": "en-US", "articleSection": cluster_name,
        "keywords": ", ".join([post["primary_keyword"], *post.get("secondary_keywords", [])]),
        "wordCount": word_count(post["body_markdown"]),
        "about": {"@type": "Thing", "name": post["primary_keyword"]},
        "citation": [{"@type": "CreativeWork", "name": s["title"], "url": s["url"]} for s in post.get("sources", [])],
        "isAccessibleForFree": True,
    }
    blocks = [_jsonld(article)]
    if post.get("faqs"):
        blocks.append(_jsonld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["question"],
             "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}} for f in post["faqs"]]}))
    if post.get("howto_steps"):
        blocks.append(_jsonld({"@context": "https://schema.org", "@type": "HowTo", "name": post["title"],
                               "description": post["meta_description"],
                               "step": [{"@type": "HowToStep", "position": i + 1, "text": s}
                                        for i, s in enumerate(post["howto_steps"])]}))
    blocks.append(_jsonld({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
        {"@type": "ListItem", "position": 2, "name": "Blog", "item": base + "/blog/"},
        {"@type": "ListItem", "position": 3, "name": cluster_name, "item": f"{base}/blog/topics/{post['cluster']}/"},
        {"@type": "ListItem", "position": 4, "name": post["title"], "item": url}]}))
    return blocks


# ------------------------------------------------------------ OG images
def og_image(post: dict, cluster_name: str, force: bool = False) -> Path:
    out = SITE_DIR / "img" / "blog" / f"{post['slug']}.png"
    if out.exists() and not force:
        return out
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (12, 9, 7))
    glow = Image.new("RGB", (W, H), (12, 9, 7))
    g = ImageDraw.Draw(glow)
    for r, col in [(760, (58, 23, 10)), (560, (110, 44, 15)), (380, (180, 80, 28)), (220, (232, 118, 46))]:
        g.ellipse([W - 260 - r, -260 - r // 2, W - 260 + r, -260 + r + r // 2], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = Image.blend(img, glow, 0.85)
    d = ImageDraw.Draw(img)
    fonts = ASSETS_DIR / "fonts"
    serif = ImageFont.truetype(str(fonts / "DMSerifDisplay-Regular.ttf"), 68)
    sans = ImageFont.truetype(str(fonts / "DMSans-Variable.ttf"), 28)
    small = ImageFont.truetype(str(fonts / "DMSans-Variable.ttf"), 24)

    words, lines, cur = post["title"].split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=serif) <= W - 160:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    size = 68
    while len(lines) > 4 and size > 44:  # shrink long titles
        size -= 6
        serif = ImageFont.truetype(str(fonts / "DMSerifDisplay-Regular.ttf"), size)
        words, lines, cur = post["title"].split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if d.textlength(trial, font=serif) <= W - 160:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    lines = lines[:5]
    d.text((80, 78), cluster_name.upper(), font=small, fill=(242, 196, 138))
    y = 140
    for ln in lines:
        d.text((80, y), ln, font=serif, fill=(245, 237, 227))
        y += int(size * 1.18)
    # horizon line + wordmark
    for x in range(80, 520):
        a = min(1.0, (x - 80) / 120, (520 - x) / 120)
        d.line([(x, H - 104), (x, H - 102)], fill=(int(12 + (242 - 12) * a), int(9 + (163 - 9) * a), int(7 + (65 - 7) * a)))
    d.text((80, H - 84), "DeRot", font=ImageFont.truetype(str(fonts / "DMSerifDisplay-Regular.ttf"), 40), fill=(245, 237, 227))
    d.text((200, H - 74), "derot.org  ·  Regulate, don't restrict.", font=sans, fill=(185, 172, 158))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


# ------------------------------------------------------------ build
def build(cfg: dict, data: dict, regenerate_images: bool = False) -> list[str]:
    env = _env()
    base = cfg["site"]["base_url"]
    year = today().year
    clusters = {c["id"]: c for c in data["clusters"]}
    posts = load_posts()
    written: list[str] = []
    tools = tool_pages()

    def put(rel: str, content: str) -> None:
        path = SITE_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        written.append(rel)

    def card(p: dict) -> dict:
        return {**p, "cluster_name": clusters.get(p.get("cluster"), {}).get("name", "Journal"),
                "date_human": _human(p["date_published"]), "reading_minutes": reading_minutes(p)}

    common = dict(base_url=base, year=year, app_store_url=cfg["site"].get("app_store_url") or "")

    # posts
    for p in posts:
        cname = clusters.get(p.get("cluster"), {}).get("name", "Journal")
        img = og_image(p, cname, force=regenerate_images)
        written.append(str(img.relative_to(SITE_DIR)))
        og_url = f"{base}/img/blog/{p['slug']}.png"
        body_html, toc = render_markdown(p["body_markdown"])
        same = [q for q in posts if q["slug"] != p["slug"] and q.get("cluster") == p.get("cluster")]
        other = [q for q in posts if q["slug"] != p["slug"] and q.get("cluster") != p.get("cluster")]
        related = [card(q) for q in (same + other)[:3]]
        html_out = env.get_template("post.html").render(
            **common, post=p, cluster_name=cname, body_html=body_html,
            toc=[t for t in toc if True][:12] if toc else [], related=related, tool=tool_for(p, cfg),
            meta_title=p["meta_title"], meta_description=p["meta_description"], og_title=p["title"],
            canonical=f"{base}/blog/{p['slug']}/", og_image=og_url, og_type="article",
            published_iso=p["date_published"], modified_iso=p.get("date_modified") or p["date_published"],
            published_human=_human(p["date_published"]),
            modified_human=_human(p.get("date_modified") or p["date_published"]),
            reading_minutes=reading_minutes(p), jsonld=post_jsonld(p, cfg, cname, og_url))
        put(f"blog/{p['slug']}/index.html", html_out)

    # topic chips
    counts: dict[str, int] = {}
    for p in posts:
        counts[p.get("cluster")] = counts.get(p.get("cluster"), 0) + 1
    topics = [{"id": cid, "name": clusters[cid]["name"], "count": n}
              for cid, n in sorted(counts.items(), key=lambda x: (-x[1], x[0] or "")) if cid in clusters]
    tool_cards = [{"url": t["url"], "title": t["title"].split(":")[0].strip()} for t in tools if t["url"] != "/tools/"]

    def listing_ld(url: str, name: str, desc: str, items: list[dict], crumbs: list[tuple[str, str]]) -> list[str]:
        return [
            _jsonld({"@context": "https://schema.org", "@type": "CollectionPage", "@id": url, "url": url,
                     "name": name, "description": desc, "isPartOf": {"@type": "WebSite", "url": base + "/", "name": "DeRot"},
                     "mainEntity": {"@type": "ItemList", "itemListElement": [
                         {"@type": "ListItem", "position": i + 1, "url": f"{base}/blog/{p['slug']}/", "name": p["title"]}
                         for i, p in enumerate(items)]}}),
            _jsonld({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": n, "item": u} for i, (n, u) in enumerate(crumbs)]}),
        ]

    # blog index
    intro = ("Plain-spoken guides to breathwork, calming your nervous system, and feeling better after the scroll. "
             "Evidence first, no guilt.")
    feat = next((p for p in posts if p.get("content_type") == "pillar"), posts[0] if posts else None)
    put("blog/index.html", env.get_template("listing.html").render(
        **common, eyebrow="The DeRot blog", heading="Calm, explained", intro=intro, crumbs=None,
        topics=topics, featured=({**feat, "label": "Start here" if feat.get("content_type") == "pillar" else "Latest"} if feat else None),
        posts=[card(p) for p in posts if not feat or p["slug"] != feat["slug"]], tools=tool_cards,
        meta_title="DeRot Blog: Breathwork, Calm, and Mindful Scrolling",
        meta_description="Evidence-based guides to breathing techniques, calming your nervous system, and feeling settled after scrolling. From the makers of DeRot.",
        canonical=f"{base}/blog/", og_image=cfg["site"]["default_og_image"],
        jsonld=listing_ld(f"{base}/blog/", "DeRot blog", intro, posts,
                          [("Home", base + "/"), ("Blog", base + "/blog/")])))

    # topic hubs
    for cid, n in counts.items():
        if cid not in clusters:
            continue
        c = clusters[cid]
        cps = [p for p in posts if p.get("cluster") == cid]
        pillar = next((p for p in cps if p.get("content_type") == "pillar"), None)
        url = f"{base}/blog/topics/{cid}/"
        put(f"blog/topics/{cid}/index.html", env.get_template("listing.html").render(
            **common, eyebrow="Topic", heading=c["name"], intro=c["description"],
            crumbs=[{"url": "/", "name": "Home"}, {"url": "/blog/", "name": "Blog"}],
            topics=[{**t, "active": t["id"] == cid} for t in topics],
            featured=({**pillar, "label": "The complete guide"} if pillar else None),
            posts=[card(p) for p in cps if not pillar or p["slug"] != pillar["slug"]], tools=tool_cards,
            meta_title=f"{c['name']} | DeRot Blog"[:62],
            meta_description=(c["description"][:150]).rstrip(),
            canonical=url, og_image=cfg["site"]["default_og_image"],
            jsonld=listing_ld(url, c["name"], c["description"], cps,
                              [("Home", base + "/"), ("Blog", base + "/blog/"), (c["name"], url)])))

    # about
    about_src = read_text(PAGES_DIR / "about.md")
    fm, body = about_src.split("---", 2)[1:]
    meta = yaml.safe_load(fm)
    body_html, _ = render_markdown(body)
    put("about/index.html", env.get_template("page.html").render(
        **common, heading=meta["title"], intro=meta["intro"], body_html=body_html,
        meta_title=meta["meta_title"], meta_description=meta["meta_description"],
        canonical=f"{base}/about/", og_image=cfg["site"]["default_og_image"],
        jsonld=[_jsonld({"@context": "https://schema.org", "@type": "AboutPage", "url": f"{base}/about/",
                         "mainEntity": org_ld(cfg)})]))

    shutil.copyfile(ASSETS_DIR / "blog.css", SITE_DIR / "blog" / "blog.css")
    written.append("blog/blog.css")

    put("blog/feed.xml", build_feed(cfg, posts, clusters))
    put("sitemap.xml", build_sitemap(cfg, posts, counts, tools))
    put("robots.txt", build_robots(cfg))
    put("llms.txt", build_llms(cfg, posts, tools, clusters))
    put("llms-full.txt", build_llms_full(cfg, posts))
    if cfg.get("indexnow", {}).get("enabled"):
        put(f"{cfg['indexnow']['key']}.txt", cfg["indexnow"]["key"])
    put(".nojekyll", "")
    update_home(posts, clusters)

    # remove files generated by a previous build that no longer exist (e.g. a deleted post)
    old = set(read_json(MANIFEST, []) or [])
    now = set(written)
    for rel in sorted(old - now):
        f = SITE_DIR / rel
        if f.exists() and (rel.startswith("blog/") or rel.startswith("img/blog/")):
            f.unlink()
            try:
                f.parent.rmdir()
            except OSError:
                pass
    write_json(MANIFEST, sorted(now))
    return sorted(now)


def build_feed(cfg: dict, posts: list[dict], clusters: dict) -> str:
    base = cfg["site"]["base_url"]
    items = []
    for p in posts[:30]:
        url = f"{base}/blog/{p['slug']}/"
        body_html, _ = render_markdown(p["body_markdown"])
        body_html = body_html.replace('href="/', f'href="{base}/')
        pub = dt.datetime.fromisoformat(p["date_published"] + "T13:00:00+00:00")
        items.append(f"""    <item>
      <title>{html.escape(p['title'])}</title>
      <link>{url}</link>
      <guid isPermaLink="true">{url}</guid>
      <pubDate>{format_datetime(pub)}</pubDate>
      <category>{html.escape(clusters.get(p.get('cluster'), {}).get('name', 'Journal'))}</category>
      <description>{html.escape(p['meta_description'])}</description>
      <content:encoded><![CDATA[{body_html.replace(']]>', ']]&gt;')}]]></content:encoded>
    </item>""")
    last = format_datetime(dt.datetime.fromisoformat(((posts[0].get("date_modified") or posts[0]["date_published"]) if posts else today().isoformat()) + "T13:00:00+00:00"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>DeRot Blog</title>
    <link>{base}/blog/</link>
    <atom:link href="{base}/blog/feed.xml" rel="self" type="application/rss+xml" />
    <description>Breathwork, calm, and feeling better after the scroll.</description>
    <language>en-us</language>
    <lastBuildDate>{last}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""


def build_sitemap(cfg: dict, posts: list[dict], counts: dict, tools: list[dict]) -> str:
    base = cfg["site"]["base_url"]
    latest = max((p.get("date_modified") or p["date_published"] for p in posts), default=None)
    urls: list[tuple[str, Optional[str]]] = [(base + "/", None), (base + "/blog/", latest), (base + "/about/", None),
                                             (base + "/support.html", None), (base + "/privacy.html", None)]
    urls += [(base + t["url"], None) for t in tools]
    for cid in counts:
        cl = max(p.get("date_modified") or p["date_published"] for p in posts if p.get("cluster") == cid)
        urls.append((f"{base}/blog/topics/{cid}/", cl))
    urls += [(f"{base}/blog/{p['slug']}/", p.get("date_modified") or p["date_published"]) for p in posts]
    rows = []
    for u, lm in urls:
        rows.append(f"  <url><loc>{html.escape(u)}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


def build_robots(cfg: dict) -> str:
    base = cfg["site"]["base_url"]
    # AI search and answer-engine crawlers are explicitly welcome: being cited is the point of GEO.
    return f"""# derot.org robots.txt (generated by seo/derot_seo/site.py)
User-agent: *
Allow: /
Disallow: /seo/
Disallow: /marketing/

User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Claude-SearchBot
Allow: /

User-agent: Claude-User
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Perplexity-User
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Applebot-Extended
Allow: /

Sitemap: {base}/sitemap.xml
"""


def build_llms(cfg: dict, posts: list[dict], tools: list[dict], clusters: dict) -> str:
    base = cfg["site"]["base_url"]
    lines = ["# DeRot", "",
             "> DeRot is an iOS app that pauses your social media scroll mid-session and walks you through a "
             "60-second calming breath exercise (Calm 4-6, box breathing, 4-7-8, or custom), so you put the phone "
             "down feeling settled instead of wired. Tagline: Regulate, don't restrict. derot.org publishes "
             "evidence-based guides on breathwork, nervous system regulation, and calmer phone use, plus free "
             "browser breathing tools.", "",
             "Key facts: made by NinetyFourVentures LLC; no account required; works on-device; DeRot never locks "
             "you out of apps, it interrupts mid-scroll with a breathable pause. Contact: support@derot.org.", "",
             "## Breathing tools"]
    for t in tools:
        lines.append(f"- [{t['title']}]({base}{t['url']}): {t['description']}")
    by_cluster: dict[str, list] = {}
    for p in posts:
        by_cluster.setdefault(p.get("cluster"), []).append(p)
    for cid, ps in by_cluster.items():
        lines += ["", f"## {clusters.get(cid, {}).get('name', 'Articles')}"]
        for p in ps:
            lines.append(f"- [{p['title']}]({base}/blog/{p['slug']}/): {p['meta_description']}")
    lines += ["", "## About", f"- [About DeRot and editorial standards]({base}/about/)",
              f"- [Full text of all articles]({base}/llms-full.txt)", ""]
    return "\n".join(lines)


def build_llms_full(cfg: dict, posts: list[dict]) -> str:
    base = cfg["site"]["base_url"]
    parts = ["# DeRot blog: full text", ""]
    for p in posts:
        parts += [f"# {p['title']}", "", f"URL: {base}/blog/{p['slug']}/",
                  f"Updated: {p.get('date_modified') or p['date_published']}", "",
                  "Key takeaways:", *[f"- {t}" for t in p["key_takeaways"]], "", p["body_markdown"], "",
                  "## FAQ", *[f"**{f['question']}**\n{f['answer']}\n" for f in p["faqs"]],
                  "## Sources", *[f"- {s['title']}: {s['url']}" for s in p.get("sources", [])], "", "---", ""]
    return "\n".join(parts)


def update_home(posts: list[dict], clusters: dict) -> bool:
    """Refresh the 'From the blog' block on the homepage between marker comments."""
    index = SITE_DIR / "index.html"
    if not index.exists():
        return False
    text = index.read_text(encoding="utf-8")
    if HOME_START not in text or HOME_END not in text:
        return False
    if posts:
        cards = "\n".join(
            f'        <a class="home-post glass" href="/blog/{p["slug"]}/"><span class="hp-topic">'
            f'{html.escape(clusters.get(p.get("cluster"), {}).get("name", "Blog"))}</span><span class="hp-title">'
            f'{html.escape(p["title"])}</span></a>' for p in posts[:3])
        block = (f'{HOME_START}\n  <section class="home-blog" aria-labelledby="home-blog-title">\n'
                 f'    <h2 id="home-blog-title" class="section-title">From the blog</h2>\n'
                 f'    <div class="home-posts">\n{cards}\n    </div>\n'
                 f'    <p class="home-blog-more"><a href="/blog/">All articles</a> · <a href="/tools/">Free breathing tools</a></p>\n'
                 f'  </section>\n  {HOME_END}')
    else:
        block = f"{HOME_START}\n  {HOME_END}"
    new = re.sub(re.escape(HOME_START) + r".*?" + re.escape(HOME_END), lambda _: block, text, flags=re.S)
    if new != text:
        index.write_text(new, encoding="utf-8")
        return True
    return False
