"""Normalize captured hook events into the classify_reads() event contract (Phase 2.4-C).

Kept SEPARATE from capture so a HookJournal can be re-run through different windows/normalizers
without recapturing. Two pieces:

  bash_effects(command)  -> shell recognizer with REPRESENTATION TYPES (hook_schema 0.4.0):
                            file_materialization (cat/head/tail/sed -n), git_blob (git show),
                            search (grep/rg/git grep), path_listing (find/ls). `execution`
                            (tests/python/build/lint) is recognized but is NOT a read -- Python
                            opening files internally is not model-visible source context. Genuinely-
                            complex shell stays `unknown`. Search/listing materialize CONTEXT (their
                            model-visible output), so they ARE reads even though they aren't a single
                            file; their token weight is what matters, and they classify as exploration
                            (a search scope is rarely the exact path later edited).
  to_events(rows)        -> HookJournal tool-event rows -> the flat dicts classify_reads consumes.

A read via `git show REV:PATH` is a `git_blob` representation (the historical blob, NOT the
worktree file), so a later edit of the current file correctly makes it a version conflict.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Optional

_READ_CMDS = {"cat", "head", "tail", "less", "more", "bat", "nl"}   # file_materialization
_SEARCH_CMDS = {"grep", "egrep", "fgrep", "rg", "ripgrep", "ag", "ack"}   # search_materialization
_PATH_CMDS = {"find", "ls", "tree"}                                # path_listing
# execution: runs code/tests -- NOT a source read even though Python itself opens many files
_EXEC_CMDS = {"python", "python2", "python3", "pytest", "py.test", "tox", "make", "flake8",
              "black", "isort", "mypy", "pylint", "coverage", "pip", "pip3", "npm", "node",
              "yarn", "go", "cargo", "ruff", "nox"}
# genuinely-complex shell we will NOT parse -> unknown. `;`/`&&`/`||` are now SPLIT into statements
# (each recognized on its own), and a leading `|` is handled per statement -- so those are OUT. Only
# subshells, command substitution, loops, xargs, eval, heredocs and process substitution bail here.
_TRULY_COMPLEX = re.compile(r"`|\$\(|<\(|<<|\bfor\b|\bwhile\b|\bdo\b|\bdone\b|\bxargs\b|\beval\b")


@dataclass
class ShellEffect:
    kind: str                          # "read" | "edit" | "execution" | "unknown"
    path: str = ""
    representation: str = "file"       # "file" | "git_blob" | "search" | "path_listing"
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


def _is_execution(leading) -> bool:
    # ONLY the program (leading[0]) decides execution -- never a file OPERAND, else `cat manage.py`
    # or `git show HEAD:runtests.py` would be mis-flagged execution and the read dropped.
    prog = leading[0]
    return prog in _EXEC_CMDS or prog.rstrip("/").endswith(("manage.py", "runtests.py")) \
        or prog.endswith(".py")


def _scope(ops, default="."):
    """A representative path/scope for a search or listing (not a precise per-file identity;
    search/listing materialize CONTEXT, not one file -- the token capture is what matters)."""
    return ops[-1] if ops else default


# stderr redirects (2>&1, 2>/dev/null, 2> file) do NOT reach the model -- stripped before recognition
# so they neither fabricate an edit nor pollute operands. STDOUT redirect stays (it IS an edit).
_STDERR_REDIR = re.compile(r"\s\d*>&\d+|\s\d+>>?\s*[^\s|;&<>]+")


def _split_statements(cmd: str):
    """Split a command line on top-level ; && || & into statements -- QUOTE-AWARE (a hand scanner,
    NOT shlex punctuation_chars, which splits fd-redirects like `2>` and treats quoted separators as
    operators). Returns raw statement strings; `|` is kept inside a statement (leading-pipe)."""
    out, cur, q, i, n = [], [], None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if q:
            cur.append(c)
            if c == q:
                q = None
            i += 1
        elif c in ("'", '"'):
            q = c; cur.append(c); i += 1
        elif cmd[i:i + 2] in ("&&", "||"):
            out.append("".join(cur)); cur = []; i += 2
        elif c == ";":
            out.append("".join(cur)); cur = []; i += 1
        elif c == "&" and cmd[i - 1:i] != ">" and not (i + 1 < n and cmd[i + 1] == ">"):
            out.append("".join(cur)); cur = []; i += 1        # background; `>&` and `&>` are redirects
        else:
            cur.append(c); i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def bash_effects(command: str) -> list:
    """Recognize read/mutation/execution effects of a shell command (hook_schema 0.4.0).

    Representations: file_materialization (cat/head/tail/sed -n), git_blob (git show), search
    (grep/rg/git grep), path_listing (find/ls). `execution` (tests/python/build/lint) is recognized
    but is NOT a read. Compound commands are SPLIT on ;/&&/|| and each statement recognized (so
    `cd t && grep x f` yields the grep read); a `cd` statement is a no-op. Genuinely-complex shell
    (subshells, loops, xargs, eval) is `unknown`."""
    cmd = (command or "").strip()
    if not cmd:
        return []
    if _TRULY_COMPLEX.search(cmd):
        return _unknown("complex_shell")
    stmts = _split_statements(cmd)
    if stmts is None:
        return _unknown("unparseable")
    if not stmts:
        return []
    effs = []
    for stmt in stmts:
        effs.extend(_stmt_effects(stmt))
    # a compound is unknown only if EVERY statement was unknown (all noise); otherwise keep the
    # recognized read/edit/execution effects and drop the unknown noise around them.
    recognized = [e for e in effs if e.kind != "unknown"]
    return recognized or (effs and [_unknown("unrecognized")[0]]) or []


def _has_unquoted_gt(s: str) -> bool:
    """True if `>` appears OUTSIDE quotes -- so a quoted grep pattern `'>'` is not read as a redirect."""
    q = None
    for c in s:
        if q:
            q = None if c == q else q
        elif c in ("'", '"'):
            q = c
        elif c == ">":
            return True
    return False


def _stmt_effects(stmt: str) -> list:
    """Effects of ONE shell statement (raw string; may contain a leading `|` and redirects)."""
    stmt = _STDERR_REDIR.sub("", " " + stmt).strip()           # drop stderr redirects (not model-visible)
    try:
        toks = shlex.split(stmt)                                # plain shlex: `2>/dev/null` stays one token
    except ValueError:
        return _unknown("unparseable")
    if not toks:
        return []
    # STDOUT redirection sends the model-visible output to a file -- an edit of that file, never a read.
    # Gated on an UNQUOTED `>` so a quoted pattern (grep '>' f) is not mistaken for a redirect.
    if _has_unquoted_gt(stmt):
        for i, t in enumerate(toks):
            if t in (">", ">>", "1>", "1>>", "&>", "&>>") and i + 1 < len(toks):
                return [ShellEffect("edit", toks[i + 1].strip("'\""), note="redirect")]
    if toks[0] == "cd":
        return []                                              # navigation only -- no materialization
    # a single leading pipe to a pager/filter (grep ... | head) still materializes the leading
    # command's output to the model -- analyze the FIRST pipeline segment. A pipe FILTERS the output,
    # so a piped file read is a derived subset, never a full-file read (no content_version claim).
    piped = "|" in toks
    leading = toks[:toks.index("|")] if piped else toks
    if not leading:
        return _unknown("empty_pipeline_head")
    prog = leading[0]
    rest = leading[1:]
    args = [t for t in rest if not t.startswith("-")]           # non-flag operands
    if _is_execution(leading):
        return [ShellEffect("execution", note=prog)]
    if prog in _READ_CMDS:
        ops = _file_operands(rest)
        rep = "search" if piped else "file"                    # piped cat = filtered subset, not full file
        return [ShellEffect("read", a, representation=rep, note=("piped" if piped else None))
                for a in ops] or _unknown("no_path")
    if prog in _SEARCH_CMDS:
        return [ShellEffect("read", _scope(args[1:]), representation="search", note=prog)]  # args[0]=pattern
    if prog == "git" and rest and rest[0] == "grep":
        return [ShellEffect("read", _scope(args[1:]), representation="search", note="git_grep")]
    if prog in _PATH_CMDS:
        if prog == "find" and any(t in ("-delete", "-exec", "-execdir", "-ok") for t in rest):
            return _unknown("find_mutation")   # -delete/-exec MUTATE with dynamic targets -> not a read
        return [ShellEffect("read", _scope(args), representation="path_listing", note=prog)]
    if prog == "sed":
        target = args[-1] if args else None                    # the file is the last operand
        if not target:
            return _unknown("sed_no_file")
        if any(t == "-i" or t.startswith("-i") for t in rest):
            return [ShellEffect("edit", target, note="sed_inplace")]
        if any(t == "-n" or t.startswith("-n") for t in rest):
            return [ShellEffect("read", target, representation=("search" if piped else "file"),
                                note="sed_quiet")]
        return _unknown("sed_ambiguous")
    if prog == "tee":
        return [ShellEffect("edit", a) for a in args] or _unknown("tee_no_file")
    if prog == "cp" and len(args) >= 2:
        # cp consumes the source's filesystem bytes but does NOT present them to the model, so it
        # is a destination mutation only -- not a model-visible read (kind=read means materialized).
        return [ShellEffect("edit", args[-1])]
    if prog == "mv" and len(args) >= 2:
        return [ShellEffect("edit", args[0], note="moved_from"), ShellEffect("edit", args[-1])]
    if prog == "rm":
        return [ShellEffect("edit", a, note="deleted") for a in args] or _unknown("rm_no_file")
    if prog == "git" and len(leading) >= 3 and leading[1] == "show":
        m = re.match(r"([^:]+):(.+)", leading[2])
        if m:
            return [ShellEffect("read", m.group(2), representation="git_blob", ref=m.group(1))]
        return _unknown("git_show_no_blob")
    return _unknown("unrecognized")


# channels for the classify_reads event contract
NATIVE_READ = "native_read"
BASH_MATERIALIZATION = "bash_materialization"


def to_events(rows) -> list:
    """Map HookJournal tool-event rows into the flat event dicts classify_reads consumes. Only
    materializations (reads) and mutations (edits) become events; `unknown`/no-path rows are
    dropped. A FAILED read materialized nothing to the model, so it is not emitted (it remains a
    diagnostic row); a failed mutation that changed bytes is still a mutation boundary. The
    NORMALIZED path is the classification identity, so a Bash `src/a.py` and a native `/repo/src/a.py`
    resolve to the same file. Per-read token weight uses the (unambiguously attributed) model-visible
    token count."""
    out = []
    for r in rows:
        kind = r["kind"]                                       # read | edit | unknown
        if kind not in ("read", "edit") or not r["path_normalized"]:
            continue
        if kind == "read" and not r["success"]:
            continue                                           # failed read -> no materialization
        ev = {"event_id": r["event_id"], "seq": r["seq"], "kind": kind,
              "stream_key": r["stream_key"], "path": r["path_normalized"],
              "content_version": r["content_version"], "step": r["step"],
              "batch_id": r["batch_id"]}
        if kind == "read":
            ev["channel"] = r["channel"]
            ev["representation"] = r["representation"]         # search/path_listing vs file/git_blob
            ev["version_status"] = r["version_status"]
            ev["transport_content_tokens"] = r["model_visible_tokens"]
        else:
            ev["mutation_status"] = r["mutation_status"]     # verified_change | unverified
        out.append(ev)
    return out
