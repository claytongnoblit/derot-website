from derot_seo import site
from derot_seo.util import parse_ts


def test_raw_html_escaped_in_render():
    html, _ = site.render_markdown("Hello <script>alert(1)</script> and <iframe src=x>\n\n## Head")
    assert "<script>" not in html and "<iframe" not in html
    assert "&lt;script&gt;" in html or "&lt;script>" in html


def test_markdown_link_with_parens_renders_whole_url():
    html, _ = site.render_markdown("[x](https://www.thelancet.com/article/S0140-6736(20)30001-1/fulltext)")
    assert 'href="https://www.thelancet.com/article/S0140-6736(20)30001-1/fulltext"' in html


def test_parse_ts_naive_is_utc():
    assert parse_ts("2026-10-12T13:00:00").utcoffset().total_seconds() == 0
    assert parse_ts("2026-10-12T13:00:00Z").utcoffset().total_seconds() == 0
