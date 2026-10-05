#!/usr/bin/env python3
"""Privacy sweep over tracked files (or a given list). Run before every commit:

    python3 scripts/privacy_sweep.py            # all tracked files
    python3 scripts/privacy_sweep.py --staged   # only files staged for commit

Built-in checks need no configuration:
  * a real home directory (/Users/<name>/, /home/<name>/, or the -Users-<name>- project-slug form)
    other than the /Users/<user> placeholder;
  * e-mail addresses other than noreply ones.

Names that must never appear (company, client, project, people) belong in .privacy-patterns, one
regex per line. That file is gitignored ON PURPOSE: a public list of sensitive names would leak
the very names it protects. Lines containing \\author{ are exempt (paper bylines are deliberate).
Known-public strings (e.g. a path quoted inside a public SWE-bench issue) go in
scripts/privacy_allow.txt, one regex per line, matched against "path:line-text".

Exit 0 = clean, 1 = findings (printed as path:line: match).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                      text=True).stdout.strip() or "."
HOME_PATH = re.compile(r"(?:/Users/|/home/|-Users-)(?!<user>)([A-Za-z0-9._]+)(?=[/-])")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*[A-Za-z0-9]@"
                   r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SKIP_SUFFIX = (".png", ".pdf", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".sqlite", ".zip", ".gz")
SAFE_HOME = {"Shared", "runner", "user", "you", "me", "name", "USER"}


def _lines(path):
    p = os.path.join(ROOT, path)
    return [ln.strip() for ln in open(p, encoding="utf-8")] if os.path.exists(p) else []


def _patterns(path):
    return [re.compile(x, re.I) for x in _lines(path) if x and not x.startswith("#")]


def _files(staged: bool):
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged
           else ["git", "ls-files"])
    return [f for f in subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).stdout.split()
            if not f.lower().endswith(SKIP_SUFFIX)]


def sweep(files, private, allow):
    hits = []
    for f in files:
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8").read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            found = [m.group(0) for m in HOME_PATH.finditer(line) if m.group(1) not in SAFE_HOME]
            found += [m for m in EMAIL.findall(line) if "noreply" not in m.lower()
                      and not m.lower().endswith((".py", ".json", ".md"))]
            if "\\author{" not in line:
                found += [m.group(0) for p in private for m in p.finditer(line)]
            ctx = f"{f}:{line}"
            found = [m for m in found if not any(a.search(ctx) for a in allow)]
            hits += [f"{f}:{n}: {m}" for m in found]
    return hits


def main(argv):
    private = _patterns(".privacy-patterns")
    allow = _patterns("scripts/privacy_allow.txt")
    hits = sweep(_files("--staged" in argv), private, allow)
    if not private:
        print("note: no .privacy-patterns file; only built-in checks ran", file=sys.stderr)
    print("\n".join(hits) if hits else "privacy sweep: clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
