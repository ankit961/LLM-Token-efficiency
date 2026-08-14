"""Normalize captured hook events into the classify_reads() event contract (Phase 2.4-C).

Kept SEPARATE from capture so a HookJournal can be re-run through different windows/normalizers
without recapturing. Two pieces:

  bash_effects(command)  -> conservative, HIGH-PRECISION shell recognizer. Only unambiguous
                            read/mutation forms are classified; ANYTHING else is `unknown` (never
                            exploration). The labeller's content-version hash backstop catches
                            mutations we miss, so under-recognition is safe.
  to_events(rows)        -> HookJournal tool-event rows -> the flat dicts classify_reads consumes.

A read via `git show REV:PATH` is a `git_blob` representation (the historical blob, NOT the
worktree file), so a later edit of the current file correctly makes it a version conflict.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Optional

_READ_CMDS = {"cat", "head", "tail", "less", "more", "bat", "nl"}
# any of these means shell structure we will NOT parse precisely -> unknown (conservative)
_COMPLEX = re.compile(r"[|;`]|\$\(|\|\||&&|<<|<\(|\bfor\b|\bwhile\b|\bdo\b|\bxargs\b|\bfind\b|\beval\b")


@dataclass
class ShellEffect:
    kind: str                          # "read" | "edit" | "unknown"
    path: str = ""
    representation: str = "file"       # "file" | "git_blob"
    ref: Optional[str] = None          # git rev for a git_blob
    note: Optional[str] = None


def _unknown(note: str):
    return [ShellEffect("unknown", note=note)]


_VALUED_FLAGS = {"-n", "-c", "--lines", "--bytes"}   # consume the following token as their value


def _file_operands(rest) -> list:
    """Non-flag operands, skipping the value token after a value-taking flag (e.g. `-n 5`)."""
    ops, skip = [], False
    for t in rest:
        if skip:
            skip = False
            continue
        if t.startswith("-"):
            if t in _VALUED_FLAGS:
                skip = True
            continue
        ops.append(t)
    return ops


def bash_effects(command: str) -> list:
    """Recognize only unambiguous read/mutation file effects of a single shell command."""
    cmd = (command or "").strip()
    if not cmd:
        return []
    if _COMPLEX.search(cmd):
        return _unknown("complex_shell")
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return _unknown("unparseable")
    if not toks:
        return []
    # a single output redirection to a file is a mutation of that file
    for i, t in enumerate(toks):
        if t in (">", ">>") and i + 1 < len(toks):
            return [ShellEffect("edit", toks[i + 1], note="redirect")]
    prog = toks[0]
    rest = toks[1:]
    args = [t for t in rest if not t.startswith("-")]           # non-flag operands
    if prog in _READ_CMDS:
        ops = _file_operands(rest)
        return [ShellEffect("read", a) for a in ops] or _unknown("no_path")
    if prog == "sed":
        target = args[-1] if args else None                    # the file is the last operand
        if not target:
            return _unknown("sed_no_file")
        if any(t == "-i" or t.startswith("-i") for t in rest):
            return [ShellEffect("edit", target, note="sed_inplace")]
        if any(t == "-n" or t.startswith("-n") for t in rest):
            return [ShellEffect("read", target, note="sed_quiet")]
        return _unknown("sed_ambiguous")
    if prog == "tee":
        return [ShellEffect("edit", a) for a in args] or _unknown("tee_no_file")
    if prog == "cp" and len(args) >= 2:
        return [ShellEffect("read", args[0]), ShellEffect("edit", args[-1])]
    if prog == "mv" and len(args) >= 2:
        return [ShellEffect("edit", args[0], note="moved_from"), ShellEffect("edit", args[-1])]
    if prog == "rm":
        return [ShellEffect("edit", a, note="deleted") for a in args] or _unknown("rm_no_file")
    if prog == "git" and len(toks) >= 3 and toks[1] == "show":
        m = re.match(r"([^:]+):(.+)", toks[2])
        if m:
            return [ShellEffect("read", m.group(2), representation="git_blob", ref=m.group(1))]
        return _unknown("git_show_no_blob")
    return _unknown("unrecognized")


# channels for the classify_reads event contract
NATIVE_READ = "native_read"
BASH_MATERIALIZATION = "bash_materialization"


def to_events(rows) -> list:
    """Map HookJournal tool-event rows into the flat event dicts classify_reads consumes.
    Only materializations (reads) and mutations (edits) become events; `unknown`/no-path rows are
    dropped (they cannot be attributed). One row already corresponds to one path effect."""
    out = []
    for r in rows:
        kind = r["kind"]                                       # read | edit | unknown
        if kind not in ("read", "edit") or not r["path_normalized"]:
            continue
        ev = {"event_id": r["event_id"], "seq": r["seq"], "kind": kind,
              "stream_key": r["stream_key"], "path": r["path_normalized"],
              "content_version": r["content_version"], "step": r["step"],
              "batch_id": r["batch_id"]}
        if kind == "read":
            ev["channel"] = r["channel"]
            ev["version_status"] = r["version_status"]
            ev["transport_content_tokens"] = r["estimated_tokens"]
        out.append(ev)
    return out
