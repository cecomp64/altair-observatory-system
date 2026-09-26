"""A small Markdown renderer for Altair's own reports on the local status
page: headings, paragraphs, lists, GFM tables, **bold**, *italic*, `code`,
http(s) links and relative images. Everything is HTML-escaped first, so raw
HTML in a report is shown as text, never run. (The Hub renders the same
reports with kramdown.)"""
from __future__ import annotations

import html
import re

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![*\w])\*([^*]+)\*(?![*\w])"), r"<em>\1</em>"),
    (re.compile(r"!\[([^\]]*)\]\(([A-Za-z0-9_./-]+)\)"), r'<img alt="\1" src="\2">'),   # relative images only
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"), r'<a href="\2" rel="noopener nofollow">\1</a>'),
]


def inline(text: str) -> str:
    out = html.escape(text, quote=True).replace("\\|", "|")
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def _align(spec: str) -> str:
    left, right = spec.startswith(":"), spec.endswith(":")
    return "center" if left and right else "left" if left else "right" if right else ""


def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|") if line.strip().startswith("|") else line.strip())
    return [p.strip() for p in parts]


def render(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if m := re.match(r"^(#{1,4})\s+(.*)$", line):
            level = len(m[1])
            out.append(f"<h{level}>{inline(m[2])}</h{level}>")
            i += 1
        elif line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", lines[i + 1]):
            head = _cells(line)
            aligns = [_align(c) for c in _cells(lines[i + 1])]
            style = lambda k: f' style="text-align: {aligns[k]}"' if k < len(aligns) and aligns[k] else ""  # noqa: E731
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th{style(k)}>{inline(c)}</th>" for k, c in enumerate(head)) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td{style(k)}>{inline(c)}</td>" for k, c in enumerate(r)) + "</tr>" for r in rows)
                       + "</tbody></table>")
        elif re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(t)}</li>" for t in items) + "</ul>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|\s*[-*]\s|\s*\|)", lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)
