"""`contextruntime install claude` — advisory/fail-open developer-preview installer.

The load-bearing safety properties for something that edits a user's real settings.json:
  idempotent (no duplicate hook groups), preserving (never clobber the user's own hooks),
  round-trippable (uninstall restores pre-install state), and refuses to overwrite corrupt JSON.
"""
import json

from contextruntime import install as I


def _settings(scope):
    return json.loads(open(scope.settings_path).read()) if __import__("os").path.exists(scope.settings_path) else {}


# --------------------------------------------------------------------------- pure merge logic
def test_hook_block_has_seven_events_with_the_right_matchers():
    block = I.build_hook_block("contextruntime cr-hook --db /x")
    assert set(block) == {ev for ev, _ in I.HOOK_EVENTS} and len(block) == 7
    # SessionStart / UserPromptSubmit / PostToolBatch carry NO matcher; the rest carry "".
    assert "matcher" not in block["SessionStart"][0]
    assert block["PreToolUse"][0]["matcher"] == ""


def test_merge_hooks_is_idempotent():
    cmd = "contextruntime cr-hook --db /x"
    once = I.merge_hooks({}, cmd)
    twice = I.merge_hooks(once, cmd)
    assert once == twice
    # exactly one cr-hook group per event, never two
    for ev, _ in I.HOOK_EVENTS:
        cr = [g for g in twice["hooks"][ev] if I._is_cr_group(g)]
        assert len(cr) == 1, ev


def test_merge_preserves_a_users_own_hooks():
    user = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}]}}
    merged = I.merge_hooks(user, "contextruntime cr-hook --db /x")
    cmds = [h["command"] for g in merged["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "my-linter" in cmds and any("cr-hook" in c for c in cmds)


def test_reinstall_with_a_new_db_replaces_not_duplicates():
    a = I.merge_hooks({}, "contextruntime cr-hook --db /old")
    b = I.merge_hooks(a, "contextruntime cr-hook --db /new")
    cmds = [h["command"] for g in b["hooks"]["PostToolUse"] for h in g["hooks"]]
    assert cmds == ["contextruntime cr-hook --db /new"]  # old path gone, single entry


def test_strip_restores_exactly_the_users_hooks():
    user = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}]}}
    merged = I.merge_hooks(user, "contextruntime cr-hook --db /x")
    assert I.strip_hooks(merged) == user


def test_strip_drops_the_hooks_key_when_only_ours_existed():
    merged = I.merge_hooks({}, "contextruntime cr-hook --db /x")
    assert I.strip_hooks(merged) == {}


def test_mcp_merge_and_strip_round_trip():
    m = I.resolve_scope("claude", "/repo", False)
    merged = I.merge_mcp({"mcpServers": {"other": {"command": "x"}}}, m)
    assert "other" in merged["mcpServers"] and "contextruntime" in merged["mcpServers"]
    assert I.strip_mcp(merged) == {"mcpServers": {"other": {"command": "x"}}}


# --------------------------------------------------------------------------- end-to-end (sandbox)
def test_install_writes_settings_manifest_and_verifies(tmp_path):
    proj = tmp_path / "repo"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "m.py").write_text("def f():\n    return 1\n")
    rep = I.install("claude", project=str(proj), with_index=False)
    assert rep.ok
    scope = I.resolve_scope("claude", str(proj), False)
    s = json.loads((proj / ".claude" / "settings.json").read_text())
    assert I.hooks_wired(s) == [ev for ev, _ in I.HOOK_EVENTS]                 # all 7 wired
    assert I.policy_wired(s)                                                    # cr-policy on SessionStart
    assert json.loads((proj / ".mcp.json").read_text())["mcpServers"]["contextruntime"]
    man = json.loads(open(scope.config_path).read())
    assert man["mode"] == "advisory" and man["hook_schema"] == "0.4.1" and man["with_policy"] is True
    assert rep.verify["checks"] and any(c["check"] == "cr-hook-wired" and c["ok"] for c in rep.verify["checks"])


def test_no_policy_wires_crhook_but_not_the_brief(tmp_path):
    proj = tmp_path / "repo"
    proj.mkdir()
    I.install("claude", project=str(proj), with_index=False, with_policy=False)
    s = json.loads((proj / ".claude" / "settings.json").read_text())
    assert I.hooks_wired(s) == [ev for ev, _ in I.HOOK_EVENTS]                  # observation still fully wired
    assert not I.policy_wired(s)                                                # but no advisory steering


def test_dry_run_touches_nothing(tmp_path):
    proj = tmp_path / "repo"
    proj.mkdir()
    rep = I.install("claude", project=str(proj), with_index=False, dry_run=True)
    assert rep.dry_run
    assert not (proj / ".claude").exists() and not (proj / ".mcp.json").exists()


def test_install_then_uninstall_is_a_clean_round_trip(tmp_path):
    proj = tmp_path / "repo"
    proj.mkdir()
    # a pre-existing user hook that must survive the whole cycle
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": "Bash",
                                   "hooks": [{"type": "command", "command": "my-linter"}]}]}}))
    before = json.loads((proj / ".claude" / "settings.json").read_text())
    I.install("claude", project=str(proj), with_index=False)
    I.uninstall("claude", project=str(proj))
    after = json.loads((proj / ".claude" / "settings.json").read_text())
    assert after == before                                                     # exact restoration


def test_backup_is_written_before_modifying_existing_settings(tmp_path):
    import os
    proj = tmp_path / "repo"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text('{"hooks": {}}')
    I.install("claude", project=str(proj), with_index=False)
    assert os.path.exists(str(proj / ".claude" / "settings.json.crbak"))


def test_corrupt_settings_is_refused_not_overwritten(tmp_path):
    proj = tmp_path / "repo"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text("{ this is not json")
    rep = I.install("claude", project=str(proj), with_index=False)
    step = next(s for s in rep.steps if s.name == "register-cr-hook")
    assert not step.ok and "not valid JSON" in step.note
    # the corrupt file is left exactly as-is
    assert (proj / ".claude" / "settings.json").read_text() == "{ this is not json"


def test_global_scope_has_no_project_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rep = I.install("claude", use_global=True, with_mcp=True, with_index=False)
    mcp_step = next(s for s in rep.steps if s.name == "register-cr-mcp")
    assert not mcp_step.changed and "global scope" in mcp_step.detail
