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

log = logging.getLogger(__name__)

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
  .bar {{ display: flex; gap: 8px; margin: 16px 0; flex-wrap: wrap; }}
  button, a.btn {{
    font: inherit; padding: 10px 16px; border-radius: 8px; cursor: pointer;
    border: 1px solid currentColor; background: transparent; color: inherit;
    text-decoration: none; display: inline-block;
  }}
  button:active {{ opacity: .6; }}
  pre {{
    white-space: pre-wrap; word-wrap: break-word; padding: 16px;
    border: 1px solid rgba(128,128,128,.35); border-radius: 8px;
    font: 14px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  .ok {{ opacity: 0; transition: opacity .2s; font-size: .875rem; align-self: center; }}
  .ok.show {{ opacity: .8; }}
</style>
</head>
<body>
<h1>{brand}</h1>
<div class="meta">{date} · generated automatically</div>

<img src="daily.png" alt="Today's card">

<div class="bar">
  <button id="copy">Copy caption</button>
  <a class="btn" href="daily.png" download>Download image</a>
  <span class="ok" id="ok">copied</span>
</div>

<pre id="caption">{caption}</pre>

<script>
document.getElementById('copy').addEventListener('click', async () => {{
  await navigator.clipboard.writeText(document.getElementById('caption').textContent);
  const ok = document.getElementById('ok');
  ok.classList.add('show');
  setTimeout(() => ok.classList.remove('show'), 1500);
}});
</script>
</body>
</html>
"""


def render_review_page(caption: str, brand_name: str, out_path: Path) -> Path:
    """Write a self-contained review page next to the rendered card.

    The page references ``daily.png`` relatively, so it works anywhere the two
    files sit side by side — including the gh-pages branch.
    """
    page = _PAGE.format(
        brand=html.escape(brand_name),
        date=dt.datetime.now(dt.timezone.utc).strftime("%A, %d %B %Y"),
        caption=html.escape(caption),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    log.info("review page written to %s", out_path)
    return out_path
