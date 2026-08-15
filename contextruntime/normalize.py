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

import os
import re
import shlex
from dataclasses import dataclass
from typing import List, Optional

_READ_CMDS = {"cat", "head", "tail", "less", "more", "bat", "nl"}   # file_materialization
_SEARCH_CMDS = {"grep", "egrep", "fgrep", "rg", "ripgrep", "ag", "ack"}   # search_materialization
_PATH_CMDS = {"find", "ls", "tree"}                                # path_listing
# execution: runs code/tests -- NOT a source read even though Python itself opens many files
_EXEC_CMDS = {"python", "python2", "python3", "pytest", "py.test", "tox", "make", "flake8",
              "black", "isort", "mypy", "pylint", "coverage", "pip", "pip3", "npm", "node",
              "yarn", "go", "cargo", "ruff", "nox"}
# a pipeline whose TAIL summarizes/transforms shows the model a DERIVED artifact (a count, a sort),
# not source lines -- so it is `derived`, not `search`. A source-preserving filter tail keeps `search`.
_SUMMARIZER_TAILS = {"wc", "sort", "uniq", "cut", "tr", "jq", "column", "paste", "comm", "md5",
                     "md5sum", "sha1sum", "sha256sum", "xxd", "od", "base64", "tee"}
# genuinely-complex shell we will NOT parse -> unknown. `;`/`&&`/`||` are now SPLIT into statements
# (each recognized on its own), and a leading `|` is handled per statement -- so those are OUT. Only
# subshells, command substitution, loops, xargs, eval, heredocs and process substitution bail here.
_TRULY_COMPLEX = re.compile(r"`|\$\(|<\(|<<|\bfor\b|\bwhile\b|\bdo\b|\bdone\b|\bxargs\b|\beval\b")


@dataclass
class ShellEffect:
    kind: str                          # "read" | "edit" | "execution" | "unknown"
    path: str = ""
    representation: str = "file"       # "file" | "git_blob" | "search" | "path_listing" | "derived"
    ref: Optional[str] = None          # git rev for a git_blob
    note: Optional[str] = None
    attributable: bool = True          # may the single Bash response's tokens go to THIS read?


@dataclass
class BashParse:
    """Structured result of parsing one Bash command (hook_schema 0.4.1). `effects` holds only the
    ASSERTED effects -- from statements guaranteed to execute -- so a conditional/backgrounded
    statement never manufactures a phantom read/edit."""
    effects: List[ShellEffect]
    recognized_statements: int = 0
    unknown_statements: int = 0
    has_execution: bool = False        # an asserted execution statement (a second output producer)
    has_unknown: bool = False          # a statement we could not recognize (partial coverage)
    conditional: bool = False          # some statement's effects were dropped (uncertain execution)

    def coverage(self) -> str:
        rec, unk = self.recognized_statements, self.unknown_statements
        if rec and unk:
            return "partial"
        if unk:
            return "unknown_only"
        if rec and not any(e.kind in ("read", "edit") for e in self.effects):
            return "execution_only"
        return "fully" if rec else "none"


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


def _grep_scope(rest):
    """Search scope for grep/rg/git grep: the PATTERN is not a path. With -e/-f the pattern is a flag
    value, so every non-flag operand is a path; otherwise the first non-flag operand is the pattern."""
    ops = [t for t in rest if not t.startswith("-")]
    has_pat_flag = any(t.startswith(("-e", "-f", "--regexp", "--file")) for t in rest)
    paths = ops if has_pat_flag else ops[1:]
    return paths[-1] if paths else "."


def _find_scope(rest):
    """find scope: the search ROOT(s) come BEFORE the first -expression (`find . -name '*.py'` -> .)."""
    paths = []
    for t in rest:
        if t.startswith("-"):
            break
        paths.append(t)
    return paths[-1] if paths else "."


def _list_scope(rest):
    ops = [t for t in rest if not t.startswith("-")]
    return ops[-1] if ops else "."


# stderr redirects (2>&1, 2>/dev/null, 2> file) do NOT reach the model -- stripped before recognition
# so they neither fabricate an edit nor pollute operands. STDOUT redirect stays (it IS an edit).
_STDERR_REDIR = re.compile(r"\s\d*>&\d+|\s\d+>>?\s*[^\s|;&<>]+")


def _split_statements(cmd: str):
    """Split a command line into (statement, separator_after) pairs on top-level ; && || & --
    QUOTE-AWARE (a hand scanner, NOT shlex punctuation_chars, which splits fd-redirects like `2>` and
    treats quoted separators as operators). The separator is retained so execution CERTAINTY can be
    judged (`;` sequences unconditionally; `&&`/`||`/`&` do not). `|` stays inside a statement."""
    out, cur, sep, q, i, n = [], [], None, None, 0, len(cmd)

    def flush(s):
        out.append(("".join(cur).strip(), s))

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
            flush(cmd[i:i + 2]); cur = []; i += 2
        elif c == ";":
            flush(";"); cur = []; i += 1
        elif c == "&" and cmd[i - 1:i] != ">" and not (i + 1 < n and cmd[i + 1] == ">"):
            flush("&"); cur = []; i += 1                      # background; `>&`/`&>` are redirects
        else:
            cur.append(c); i += 1
    flush(None)
    return [(s, sp) for s, sp in out if s]


def bash_parse(command: str) -> BashParse:
    """Structured recognition of a Bash command (hook_schema 0.4.1). Only statements GUARANTEED to
    execute contribute effects; a `cd DIR` updates a virtual cwd for later statements; partial
    recognition is preserved in the coverage counts; a read is `attributable` only when it is the sole
    output producer (no execution / no unknown / no other read / not conditional)."""
    cmd = (command or "").strip()
    if not cmd:
        return BashParse([])
    if _TRULY_COMPLEX.search(cmd):
        return BashParse(_unknown("complex_shell"), unknown_statements=1, has_unknown=True)
    parts = _split_statements(cmd)
    if not parts:
        return BashParse([])
    asserted, rec, unk = [], 0, 0
    has_exec = has_unknown = conditional = False
    vcwd, prev_sep, prev_cd = "", None, False
    for idx, (stmt, sep_after) in enumerate(parts):
        # a statement runs FOR CERTAIN only if it is first, or `;`-sequenced, or the `&&`-guard was a
        # (safe) cd; never when it is backgrounded or follows a background.
        certain = ((idx == 0 or prev_sep == ";" or (prev_sep == "&&" and prev_cd))
                   and prev_sep != "&" and sep_after != "&")
        head = stmt.split()
        is_cd = bool(head) and head[0] == "cd"
        effs = _stmt_effects(stmt)
        if bool(effs) and all(e.kind == "unknown" for e in effs):
            unk += 1; has_unknown = True
        elif any(e.kind != "unknown" for e in effs):
            rec += 1
        for e in effs:
            if e.kind == "unknown":
                continue
            if not certain:
                conditional = True
                continue
            if e.kind == "execution":
                has_exec = True
            if e.path and vcwd and not os.path.isabs(e.path):   # resolve against the virtual cwd
                e.path = os.path.normpath(os.path.join(vcwd, e.path))
            asserted.append(e)
        if is_cd and certain and len(head) > 1 and not head[1].startswith("-"):
            d = head[1]
            vcwd = d if os.path.isabs(d) else (os.path.normpath(os.path.join(vcwd, d)) if vcwd else d)
        prev_cd, prev_sep = (is_cd and certain), sep_after
    # token attribution: a read is a candidate for the single Bash response's tokens only if NO other
    # KIND of producer co-ran (execution / unknown / conditional). Multiple READS from one call stay
    # candidates here and are resolved to `ambiguous_multipath` downstream; a read alongside an
    # execution or unknown is `ambiguous_composite` (its slice of the mixed response can't be isolated).
    clean = not has_exec and not has_unknown and not conditional
    for e in asserted:
        if e.kind == "read":
            e.attributable = clean
    return BashParse(asserted, rec, unk, has_exec, has_unknown, conditional)


def bash_effects(command: str) -> list:
    """The asserted effects of a Bash command (thin wrapper over bash_parse)."""
    return bash_parse(command).effects


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
    # A leading pipe to a pager/filter (grep ... | head) still materializes the leading command's
    # output. A pipe FILTERS, so a piped file read is a derived subset (never a full-file read); a pipe
    # whose TAIL summarizes (| wc -l) shows a DERIVED artifact (a count), not source -> representation
    # `derived`.
    piped = "|" in toks
    leading = toks[:toks.index("|")] if piped else toks
    if not leading:
        return _unknown("empty_pipeline_head")
    tail_prog = toks[max(i for i, t in enumerate(toks) if t == "|") + 1:][:1] if piped else []
    derived = piped and bool(tail_prog) and tail_prog[0] in _SUMMARIZER_TAILS
    prog = leading[0]
    rest = leading[1:]
    args = [t for t in rest if not t.startswith("-")]           # non-flag operands

    def rd(path, natural, note=None):
        rep = "derived" if derived else ("search" if (piped and natural == "file") else natural)
        return ShellEffect("read", path, representation=rep, note=note)

    if _is_execution(leading):
        return [ShellEffect("execution", note=prog)]
    if prog in _READ_CMDS:
        ops = _file_operands(rest)
        return [rd(a, "file", note=("piped" if piped else None)) for a in ops] or _unknown("no_path")
    if prog in _SEARCH_CMDS:
        return [rd(_grep_scope(rest), "search", note=prog)]
    if prog == "git" and rest and rest[0] == "grep":
        return [rd(_grep_scope(rest[1:]), "search", note="git_grep")]
    if prog in _PATH_CMDS:
        if prog == "find" and any(t in ("-delete", "-exec", "-execdir", "-ok") for t in rest):
            return _unknown("find_mutation")   # -delete/-exec MUTATE with dynamic targets -> not a read
        scope = _find_scope(rest) if prog == "find" else _list_scope(rest)
        return [rd(scope, "path_listing", note=prog)]
    if prog == "sed":
        target = args[-1] if args else None                    # the file is the last operand
        if not target:
            return _unknown("sed_no_file")
        if any(t == "-i" or t.startswith("-i") for t in rest):
            return [ShellEffect("edit", target, note="sed_inplace")]
        if any(t == "-n" or t.startswith("-n") for t in rest):
            return [rd(target, "file", note="sed_quiet")]
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
