"""B1.0 — deterministic safety-foundation tests (Transparent Reduction Contract v0.1).

Covers the seven safety pieces that must hold BEFORE any live experiment is worth running:
prospective-only routing, a recoverable live CAS, fail-safe version gating, foreign-cwd
installer wiring, and the durable decision log. All zero model cost.
"""
import json

import pytest

from contextruntime import doctor
from contextruntime import install as I
from contextruntime.reducers import gate, livecas
from contextruntime.reducers import hook as hook_mod
from contextruntime.semanticfs import context_expand
from contextruntime.store import GraphStore


# --------------------------------------------------------------------- routing gate
@pytest.mark.parametrize("tool,args,reduce,rep", [
    ("Grep", {"pattern": "x"},                       True,  "search"),
    ("Glob", {"pattern": "*.py"},                    True,  "path_listing"),   # paths, not matches
    ("Bash", {"command": "grep -rn foo src/"},       True,  "search"),
    ("Bash", {"command": "rg foo"},                  True,  "search"),
    ("Bash", {"command": "find . -name '*.py'"},     True,  "path_listing"),
    ("Bash", {"command": "ls -la src/"},             True,  "path_listing"),
    # everything below MUST pass through — the safety core
    ("Read", {"file_path": "/a.py"},                 False, None),
    ("Bash", {"command": "cat src/a.py"},            False, None),   # file materialization
    ("Bash", {"command": "sed -n '1,5p' a.py"},      False, None),   # file materialization
    ("Bash", {"command": "git show HEAD:a.py"},      False, None),   # git_blob
    ("Bash", {"command": "grep foo a.py | wc -l"},   False, None),   # derived summary
    ("Bash", {"command": "pytest -q"},               False, None),   # execution
    ("Bash", {"command": "python run.py"},           False, None),   # execution
    ("Bash", {"command": "grep foo a.py && rm a.py"},False, None),   # conditional/mutation
    ("Bash", {"command": "cat a.py; grep x b.py"},   False, None),   # mixed file+search
    ("Bash", {"command": "echo hi > out.txt"},       False, None),   # redirect = edit
    ("WebFetch", {"url": "http://x"},                False, None),
    ("Bash", {"command": ""},                        False, None),
])
def test_gate_routes_only_prospective_search_listing(tool, args, reduce, rep):
    d = gate.route(tool, args)
    assert d.reduce is reduce
    if reduce:
        assert d.representation == rep


def test_gate_never_raises_on_junk():
    for bad in [(None, None), ("Bash", {"command": "$(rm -rf /)"}), ("Bash", {"command": "for x in a; do cat $x; done"})]:
        assert gate.route(*bad).passthrough      # complex/substitution → pass through, no crash


# --------------------------------------------------------------------- live CAS
def test_livecas_put_resolve_roundtrip(tmp_path):
    db = str(tmp_path / "live.db")
    raw = "\n".join(f"src/f{i}.py:{i}: match" for i in range(500))
    h = livecas.put(raw, reducer="grep", representation="search", path=db)
    assert h.startswith("result://")
    rec = livecas.resolve(h, path=db)
    assert rec.found and "match" in rec.text and rec.full_bytes == len(raw.encode())


def test_livecas_handle_matches_make_handle(tmp_path):
    from contextruntime.reducers.base import make_handle
    raw = "some grep output\n" * 40
    assert livecas.put(raw, path=str(tmp_path / "l.db")) == make_handle(raw)


def test_livecas_ttl_expiry_is_reported(tmp_path):
    db = str(tmp_path / "live.db")
    h = livecas.put("payload\n" * 100, path=db, now=1000.0)
    rec = livecas.resolve(h, path=db, now=1000.0 + livecas.TTL_SECONDS + 1)
    assert not rec.found and "expired" in rec.note


def test_livecas_evicts_oldest_over_row_cap(tmp_path, monkeypatch):
    db = str(tmp_path / "live.db")
    monkeypatch.setattr(livecas, "MAX_ROWS", 3)
    handles = [livecas.put(f"unique-content-{i}\n" * 5, path=db, now=1000.0 + i) for i in range(6)]
    # oldest evicted, newest retained
    assert not livecas.resolve(handles[0], path=db, now=1006.0).found
    assert livecas.resolve(handles[-1], path=db, now=1006.0).found


def test_livecas_truncation_flagged(tmp_path, monkeypatch):
    db = str(tmp_path / "live.db")
    monkeypatch.setattr(livecas, "MAX_SAMPLE_BYTES", 100)
    h = livecas.put("x" * 5000, path=db)
    rec = livecas.resolve(h, path=db)
    assert rec.found and rec.truncated and rec.full_bytes == 5000 and "bounded" in rec.note


# --------------------------------------------------------------------- recovery through context_expand
def test_context_expand_recovers_live_handle(tmp_path, monkeypatch):
    monkeypatch.setenv("CR_DB", str(tmp_path / "live.db"))
    raw = "\n".join(f"hit_{i}" for i in range(300))
    h = livecas.put(raw, reducer="grep", representation="search")   # default path honors CR_DB
    exp = context_expand(GraphStore(":memory:"), h)                 # graph CAS never held it
    assert exp.found and "hit_299" in exp.text


def test_context_expand_unknown_handle_guides_rerun(tmp_path, monkeypatch):
    monkeypatch.setenv("CR_DB", str(tmp_path / "live.db"))
    exp = context_expand(GraphStore(":memory:"), "result://deadbeefdeadbeef")
    assert not exp.found and "re-run" in exp.note


# --------------------------------------------------------------------- version gate
def test_version_gate_allowlist():
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    assert doctor.output_replacement_confirmed(v)
    assert not doctor.output_replacement_confirmed("9.9.9-unconfirmed")
    assert not doctor.output_replacement_confirmed(None)


# --------------------------------------------------------------------- decision log
def test_decision_log_written_on_gated_call(tmp_path, monkeypatch):
    import io
    log = str(tmp_path / "d.jsonl")
    monkeypatch.setenv("CR_DECISION_LOG", log)
    monkeypatch.setenv("CR_DB", str(tmp_path / "live.db"))
    monkeypatch.setenv("CR_REDUCE_MODE", "enforce")
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    monkeypatch.setenv("CR_CLIENT_VERSION", v)
    raw = "\n".join(f"src/f{i}.py:{i}: match" for i in range(300))
    monkeypatch.setattr("sys.stdin",
                        io.StringIO(json.dumps({"tool_name": "Grep",
                                                "tool_input": {"pattern": "match"},
                                                "tool_response": raw})))
    assert hook_mod.main() == 0
    rec = json.loads(open(log).read().splitlines()[-1])
    assert rec["reducer"] == "search" and rec["representation"] == "search"
    assert rec["enforced"] is True and rec["raw_tokens"] > rec["reduced_tokens"]
    assert rec["handle"].startswith("result://")


# --------------------------------------------------------------------- installer
def test_installer_enforces_only_on_confirmed_version(tmp_path, monkeypatch):
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    monkeypatch.setattr(I, "detect_claude",
                        lambda: {"cli": "/x/claude", "version": f"{v} (Claude Code)", "detected": True})
    rep = I.install("claude", project=str(tmp_path), with_mcp=True, with_index=False,
                    with_policy=False, enable_reduction=True)
    man = json.load(open(tmp_path / ".claude" / "contextruntime" / "install.json"))
    assert man["reduction_mode"] == "enforce" and man["reduction_confirmed_version"] == v
    # the reducer command is foreign-cwd safe and carries enforcement env
    assert "CR_REDUCE_MODE=enforce" in man["reducer_cmd"] and "PYTHONPATH=" in man["reducer_cmd"]
    assert f"CR_CLIENT_VERSION={v}" in man["reducer_cmd"]


def test_installer_fails_safe_to_observe_on_unconfirmed_version(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "detect_claude",
                        lambda: {"cli": "/x/claude", "version": "9.9.9-dev", "detected": True})
    I.install("claude", project=str(tmp_path), with_mcp=False, with_index=False,
              with_policy=False, enable_reduction=True)
    man = json.load(open(tmp_path / ".claude" / "contextruntime" / "install.json"))
    assert man["reduction_mode"] == "observe"                 # refused to enforce
    assert "CR_REDUCE_MODE" not in man["reducer_cmd"]         # observe command carries no enforce env


def test_installer_reducer_group_recognized_and_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "detect_claude",
                        lambda: {"cli": None, "version": None, "detected": False})
    I.install("claude", project=str(tmp_path), with_mcp=False, with_index=False, with_policy=False)
    sp = tmp_path / ".claude" / "settings.json"
    post = json.load(open(sp))["hooks"]["PostToolUse"]
    assert any("reducers.hook" in g["hooks"][0]["command"] for g in post)  # reducer wired
    I.uninstall("claude", project=str(tmp_path))
    s = json.load(open(sp))
    assert "PostToolUse" not in (s.get("hooks") or {})        # our groups removed cleanly


# ============================= B1.1 — budget-aware, evidence-preserving reducer ============
from contextruntime.reducers import library
from contextruntime.reducers.base import tokens


def test_reduce_search_enforces_token_budget():
    raw = "\n".join(f"src/pkg/mod{i}.py:{i}: def handler_{i}(): return None" for i in range(600))
    out = library.reduce_search(raw, {}, budget_tokens=128, representation="search")
    assert tokens(out.reduced_text) <= 128 + 8          # within budget (small join slack)
    assert out.reduced_tokens < out.raw_tokens
    assert out.handle in out.reduced_text                # recovery handle always present
    assert "more match(es)" in out.reduced_text          # truncation acknowledged


def test_reduce_search_always_preserves_diagnostics():
    raw = ("grep: src/secret.py: Permission denied\n"
           + "\n".join(f"src/a.py:{i}: match" for i in range(400))
           + "\nBinary file build/x.o matches")
    out = library.reduce_search(raw, {}, budget_tokens=64, representation="search")
    assert out.invariants_ok                             # preserved-evidence invariant held
    assert "Permission denied" in out.reduced_text       # diagnostic survived a tiny budget
    assert "Binary file build/x.o matches" in out.reduced_text


def test_reduce_search_keeps_path_and_lineno_verbatim():
    raw = "\n".join(f"src/mod{i}.py:{100+i}: hit" for i in range(300))
    out = library.reduce_search(raw, {}, budget_tokens=256, representation="search")
    assert "src/mod0.py:100:" in out.reduced_text        # exact path:lineno prefix retained
    assert "src/mod1.py:101:" in out.reduced_text


def test_reduce_search_rollup_names_top_files_on_truncation():
    raw = "\n".join(f"src/hot.py:{i}: x" for i in range(300)) + "\n" \
        + "\n".join(f"src/cold{i}.py:1: y" for i in range(5))
    out = library.reduce_search(raw, {}, budget_tokens=96, representation="search")
    assert "matches by file:" in out.reduced_text
    assert "src/hot.py×300" in out.reduced_text           # highest-hit file named with its count


def test_reduce_search_path_listing_uses_path_tail():
    raw = "\n".join(f"src/deep/dir{i}/file{i}.py" for i in range(400))
    out = library.reduce_search(raw, {}, budget_tokens=96, representation="path_listing")
    assert "more path(s)" in out.reduced_text
    assert "matches by file:" not in out.reduced_text     # listing gets the simpler tail
    assert out.handle in out.reduced_text


# ============================= B1.0.1 — merge-blocker repairs (adversarial) ================
import io
import os
import subprocess
import sys


def _grep_event(n=400):
    raw = "\n".join(f"src/f{i}.py:{i}: def handler_{i}(): pass" for i in range(n))
    return {"tool_name": "Grep", "tool_input": {"pattern": "handler"}, "tool_response": raw}


def _enforce_env(tmp_path, **extra):
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    env = {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": v,
           "CR_DB": str(tmp_path / "live.db"), "CR_DECISION_LOG": str(tmp_path / "d.jsonl")}
    env.update(extra)
    return env


def test_put_confirmed_reports_persistence_and_exactness(tmp_path):
    db = str(tmp_path / "live.db")
    s = livecas.put_confirmed("hit\n" * 200, path=db)
    assert s.persisted and s.exact and not s.truncated


def test_put_confirmed_not_exact_when_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(livecas, "MAX_SAMPLE_BYTES", 50)
    s = livecas.put_confirmed("x" * 5000, path=str(tmp_path / "live.db"))
    assert s.persisted and s.truncated and not s.exact         # stored, but NOT complete


def test_put_confirmed_fails_closed_on_write_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk on fire")
    monkeypatch.setattr(livecas, "_connect", boom)
    s = livecas.put_confirmed("data\n" * 100, path=str(tmp_path / "live.db"))
    assert not s.persisted and not s.exact                     # never claims a phantom persist


def test_hook_passes_through_when_cas_write_fails(tmp_path, monkeypatch, capsys):
    """The blocker: a swallowed CAS-write failure must NOT emit a compact response with a
    dead handle. Enforce + confirmed version, but the CAS cannot persist → pass raw through."""
    monkeypatch.setattr(livecas, "_connect", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    for k, val in _enforce_env(tmp_path).items():
        monkeypatch.setenv(k, val)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_grep_event())))
    assert hook_mod.main() == 0
    out = capsys.readouterr()
    assert out.out.strip() == "{}"                             # NOT replaced — raw passes through
    assert "could not confirm complete recovery" in out.err
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["enforced"] is False and rec["cas_persisted"] is False


def test_hook_passes_through_when_cas_would_truncate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(livecas, "MAX_SAMPLE_BYTES", 40)       # force a bounded (inexact) store
    for k, val in _enforce_env(tmp_path).items():
        monkeypatch.setenv(k, val)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_grep_event())))
    assert hook_mod.main() == 0
    out = capsys.readouterr()
    assert out.out.strip() == "{}"                             # incomplete recovery → do not reduce
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["enforced"] is False and rec["cas_exact"] is False and rec["cas_truncated"] is True


def test_put_confirmed_not_exact_when_redaction_rewrites_secret(tmp_path):
    db = str(tmp_path / "live.db")
    raw = "cfg.py:1: AKIAIOSFODNN7EXAMPLE\n" + "\n".join(f"f.py:{i}: hit" for i in range(60))
    s = livecas.put_confirmed(raw, path=db)
    assert s.persisted and s.redacted and not s.exact          # stored, but recovery != raw verbatim
    rec = livecas.resolve(s.handle, path=db)
    assert "AKIA" not in rec.text                               # secret scrubbed at rest


def test_hook_passes_through_when_recovery_would_be_redacted(tmp_path, monkeypatch, capsys):
    """Enforce + confirmed version, but the payload holds a secret redaction rewrites → recovery
    would not be verbatim → the '[+ full output]' claim would be false → pass the raw through."""
    for k, val in _enforce_env(tmp_path).items():
        monkeypatch.setenv(k, val)
    raw = ("src/x.py:1: export GITHUB_TOKEN=ghp_" + "a" * 36 + "\n"
           + "\n".join(f"src/f{i}.py:{i}: handler_{i}" for i in range(400)))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "tool_response": raw})))
    assert hook_mod.main() == 0
    assert capsys.readouterr().out.strip() == "{}"             # NOT replaced
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["cas_redacted"] is True and rec["cas_exact"] is False and rec["enforced"] is False


def test_put_confirmed_cap_is_byte_accurate(tmp_path, monkeypatch):
    monkeypatch.setattr(livecas, "MAX_SAMPLE_BYTES", 100)
    raw = "€" * 200                                            # 3 bytes each → 600 bytes, 200 chars
    s = livecas.put_confirmed(raw, path=str(tmp_path / "live.db"))
    assert s.truncated and s.stored_bytes <= 100 and not s.exact   # capped in BYTES, not chars


def test_hook_passes_through_unknown_response_shape(tmp_path, monkeypatch, capsys):
    for k, val in _enforce_env(tmp_path).items():
        monkeypatch.setenv(k, val)
    # a content-block LIST is not a supported shape — must never be json.dumps'd and substituted
    resp = {"content": [{"type": "text", "text": "src/f.py:1: hit\n" * 400}]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "tool_response": resp})))
    assert hook_mod.main() == 0
    assert capsys.readouterr().out.strip() == "{}"             # unknown schema → pass through


def test_all_launch_commands_carry_pythonpath(tmp_path):
    scope = I.resolve_scope("claude", str(tmp_path), False)
    assert I.default_crhook_cmd(scope).startswith("PYTHONPATH=")
    assert I.default_crpolicy_cmd(scope).startswith("PYTHONPATH=")
    assert "PYTHONPATH=" in I.default_reducer_cmd(scope)
    assert I.mcp_entry(scope)["env"]["PYTHONPATH"] == I._pkg_root()


def test_foreign_cwd_package_imports_with_pythonpath():
    """From an unrelated cwd (/), the package resolves ONLY because PYTHONPATH is set — the
    exact launch condition the MCP server and hooks face in a source checkout."""
    r = subprocess.run(
        [sys.executable, "-c", "import contextruntime.reducers.hook, contextruntime.mcp"],
        cwd="/", env={**os.environ, "PYTHONPATH": I._pkg_root()},
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_glob_uses_path_listing_formatting():
    from contextruntime.reducers import library
    d = gate.route("Glob", {"pattern": "*.py"})
    assert d.representation == "path_listing"
    raw = "\n".join(f"src/dir{i}/file{i}.py" for i in range(300))
    out = library.reduce_search(raw, {}, budget_tokens=96, representation=d.representation)
    assert "more path(s)" in out.reduced_text and "matches by file:" not in out.reduced_text


def test_reduce_search_no_truncation_keeps_everything():
    raw = "\n".join(f"a.py:{i}: m" for i in range(5))
    out = library.reduce_search(raw, {}, budget_tokens=256, representation="search")
    assert "more match" not in out.reduced_text           # nothing dropped
    for i in range(5):
        assert f"a.py:{i}: m" in out.reduced_text


def test_hook_respects_budget_env_and_handle_recovers(tmp_path, monkeypatch, capsys):
    import io
    db = str(tmp_path / "live.db")
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    for k, val in {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": v, "CR_DB": db,
                   "CR_DECISION_LOG": str(tmp_path / "d.jsonl"), "CR_REDUCE_BUDGET": "80"}.items():
        monkeypatch.setenv(k, val)
    raw = "\n".join(f"src/f{i}.py:{i}: def handler_{i}(): pass" for i in range(500))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Grep", "tool_input": {"pattern": "handler"}, "tool_response": raw})))
    assert hook_mod.main() == 0
    out = json.loads(capsys.readouterr().out)
    stdout = out["hookSpecificOutput"]["updatedToolOutput"]
    assert tokens(stdout) <= 80 + 8                        # budget honored end-to-end
    h = stdout.splitlines()[-1].split("result://")[1].strip(" []")
    assert livecas.resolve(f"result://{h}", path=db).found  # and the handle recovers
