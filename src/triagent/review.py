"""Render a daily review page so the post is usable without API publishing.

The build step always produces a card and a caption. When automated publishing
is unavailable — Meta credentials missing, developer access blocked, token
expired — those artifacts are still the finished product; they're just buried
in the Actions artifact zip. This module puts them on a page you can open each
morning and post from in about thirty seconds.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from pathlib import Path
from typing import Protocol, Sequence

log = logging.getLogger(__name__)


class Story(Protocol):
    """Anything with a title, url and source — NewsItem satisfies this."""

    title: str
    url: str
    source: str

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{brand} — {date}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 620px; margin: 0 auto; padding: 24px 16px 64px;
  }}
  h1 {{ font-size: 1.25rem; margin: 0 0 4px; }}
  .meta {{ opacity: .65; font-size: .875rem; margin-bottom: 24px; }}
  img {{ width: 100%; height: auto; border-radius: 12px; display: block; }}
  h2 {{ font-size: 1rem; text-transform: uppercase; letter-spacing: .06em;
        opacity: .6; margin: 32px 0 12px; }}
  ol.stories {{ padding-left: 0; list-style: none; margin: 0; }}
  ol.stories li {{ padding: 14px 0; border-bottom: 1px solid rgba(128,128,128,.25); }}
  ol.stories a {{ color: inherit; font-weight: 600; text-decoration: none; }}
  ol.stories a:hover {{ text-decoration: underline; }}
  .src {{ display: block; font-size: .8rem; opacity: .55; margin-top: 4px;
          text-transform: uppercase; letter-spacing: .04em; }}
</style>
</head>
<body>
<h1>{brand}</h1>
<div class="meta">{date} · generated automatically</div>

<img src="{image_name}" alt="Today's card">

<h2>Today's stories</h2>
<ol class="stories">
{stories}
</ol>
</body>
</html>
"""


def render_review_page(
    brand_name: str,
    out_path: Path,
    stories: Sequence[Story] = (),
    image_name: str = "daily.png",
) -> Path:
    """Write the public link-in-bio page next to the rendered card.

    The page references the card relatively, so it works anywhere the two files
    sit side by side — including the gh-pages branch.

    ``stories`` become real links to the source articles. The post's CTA sends
    people here for more news, so the page has to actually contain it.

    The caption is deliberately absent. It was shown here while this doubled as
    an operator page for posting by hand, but the bio link makes it public, and
    a reader has no use for the raw caption of the post they just came from. It
    is still written to caption.txt and uploaded with the run artifacts.
    """
    items = "\n".join(
        f'  <li><a href="{html.escape(s.url, quote=True)}" target="_blank" '
        f'rel="noopener">{html.escape(s.title)}</a>'
        f'<span class="src">{html.escape(s.source)}</span></li>'
        for s in stories
    ) or "  <li>No sources recorded for today.</li>"

    page = _PAGE.format(
        brand=html.escape(brand_name),
        date=dt.datetime.now(dt.timezone.utc).strftime("%A, %d %B %Y"),
        stories=items,
        image_name=html.escape(image_name, quote=True),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    log.info("review page written to %s", out_path)
    return out_path
