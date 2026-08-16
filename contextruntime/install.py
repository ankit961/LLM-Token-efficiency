"""`contextruntime install claude` — advisory / fail-open developer-preview installer.

Wires the FROZEN 0.4.1 observation layer into a Claude Code project:
  * a local state dir (``.claude/contextruntime/``) holding the HookJournal, the code-graph,
    and an ``install.json`` manifest;
  * the 7-event ``cr-hook`` block merged (idempotently, with a backup) into ``settings.json``;
  * (optional) the observe-only SemanticFS read surface registered as an MCP server in
    ``.mcp.json``;
  * an initial code-graph index of the repo;
  * a ``doctor`` verify pass over the wiring.

Design invariants (developer preview):
  - ADVISORY / OBSERVE-ONLY. It registers the fail-open journal hook and a read-only MCP; it
    never enables output rewriting. The installed posture cannot block or alter a tool call.
  - IDEMPOTENT. Re-running replaces *our* entries in place — never duplicates, never touches
    hook groups that aren't ours (identified by the ``cr-hook`` token in their command).
  - REVERSIBLE. ``uninstall`` removes exactly what we added; the journal db survives unless
    ``--purge``.
  - CONTAINED. Project scope by default (``.claude/`` inside the repo); ``--global`` targets
    ``~/.claude``.
  - SAFE WRITES. ``settings.json`` is copied to ``settings.json.crbak`` before each modification;
    ``--dry-run`` prints the plan and touches nothing.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

HOOK_SCHEMA = "0.4.1"
RUNTIME_TAG = "obs-runtime-3a-v2.1"
MODE = "advisory"  # observe-only; no output rewriting is ever enabled by the installer

# (event_name, emit_matcher) — mirrors docs/hooks.settings.example.json exactly.
HOOK_EVENTS = [
    ("SessionStart", False),
    ("SubagentStart", True),
    ("UserPromptSubmit", False),
    ("PreToolUse", True),
    ("PostToolUse", True),
    ("PostToolUseFailure", True),
    ("PostToolBatch", False),
]

CR_TOKENS = ("cr-hook", "cr-policy")  # how we recognize our own hook groups on re-install / uninstall


# --------------------------------------------------------------------------- paths / detection
@dataclass
class Scope:
    name: str            # "project" | "global"
    root: str            # the .claude directory
    settings_path: str
    mcp_path: str | None  # project scope only (Claude Code project MCP lives in <repo>/.mcp.json)
    state_dir: str
    journal_db: str
    codegraph_db: str
    config_path: str
    repo_path: str        # the code root to index / observe
    repo_id: str


def resolve_scope(client: str, project: str | None, use_global: bool) -> Scope:
    if client != "claude":
        raise ValueError(f"unsupported client {client!r} (only 'claude' in developer preview)")
    if use_global:
        root = os.path.expanduser("~/.claude")
        repo_path = os.path.abspath(project or os.getcwd())
        mcp_path = None
    else:
        repo_path = os.path.abspath(project or os.getcwd())
        root = os.path.join(repo_path, ".claude")
        mcp_path = os.path.join(repo_path, ".mcp.json")
    state = os.path.join(root, "contextruntime")
    return Scope(
        name="global" if use_global else "project",
        root=root,
        settings_path=os.path.join(root, "settings.json"),
        mcp_path=mcp_path,
        state_dir=state,
        journal_db=os.path.join(state, "hookjournal.db"),
        codegraph_db=os.path.join(state, "codegraph.db"),
        config_path=os.path.join(state, "install.json"),
        repo_path=repo_path,
        repo_id=os.path.basename(repo_path.rstrip("/")) or "repo",
    )


def detect_claude() -> dict:
    """Fail-soft probe of the Claude Code CLI. Never raises."""
    cli = shutil.which("claude")
    version = None
    if cli:
        try:
            out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=10)
            version = (out.stdout or out.stderr).strip() or None
        except Exception:  # noqa: BLE001 — detection must never break the install
            version = None
    return {"cli": cli, "version": version, "detected": cli is not None}


def cli_argv() -> list[str]:
    """A resolvable invocation of this CLI: the installed console script if it's on PATH,
    else ``{python} -m contextruntime.cli`` (dev / editable checkout). Either form runs the
    same code, so the hook resolves whether or not the entry point was installed."""
    console = shutil.which("contextruntime")
    if console:
        return [console]
    return [sys.executable, "-m", "contextruntime.cli"]


def default_crhook_cmd(scope: Scope) -> str:
    argv = cli_argv() + ["cr-hook", "--db", scope.journal_db]
    return " ".join(shlex.quote(a) for a in argv)


def default_crpolicy_cmd(scope: Scope) -> str:
    argv = cli_argv() + ["cr-policy", "--graph", scope.codegraph_db, "--repo", scope.repo_id]
    return " ".join(shlex.quote(a) for a in argv)


# --------------------------------------------------------------------------- settings merge
def _group(command: str, matcher: bool) -> dict:
    g: dict = {}
    if matcher:
        g["matcher"] = ""
    g["hooks"] = [{"type": "command", "command": command}]
    return g


def _is_cr_group(group: dict) -> bool:
    """True if this hook group is one we own (any command references a cr- entrypoint)."""
    for h in group.get("hooks", []) or []:
        cmd = str(h.get("command", ""))
        if any(t in cmd for t in CR_TOKENS):
            return True
    return False


def build_hook_block(crhook_cmd: str) -> dict:
    return {ev: [_group(crhook_cmd, matcher)] for ev, matcher in HOOK_EVENTS}


def merge_hooks(settings: dict, crhook_cmd: str, crpolicy_cmd: str | None = None) -> dict:
    """Idempotently add our 7-event cr-hook block (+ the cr-policy SessionStart brief when given),
    replacing any prior cr-hook/cr-policy groups in place."""
    settings = dict(settings)
    hooks = dict(settings.get("hooks") or {})
    for ev, matcher in HOOK_EVENTS:
        existing = [g for g in (hooks.get(ev) or []) if not _is_cr_group(g)]
        groups = [_group(crhook_cmd, matcher)]
        if crpolicy_cmd and ev == "SessionStart":
            groups.append(_group(crpolicy_cmd, False))   # advisory brief, SessionStart carries no matcher
        hooks[ev] = existing + groups
    settings["hooks"] = hooks
    return settings


def strip_hooks(settings: dict) -> dict:
    """Remove exactly our cr-hook groups; drop events left empty of anything but ours."""
    settings = dict(settings)
    hooks = dict(settings.get("hooks") or {})
    for ev in list(hooks.keys()):
        kept = [g for g in (hooks.get(ev) or []) if not _is_cr_group(g)]
        if kept:
            hooks[ev] = kept
        else:
            hooks.pop(ev, None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def hooks_wired(settings: dict) -> list[str]:
    """Which of our 7 events currently carry a cr-hook group."""
    hooks = settings.get("hooks") or {}
    return [ev for ev, _ in HOOK_EVENTS if any(_is_cr_group(g) for g in (hooks.get(ev) or []))]


def policy_wired(settings: dict) -> bool:
    """True if the cr-policy advisory brief is registered on SessionStart."""
    hooks = settings.get("hooks") or {}
    for g in (hooks.get("SessionStart") or []):
        if any("cr-policy" in str(h.get("command", "")) for h in (g.get("hooks", []) or [])):
            return True
    return False


# --------------------------------------------------------------------------- mcp merge
def mcp_entry(scope: Scope) -> dict:
    argv = cli_argv()
    return {
        "command": argv[0],
        "args": argv[1:] + ["mcp", "--db", scope.codegraph_db, "--repo", scope.repo_id],
    }


def merge_mcp(mcp: dict, scope: Scope) -> dict:
    mcp = dict(mcp)
    servers = dict(mcp.get("mcpServers") or {})
    servers["contextruntime"] = mcp_entry(scope)
    mcp["mcpServers"] = servers
    return mcp


def strip_mcp(mcp: dict) -> dict:
    mcp = dict(mcp)
    servers = dict(mcp.get("mcpServers") or {})
    servers.pop("contextruntime", None)
    if servers:
        mcp["mcpServers"] = servers
    else:
        mcp.pop("mcpServers", None)
    return mcp


# --------------------------------------------------------------------------- io helpers
def _read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt file must not crash; caller decides
        raise ValueError(f"{path} is not valid JSON — refusing to overwrite it")


def _write_json(path: str, data: dict, *, backup: bool) -> str | None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bak = None
    if backup and os.path.exists(path):
        bak = path + ".crbak"
        shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return bak


# --------------------------------------------------------------------------- plan / execute
@dataclass
class Step:
    name: str
    detail: str
    changed: bool = False
    ok: bool = True
    note: str = ""


@dataclass
class Report:
    action: str
    scope: str
    dry_run: bool
    steps: list[Step] = field(default_factory=list)
    verify: dict = field(default_factory=dict)

    def add(self, s: Step) -> None:
        self.steps.append(s)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


def install(client: str, *, project: str | None = None, use_global: bool = False,
            with_mcp: bool = True, with_index: bool = True, with_policy: bool = True,
            crhook_cmd: str | None = None, dry_run: bool = False, force: bool = False) -> Report:
    scope = resolve_scope(client, project, use_global)
    crhook_cmd = crhook_cmd or default_crhook_cmd(scope)
    crpolicy_cmd = default_crpolicy_cmd(scope) if with_policy else None
    rep = Report(action="install", scope=scope.name, dry_run=dry_run)

    det = detect_claude()
    rep.add(Step("detect-claude-code",
                 f"claude {det['version'] or '(version unknown)'} at {det['cli'] or 'NOT ON PATH'}",
                 changed=False, ok=True,
                 note="" if det["detected"] else "claude CLI not found — hook installs but stays dormant"))

    # 1) state dir
    rep.add(Step("state-dir", scope.state_dir, changed=not os.path.isdir(scope.state_dir)))
    if not dry_run:
        os.makedirs(scope.state_dir, exist_ok=True)

    # 2) cr-hook block -> settings.json
    try:
        settings = _read_json(scope.settings_path)
    except ValueError as e:
        rep.add(Step("register-cr-hook", scope.settings_path, changed=False, ok=False, note=str(e)))
        settings = None
    if settings is not None:
        merged = merge_hooks(settings, crhook_cmd, crpolicy_cmd)
        changed = merged != settings
        note = "7 events; idempotent; backup -> settings.json.crbak"
        if crpolicy_cmd:
            note += "; + cr-policy advisory brief on SessionStart"
        rep.add(Step("register-cr-hook",
                     f"{scope.settings_path}  ({crhook_cmd})",
                     changed=changed, note=note if changed else "already wired"))
        if not dry_run and changed:
            _write_json(scope.settings_path, merged, backup=True)

    # 3) mcp -> .mcp.json (project scope only)
    if with_mcp and scope.mcp_path:
        try:
            mcp = _read_json(scope.mcp_path)
            merged_mcp = merge_mcp(mcp, scope)
            changed = merged_mcp != mcp
            rep.add(Step("register-cr-mcp", scope.mcp_path, changed=changed,
                         note="observe-only SemanticFS read surface"))
            if not dry_run and changed:
                _write_json(scope.mcp_path, merged_mcp, backup=True)
        except ValueError as e:
            rep.add(Step("register-cr-mcp", scope.mcp_path, changed=False, ok=False, note=str(e)))
    elif with_mcp:
        rep.add(Step("register-cr-mcp", "(skipped — global scope has no project .mcp.json)",
                     changed=False, note="run install in a repo for the read-surface MCP"))

    # 4) index the repo
    if with_index:
        detail = f"index-code {scope.repo_path} -> {scope.codegraph_db}"
        if dry_run:
            rep.add(Step("index-repo", detail, changed=True, note="(dry-run: not executed)"))
        else:
            ok, note = _run_index(scope)
            rep.add(Step("index-repo", detail, changed=ok, ok=ok, note=note))

    # 5) write install manifest
    manifest = {
        "client": client, "scope": scope.name, "mode": MODE,
        "hook_schema": HOOK_SCHEMA, "runtime_tag": RUNTIME_TAG,
        "journal_db": scope.journal_db, "codegraph_db": scope.codegraph_db,
        "repo_path": scope.repo_path, "repo_id": scope.repo_id,
        "settings_path": scope.settings_path, "mcp_path": scope.mcp_path,
        "crhook_cmd": crhook_cmd, "crpolicy_cmd": crpolicy_cmd,
        "with_mcp": with_mcp, "with_index": with_index, "with_policy": with_policy,
    }
    rep.add(Step("write-manifest", scope.config_path, changed=True))
    if not dry_run:
        _write_json(scope.config_path, manifest, backup=False)

    # 6) verify (doctor)
    rep.verify = verify_install(scope, det)
    return rep


def _run_index(scope: Scope) -> tuple[bool, str]:
    try:
        r = subprocess.run(cli_argv() + ["index-code", scope.repo_path, "--db", scope.codegraph_db,
                                         "--repo", scope.repo_id],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode == 0:
            return True, (r.stdout.strip().splitlines() or ["indexed"])[-1]
        return False, (r.stderr.strip() or "index-code failed")[:200]
    except Exception as e:  # noqa: BLE001 — indexing is best-effort; the observation layer works without it
        return False, f"{type(e).__name__}: {e}"[:200]


def uninstall(client: str, *, project: str | None = None, use_global: bool = False,
              purge: bool = False, dry_run: bool = False) -> Report:
    scope = resolve_scope(client, project, use_global)
    rep = Report(action="uninstall", scope=scope.name, dry_run=dry_run)

    try:
        settings = _read_json(scope.settings_path)
        stripped = strip_hooks(settings)
        changed = stripped != settings
        rep.add(Step("remove-cr-hook", scope.settings_path, changed=changed,
                     note="removed our cr-hook groups" if changed else "nothing to remove"))
        if not dry_run and changed:
            _write_json(scope.settings_path, stripped, backup=True)
    except ValueError as e:
        rep.add(Step("remove-cr-hook", scope.settings_path, changed=False, ok=False, note=str(e)))

    if scope.mcp_path and os.path.exists(scope.mcp_path):
        try:
            mcp = _read_json(scope.mcp_path)
            stripped = strip_mcp(mcp)
            changed = stripped != mcp
            rep.add(Step("remove-cr-mcp", scope.mcp_path, changed=changed))
            if not dry_run and changed:
                _write_json(scope.mcp_path, stripped, backup=True)
        except ValueError as e:
            rep.add(Step("remove-cr-mcp", scope.mcp_path, changed=False, ok=False, note=str(e)))

    if purge:
        rep.add(Step("purge-state", scope.state_dir, changed=os.path.isdir(scope.state_dir),
                     note="removed journal + code-graph + manifest"))
        if not dry_run and os.path.isdir(scope.state_dir):
            shutil.rmtree(scope.state_dir, ignore_errors=True)
    else:
        rep.add(Step("preserve-state", scope.state_dir, changed=False,
                     note="journal + code-graph kept (use --purge to delete)"))
    return rep


# --------------------------------------------------------------------------- doctor / verify
def verify_install(scope: Scope, det: dict | None = None) -> dict:
    det = det or detect_claude()
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    chk("claude-code-detected", det["detected"],
        det["version"] or (det["cli"] or "not on PATH"))
    argv = cli_argv()
    chk("cli-invocation-resolvable", True,
        argv[0] if len(argv) == 1 else " ".join(argv) + "  (editable checkout — no console script)")

    settings_ok, wired = False, []
    try:
        s = _read_json(scope.settings_path)
        settings_ok = True
        wired = hooks_wired(s)
    except ValueError as e:
        chk("settings-parses", False, str(e))
    if settings_ok:
        chk("settings-parses", True, scope.settings_path)
        chk("cr-hook-wired", len(wired) == len(HOOK_EVENTS),
            f"{len(wired)}/{len(HOOK_EVENTS)} events: {','.join(wired) or 'none'}")
        chk("cr-policy-wired", policy_wired(s),
            "SessionStart advisory brief" if policy_wired(s) else "not wired (advisory steering off)")

    parent = os.path.dirname(scope.journal_db)
    chk("journal-dir-writable", os.path.isdir(parent) and os.access(parent, os.W_OK),
        parent if os.path.isdir(parent) else "(missing)")

    if scope.mcp_path:
        mcp_ok = False
        try:
            m = _read_json(scope.mcp_path)
            mcp_ok = "contextruntime" in (m.get("mcpServers") or {})
        except ValueError:
            mcp_ok = False
        chk("cr-mcp-registered", mcp_ok, scope.mcp_path)
        chk("code-graph-indexed", os.path.exists(scope.codegraph_db),
            scope.codegraph_db if os.path.exists(scope.codegraph_db) else "(not indexed)")

    return {
        "scope": scope.name,
        "mode": MODE,
        "hook_schema": HOOK_SCHEMA,
        "runtime_tag": RUNTIME_TAG,
        "healthy": all(c["ok"] for c in checks
                       if c["check"] not in ("code-graph-indexed", "cr-policy-wired")),
        "checks": checks,
    }


# --------------------------------------------------------------------------- rendering
def format_report(rep: Report) -> str:
    head = f"contextruntime {rep.action} claude  [scope={rep.scope}, mode={MODE}"
    head += ", DRY-RUN]" if rep.dry_run else "]"
    lines = [head, ""]
    for s in rep.steps:
        mark = "✗" if not s.ok else ("~" if s.changed else "·")
        line = f"  {mark} {s.name:20s} {s.detail}"
        lines.append(line)
        if s.note:
            lines.append(f"      {s.note}")
    if rep.verify:
        v = rep.verify
        lines += ["", f"  doctor: {'HEALTHY' if v['healthy'] else 'DEGRADED'} "
                      f"(hook_schema {v['hook_schema']}, {v['runtime_tag']})"]
        for c in v["checks"]:
            m = "✓" if c["ok"] else "✗"
            lines.append(f"    {m} {c['check']:22s} {c['detail']}")
    lines += ["",
              "  advisory / observe-only: the journal hook is fail-open and never blocks or "
              "rewrites a tool call."]
    return "\n".join(lines)
