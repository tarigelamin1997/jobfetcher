"""The full-list report (B-1): a single, self-contained HTML page listing EVERY scored job
(surfaced + still-open + below-threshold) — score · override · fit · title · company · location ·
application status · why/gaps · apply link · dates — so the daily digest's two dead-text lines
("…and N more", "+N below") can point at the real data instead of at a LOCAL `scripts/export.py`
the email can never reach.

Dependency-free on purpose (P1): plain string templates, the same style as `core/notifier.py`.
Unlike the email this is a **standalone web page**, so it MAY carry a `<style>` block and a small
**inline vanilla `<script>`** for client-side sort/filter — no framework, no CDN, no external
asset (self-contained by construction). All user/LLM text is HTML-escaped before interpolation
(a JD title/company/reason is untrusted input); the apply link is scheme-allowlisted via the
notifier's `_safe_apply_url` (no `javascript:`/`data:` href). `render_full_list([])` is a
first-class case (VG5 spirit): a valid "no scored jobs yet" page, never a crash or a blank body.
"""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

from .models import APPLICATION_STATUSES
from .notifier import (
    _APPLY_BG,
    _badge_color,
    _display_title,
    _location,
    _safe_apply_url,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    from .ports import ShortlistItem

    # Injected `(posting_id, status) -> url | None` (INV-001): mints an HMAC-signed capture link
    # per outcome. `None` (capture unconfigured) omits the Mark column entirely — graceful.
    CaptureLink = Callable[[str, str], "str | None"]


def _join(values: list[Any]) -> str:
    """A JSONB list of short phrases (strengths/gaps) → a single `; `-joined line; blanks
    dropped. Never leaks a `None`."""
    return "; ".join(s for s in (str(v).strip() for v in (values or [])) if s)


def _fmt_date(value: "datetime | date | None") -> str:
    """A timestamp/date → its ISO `YYYY-MM-DD` (date only — the full list is a scan tool, not an
    audit log); `None` → empty string."""
    if value is None:
        return ""
    d = value.date() if hasattr(value, "date") else value
    return d.isoformat()


def _capture_cell(item: "ShortlistItem", capture_link: "CaptureLink") -> str:
    """The "Mark" cell: one signed capture link per application status (INV-001) — the full
    `APPLICATION_STATUSES` set, since a full page (unlike the email) can carry the whole outcome
    vocabulary. A status whose link comes back `None` is skipped; an empty cell shows a dash. The
    URL is our own signed link, escaped for the href; the status label is escaped too."""
    links = [
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f"{escape(status)}</a>"
        for status in APPLICATION_STATUSES
        if (url := capture_link(item.posting_id, status))
    ]
    return " &middot; ".join(links) if links else '<span class="muted">&mdash;</span>'


def _row_html(
    item: "ShortlistItem", *, threshold: int, capture_link: "CaptureLink | None" = None
) -> str:
    """One `<tr>` for the jobs table. `data-below` drives the below-threshold filter; the score
    cell carries `data-sort` so the numeric sort is by value, not lexical text. Every user/LLM
    field is escaped; the apply link is scheme-allowlisted. When `capture_link` is supplied, a
    trailing "Mark" cell carries the per-status capture links; otherwise no cell is emitted."""
    score = item.score
    below = "1" if score < threshold else "0"
    override = "" if item.score_override is None else str(item.score_override)
    fit = escape((item.fit_category or "").replace("_", " ").strip())
    title = escape(_display_title(item))
    company = escape((item.company or "").strip())
    loc = escape(_location(item))
    status = escape((item.application_status or "").strip())
    why = escape(_join(item.strengths))
    gaps = escape(_join(item.gaps))
    scored = escape(_fmt_date(item.scored_at))
    fetched = escape(_fmt_date(item.fetched_at))
    badge = _badge_color(item.fit_category)

    safe_url = _safe_apply_url(item.apply_url)
    apply_cell = (
        f'<a href="{escape(safe_url, quote=True)}" target="_blank" rel="noopener noreferrer">Apply</a>'
        if safe_url
        else '<span class="muted">no link</span>'
    )
    mark_cell = (
        f'<td class="mark">{_capture_cell(item, capture_link)}</td>'
        if capture_link is not None
        else ""
    )
    return (
        f'<tr data-below="{below}">'
        f'<td class="num" data-sort="{score}">'
        f'<span class="badge" style="background:{badge};">{score}</span></td>'
        f'<td class="num" data-sort="{override or -1}">{override}</td>'
        f"<td>{fit}</td>"
        f'<td class="title">{title}</td>'
        f"<td>{company}</td>"
        f"<td>{loc}</td>"
        f"<td>{status}</td>"
        f'<td class="why">{why}</td>'
        f'<td class="gaps">{gaps}</td>'
        f"<td>{apply_cell}</td>"
        f'<td class="date">{scored}</td>'
        f'<td class="date">{fetched}</td>'
        f"{mark_cell}"
        "</tr>"
    )


def render_full_list(
    items: "list[ShortlistItem]",
    *,
    threshold: int,
    run_date: "date",
    generated_at: "datetime | None" = None,
    capture_link: "CaptureLink | None" = None,
) -> str:
    """Render the full-list HTML page over ALL scored `items` (surfaced + below-threshold, score
    DESC). Returns a complete, self-contained `<!doctype html>` document (inline CSS + JS, no
    external asset) suitable for upload to S3 and viewing from a presigned link.

    `threshold` classifies each row (a below-threshold row is tagged for the "show below" filter);
    `run_date`/`generated_at` label the header. `render_full_list([])` returns a valid
    "no scored jobs yet" page — never a crash or a blank body (the digest's zero-scored path).

    `capture_link` (INV-001) is the injected `(posting_id, status) -> url | None` callable. When
    supplied, each row gains a trailing "Mark" column with a signed capture link per application
    status (the full page can carry the whole vocabulary); `None` omits the column entirely —
    graceful degrade, identical to the digest, when capture isn't configured."""
    day = escape(run_date.isoformat())
    gen = escape(generated_at.strftime("%Y-%m-%d %H:%M UTC")) if generated_at is not None else ""
    total = len(items)
    surfaced = sum(1 for i in items if i.score >= threshold)
    # INV-001: the Mark column exists only when capture is configured (a callable was injected).
    mark_th = "<th>Mark</th>" if capture_link is not None else ""

    if not items:
        body = '<p class="empty">No scored jobs yet.</p>'
    else:
        rows = "".join(
            _row_html(i, threshold=threshold, capture_link=capture_link) for i in items
        )
        body = f"""<div class="controls">
  <input id="q" type="search" placeholder="Filter by title, company, location…" oninput="filterRows()" />
  <label><input id="below" type="checkbox" checked onchange="filterRows()" /> show below-threshold</label>
  <span class="count"><b id="shown">{total}</b> of {total} shown</span>
</div>
<div class="scroll">
<table id="jobs">
<thead><tr>
  <th onclick="sortBy(0,true)">Score</th>
  <th onclick="sortBy(1,true)">Override</th>
  <th onclick="sortBy(2,false)">Fit</th>
  <th onclick="sortBy(3,false)">Title</th>
  <th onclick="sortBy(4,false)">Company</th>
  <th onclick="sortBy(5,false)">Location</th>
  <th onclick="sortBy(6,false)">Status</th>
  <th>Why</th>
  <th>Gaps</th>
  <th>Apply</th>
  <th onclick="sortBy(10,false)">Scored</th>
  <th onclick="sortBy(11,false)">Fetched</th>
  {mark_th}
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>JobFetcher — all scored jobs ({day})</title>
<style>
  body {{ margin:0; padding:24px 16px; background:#f4f5f7; color:#202124;
    font-family:Arial,Helvetica,sans-serif; }}
  .wrap {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#5f6368; font-size:13px; margin:0 0 16px; }}
  .controls {{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:0 0 12px; }}
  #q {{ padding:8px 10px; font-size:14px; border:1px solid #cdd0d4; border-radius:6px; min-width:240px; }}
  .count {{ color:#5f6368; font-size:13px; }}
  .scroll {{ overflow-x:auto; background:#fff; border:1px solid #e2e4e8; border-radius:8px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #eceef1; vertical-align:top; }}
  th {{ position:sticky; top:0; background:#f1f3f4; cursor:pointer; white-space:nowrap;
    color:#3c4043; font-size:12px; }}
  th:hover {{ background:#e4e7ea; }}
  tbody tr:hover {{ background:#f8f9fa; }}
  td.num {{ text-align:right; white-space:nowrap; }}
  td.date {{ white-space:nowrap; color:#5f6368; }}
  td.title {{ font-weight:bold; }}
  td.why, td.gaps {{ max-width:280px; }}
  td.gaps {{ color:#a8641b; }}
  td.mark {{ white-space:nowrap; font-size:12px; }}
  .badge {{ display:inline-block; color:#fff; font-weight:bold; padding:2px 8px; border-radius:12px; }}
  .muted {{ color:#9aa0a6; font-style:italic; }}
  a {{ color:{_APPLY_BG}; font-weight:bold; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .empty {{ color:#5f6368; font-size:15px; }}
</style>
</head>
<body>
<div class="wrap">
<h1>JobFetcher — all scored jobs</h1>
<p class="sub">{day} &middot; {total} scored ({surfaced} at or above your threshold of {threshold})\
{f" &middot; generated {gen}" if gen else ""}</p>
{body}
</div>
<script>
function filterRows() {{
  var q = (document.getElementById('q').value || '').toLowerCase();
  var showBelow = document.getElementById('below').checked;
  var shown = 0;
  var rows = document.querySelectorAll('#jobs tbody tr');
  for (var i = 0; i < rows.length; i++) {{
    var r = rows[i];
    var matchText = r.textContent.toLowerCase().indexOf(q) !== -1;
    var ok = matchText && (showBelow || r.getAttribute('data-below') !== '1');
    r.style.display = ok ? '' : 'none';
    if (ok) shown++;
  }}
  document.getElementById('shown').textContent = shown;
}}
function sortBy(col, numeric) {{
  var tb = document.querySelector('#jobs tbody');
  var rows = Array.prototype.slice.call(tb.rows);
  var dir = (tb.getAttribute('data-col') === String(col) && tb.getAttribute('data-dir') === 'asc')
    ? 'desc' : 'asc';
  rows.sort(function (a, b) {{
    var x = a.cells[col].getAttribute('data-sort');
    var y = b.cells[col].getAttribute('data-sort');
    if (x === null) x = a.cells[col].textContent.trim();
    if (y === null) y = b.cells[col].textContent.trim();
    if (numeric) {{ x = parseFloat(x) || 0; y = parseFloat(y) || 0; }}
    else {{ x = x.toLowerCase(); y = y.toLowerCase(); }}
    return (x < y ? -1 : x > y ? 1 : 0) * (dir === 'asc' ? 1 : -1);
  }});
  for (var i = 0; i < rows.length; i++) tb.appendChild(rows[i]);
  tb.setAttribute('data-col', col);
  tb.setAttribute('data-dir', dir);
}}
</script>
</body>
</html>"""
