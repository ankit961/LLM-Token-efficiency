"""B5.2 Stage B — the scoped local discovery executor (D0/D1 patterns ONLY).

Replaces a multi-call discovery run (grep → read → read …) with ONE tool call that performs the same
mechanical steps locally and returns a single canonical, deduplicated evidence packet. Deliberately
narrow — exactly the transition classes the offline oracle validated (D0: read what the search hit;
D1: bounded expansions of accumulated evidence):

    pattern/identifier → search source files → top-k files by hits → enclosing source slices
    traceback 'path:line'  → the enclosing slice of that location
    path → the file's head/structure (or the whole file when small)
    same-dir / test-file expansion (bounded, names only unless asked)
    parameter-free listing of a directory

NO D2: no semantic planning, no LLM calls, no query rewriting. Graph/AST may be used internally only
where deterministic; this first executor uses plain lexical search + line slicing. Everything omitted
is listed as paths so the model can fall back to native tools (the fallback is itself a measurement).
"""
from __future__ import annotations

import os
import re

DEFAULT_K = 3
DEFAULT_BUDGET = 6000                 # cl100k-ish tokens per packet (approx via chars/3.2)
SLICE_BEFORE, SLICE_AFTER = 20, 30
MAX_REGIONS_PER_FILE = 3
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
_TB = re.compile(r'(?:File "([^"]+)", line (\d+))|([\w./-]+\.py):(\d+)')


def _tok_est(s: str) -> int:
    return max(1, len(s) // 3)


def _iter_source_files(root, exts=(".py",), limit=20000):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if any(fn.endswith(e) for e in exts):
                yield os.path.join(dirpath, fn)
                n += 1
                if n >= limit:
                    return


def search_pattern(root, pattern, *, k=DEFAULT_K, regex=False):
    """[(path, [(lineno, line)])] for the top-k files by hit count (ties: fewer lines first)."""
    try:
        rx = re.compile(pattern if regex else re.escape(pattern))
    except re.error:
        rx = re.compile(re.escape(pattern))
    hits = []
    for p in _iter_source_files(root):
        try:
            lines = open(p, errors="replace").read().splitlines()
        except OSError:
            continue
        fh = [(i + 1, ln) for i, ln in enumerate(lines) if rx.search(ln)]
        if fh:
            hits.append((p, fh))
    hits.sort(key=lambda x: (-len(x[1]), len(x[0])))
    return hits[:k], [p for p, _ in hits[k:]]


def slice_around(path, lineno, *, before=SLICE_BEFORE, after=SLICE_AFTER):
    """Numbered source slice around a line (1-based), clamped to the file."""
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return None
    lo = max(lineno - before, 1)
    hi = min(lineno + after, len(lines))
    body = "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(lo, hi + 1))
    return {"path": path, "start": lo, "end": hi, "text": body}


def regions_for_file(path, linenos, *, max_regions=MAX_REGIONS_PER_FILE):
    """Merge nearby hits into at most max_regions slices."""
    regions = []
    for ln in sorted(linenos):
        if regions and ln <= regions[-1]["end"]:
            continue
        r = slice_around(path, ln)
        if r:
            regions.append(r)
        if len(regions) >= max_regions:
            break
    return regions


def related_tests(root, path, *, limit=5):
    """Bounded, name-based test-file candidates for a source file (paths only)."""
    base = os.path.basename(path)
    stem = re.sub(r"\.py$", "", base)
    out = []
    for p in _iter_source_files(root):
        bn = os.path.basename(p)
        if bn in (f"test_{base}", f"{stem}_test.py") or (("test" in p.split(os.sep)[-2:][0].lower()
                                                          if len(p.split(os.sep)) > 1 else False) and stem in bn):
            out.append(p)
            if len(out) >= limit:
                break
    return out


def read_head(path, *, max_lines=120):
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return None
    body = "\n".join(f"{i + 1:6d}\t{ln}" for i, ln in enumerate(lines[:max_lines]))
    more = len(lines) - max_lines
    if more > 0:
        body += f"\n… ({more} more lines — Read {path} for the rest)"
    return {"path": path, "start": 1, "end": min(max_lines, len(lines)), "text": body}


def parse_traceback(text):
    out = []
    for m in _TB.finditer(text or ""):
        p = m.group(1) or m.group(3)
        ln = m.group(2) or m.group(4)
        if p and ln:
            out.append((p, int(ln)))
    return out


def discover(root, *, pattern=None, query=None, path=None, traceback=None,
             k=DEFAULT_K, budget_tokens=DEFAULT_BUDGET):
    """One evidence packet for one discovery intent. Deterministic; D0/D1 mechanics only."""
    root = os.path.abspath(root)
    regions, listed, note = [], [], []
    if traceback:
        for p, ln in parse_traceback(traceback)[:k]:
            fp = p if os.path.isabs(p) else os.path.join(root, p)
            r = slice_around(fp, ln)
            if r:
                regions.append(r)
    if pattern or query:
        pat = pattern or query
        top, rest = search_pattern(root, pat, k=k, regex=bool(pattern))
        for p, fh in top:
            regions.extend(regions_for_file(p, [ln for ln, _ in fh]))
            listed.extend(related_tests(root, p, limit=3))
        listed.extend(rest[:10])
        if not top:
            note.append(f"no source hits for {pat!r}")
    if path:
        fp = path if os.path.isabs(path) else os.path.join(root, path)
        if os.path.isdir(fp):
            entries = sorted(os.listdir(fp))[:60]
            regions.append({"path": fp, "start": 0, "end": 0,
                            "text": "\n".join(entries) + ("\n…" if len(os.listdir(fp)) > 60 else "")})
        else:
            r = read_head(fp)
            if r:
                regions.append(r)
            else:
                note.append(f"unreadable: {path}")
    # dedup + budget
    seen, out_regions, used = set(), [], 0
    dropped = []
    for r in regions:
        key = (r["path"], r["start"], r["end"])
        if key in seen:
            continue
        seen.add(key)
        t = _tok_est(r["text"])
        if used + t > budget_tokens:
            dropped.append(r["path"])
            continue
        used += t
        out_regions.append(r)
    parts = ["EVIDENCE PACKET (local discovery; one consolidated result)"]
    for r in out_regions:
        loc = f"{os.path.relpath(r['path'], root)}" + (f":{r['start']}-{r['end']}" if r["end"] else "")
        parts.append(f"== {loc} ==\n{r['text']}")
    extra = sorted({os.path.relpath(p, root) if p.startswith(root) else p for p in listed + dropped})
    if extra:
        parts.append("OTHER RELEVANT PATHS (not expanded — Read/Grep them natively if needed):\n" +
                     "\n".join(extra[:20]))
    if note:
        parts.append("NOTES: " + "; ".join(note))
    return {"packet": "\n\n".join(parts), "n_regions": len(out_regions),
            "est_tokens": used, "omitted_paths": extra[:20]}
