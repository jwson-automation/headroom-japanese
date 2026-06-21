"""Line-based compressors for non-JSON tool output: logs, search results, diffs.

Mirrors headroom's content-type handlers: preserve the structurally important
lines (errors, hunk headers, the match itself) and collapse the redundant bulk
(repeated log templates, large diff context, duplicate hits). Japanese error
keywords come from lexicon_ja.

Each function returns (out_text, total_lines, kept_lines).
"""

from __future__ import annotations

import re

from .lexicon_ja import ERROR_KEYWORDS

_EN_ERR = re.compile(r"\b(ERROR|FATAL|WARN|WARNING|Exception|Traceback|FAIL(?:ED)?)\b")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NUM = re.compile(r"\b\d+\b")
_JP_ERR = [k for k in ERROR_KEYWORDS if not k.isascii()]


def _is_error(line: str) -> bool:
    if _EN_ERR.search(line):
        return True
    return any(k in line for k in _JP_ERR)


def _template(line: str) -> str:
    """Collapse volatile parts so repeated log lines share a template key."""
    t = _TS.sub("<TS>", line)
    t = _HEX.sub("<HEX>", t)
    t = _NUM.sub("<N>", t)
    return t.strip()


def compress_log(text: str, first: int = 3, last: int = 3, max_lines: int = 40):
    lines = text.splitlines()
    n = len(lines)
    if n <= first + last:
        return text, n, n

    keep: set[int] = set(range(first)) | set(range(n - last, n))
    for i, ln in enumerate(lines):
        if _is_error(ln):
            keep.add(i)

    # One representative per template; annotate repeats with their count.
    first_of: dict[str, int] = {}
    count: dict[str, int] = {}
    for i, ln in enumerate(lines):
        t = _template(ln)
        count[t] = count.get(t, 0) + 1
        first_of.setdefault(t, i)
    for t, i in first_of.items():
        keep.add(i)

    kept_sorted = sorted(keep)[:max_lines]
    out_lines = []
    for i in kept_sorted:
        ln = lines[i]
        c = count[_template(ln)]
        out_lines.append(ln + (f"  (×{c})" if c > 1 and not _is_error(ln) else ""))
    dropped = n - len(kept_sorted)
    if dropped > 0:
        out_lines.append(f"[{dropped}/{n} 行省略 (重複テンプレート集約)]")
    return "\n".join(out_lines), n, len(kept_sorted)


_SEARCH_RE = re.compile(r"^([\w./\-]+):(\d+):(.*)$")


def compress_search(text: str, per_file: int = 3, max_lines: int = 40):
    """`file:line:content` search hits — dedup identical hits, cap per file."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    n = len(lines)
    seen: set[str] = set()
    per: dict[str, int] = {}
    out_lines = []
    omitted = 0
    for ln in lines:
        if ln in seen:
            omitted += 1
            continue
        seen.add(ln)
        m = _SEARCH_RE.match(ln)
        f = m.group(1) if m else ""
        per[f] = per.get(f, 0) + 1
        if per[f] > per_file or len(out_lines) >= max_lines:
            omitted += 1
            continue
        out_lines.append(ln)
    if omitted:
        out_lines.append(f"[{omitted}/{n} 件省略 (ファイルごと上位{per_file}件)]")
    return "\n".join(out_lines), n, n - omitted


def compress_diff(text: str, context: int = 1, max_lines: int = 120):
    """Keep file/hunk headers and changed lines; trim large unchanged context."""
    lines = text.splitlines()
    n = len(lines)
    changed: set[int] = set()
    for i, ln in enumerate(lines):
        if ln[:1] in "+-" and not ln.startswith(("+++", "---")):
            changed.add(i)
    keep: set[int] = set()
    for i, ln in enumerate(lines):
        if ln.startswith(("diff ", "@@", "+++", "---", "index ")):
            keep.add(i)
        elif i in changed:
            keep.add(i)
            for d in range(1, context + 1):  # a little context around changes
                if i - d >= 0:
                    keep.add(i - d)
                if i + d < n:
                    keep.add(i + d)
    kept_sorted = sorted(keep)[:max_lines]
    out_lines = [lines[i] for i in kept_sorted]
    dropped = n - len(kept_sorted)
    if dropped > 0:
        out_lines.append(f"[{dropped}/{n} 行省略 (変更なし文脈を圧縮)]")
    return "\n".join(out_lines), n, len(kept_sorted)


_FUNC_SIG = re.compile(
    r"^(\s*)(?:export\s+|public\s+|private\s+|protected\s+|static\s+|async\s+|pub\s+)*"
    r"(?:def|function|func|fn)\b"
)


def _leading_docstring(body: list[str]):
    """Return (start, end, quote) of a leading Python docstring in a body, or None.
    `body` is the list of body lines (already indented under the signature)."""
    for j, bl in enumerate(body):
        if bl.strip() == "":
            continue
        s = bl.strip()
        if s[:3] in ('"""', "'''"):
            q = s[:3]
            if len(s) >= 6 and s[3:].rstrip().endswith(q):
                return j, j, q          # single-line docstring
            for k in range(j + 1, len(body)):
                if body[k].strip().endswith(q):
                    return j, k, q       # multi-line
            return j, len(body) - 1, q   # unterminated
        return None                      # first real line isn't a docstring
    return None


def _emit_body(body: list[str], sig_indent: int, docstring_mode: str):
    """Return (kept_lines, dropped_count) for one function body."""
    pad = " " * (sig_indent + 4)
    if docstring_mode in ("first_line", "full"):
        span = _leading_docstring(body)
        if span is not None:
            start, end, q = span
            if docstring_mode == "full" or start == end:
                kept = body[start:end + 1]
            else:  # first_line of a multi-line docstring, re-wrapped as one line
                first = body[start].strip()[3:].strip() or body[start + 1].strip()
                kept = [pad + q + first + q]
            return kept + [pad + "..."], len(body) - len(kept)
    return [pad + "..."], len(body)


def compress_code(text: str, docstring_mode: str = "remove"):
    """Keep imports / signatures / class & top-level lines; drop function bodies.

    Line-based heuristic mirroring headroom's CodeCompressor intent (which uses
    tree-sitter). A function body is the run of lines more indented than its
    signature; it collapses to a single `...`. Imports, class declarations,
    decorators, type/interface lines and module-level code stay.

    docstring_mode (headroom's DocstringMode): "remove" (default), "first_line"
    (keep the first docstring line as a one-line hint), or "full".
    """
    lines = text.splitlines()
    n = len(lines)
    out: list[str] = []
    body_dropped = 0
    i = 0
    while i < n:
        line = lines[i]
        m = _FUNC_SIG.match(line)
        if m:
            out.append(line)  # signature line
            sig_indent = len(m.group(1))
            i += 1
            body: list[str] = []
            while i < n:
                bl = lines[i]
                if bl.strip() == "" or len(bl) - len(bl.lstrip()) > sig_indent:
                    body.append(bl)
                    i += 1
                    continue
                break
            if body:
                kept_lines, dropped = _emit_body(body, sig_indent, docstring_mode)
                out.extend(kept_lines)
                body_dropped += dropped
            continue
        out.append(line)
        i += 1

    kept = n - body_dropped
    text_out = "\n".join(out)
    if body_dropped:
        text_out += f"\n# [{body_dropped}/{n} 行省略 (関数本体を圧縮)]"
    return text_out, n, kept


HANDLERS = {
    "log": compress_log,
    "search": compress_search,
    "diff": compress_diff,
    "code": compress_code,
}
