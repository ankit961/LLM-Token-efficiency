"""cr doctor --prefix v1 — classification, audit actions + feasibility, counterfactual monotonicity,
deferral-aware lean attribution, duplicates, evidence scan, and the capture proxy."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from contextruntime import prefixdoctor as pd

CORE = ("\nYou are an interactive agent that helps users with software engineering tasks.\n\n# System\nx\n"
        "# Doing tasks\ny\n# Using your tools\nz\n# auto memory\nMEMORY.md is loaded each session\n# Environment\nPlatform: darwin\n")


def _body():
    big = {"type": "object", "properties": {k: {"type": "string", "description": "x" * 200} for k in "abcdefgh"}}
    return {"model": "claude-sonnet-5", "stream": True,
            "system": [{"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.229"},
                       {"type": "text", "text": CORE}],
            "tools": [{"name": "Bash", "description": "run", "input_schema": big},
                      {"name": "Workflow", "description": "orchestrate " * 400, "input_schema": big},
                      {"name": "DesignSync", "description": "design " * 400, "input_schema": big},
                      {"name": "mcp__gmail__send", "description": "send mail " * 300, "input_schema": big},
                      {"name": "mcp__gmail__list", "description": "list mail " * 300, "input_schema": big}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>\n# claudeMd\nContents of /p/CLAUDE.md (project instructions):\n" + "rule " * 3000 + "</system-reminder>"},
                {"type": "text", "text": "fix the bug"}]}]}


def _sessions(n=10, calls=50):
    """n sessions; Bash used at call 1 everywhere; Workflow used in 2 sessions, late (call 40)."""
    out = []
    for i in range(n):
        uses = {"Bash": 30}
        first = {"Bash": 1}
        if i < 2:
            uses["Workflow"] = 1
            first["Workflow"] = 40
        out.append(pd.SessionStats(f"s{i}", calls, 40000, 40000 * calls * 1.4, first, pd.Counter(uses)))
    return out


def test_classification_core_vs_injected_markers():
    assert pd._classify_block(CORE) == "system_core"                    # mentions memory/env but IS the core prompt
    assert pd._classify_block("# claudeMd\nContents of /p/CLAUDE.md\nrules") == "claude_md"
    assert pd._classify_block("Contents of /x/MEMORY.md (user's auto-memory)") == "memory"
    items = pd.itemize(_body())
    cats = {it.name: it.category for it in items}
    assert cats["system[1]"] == "system_core" and cats["msg0[0]"] == "claude_md" and cats["msg0[1]"] == "user_prompt"
    assert {it.category for it in items if it.name.startswith("mcp__")} == {"mcp_tool"}


def test_audit_actions_and_feasibility():
    rows = {r["name"]: r for r in pd.audit_tools(pd.itemize(_body()), _sessions(), 1.7)}
    assert rows["Bash"]["action"] == "KEEP" and rows["Bash"]["wasted_residency"] == 0
    assert rows["DesignSync"]["action"] == "DISABLE?" and rows["DesignSync"]["feasibility"] == pd.FEAS["SUB"]
    assert rows["mcp__gmail__send"]["action"] == "DISABLE?" and rows["mcp__gmail__send"]["feasibility"] == pd.FEAS["SUB"]
    assert rows["Workflow"]["action"] == "DEFER" and rows["Workflow"]["feasibility"] == pd.FEAS["ANT"]
    assert rows["Workflow"]["median_first_use_call"] == 40
    # wasted residency for a never-used tool = tokens × N
    assert rows["DesignSync"]["wasted_residency"] == rows["DesignSync"]["tokens"] * 50
    assert pd.audit_tools(pd.itemize(_body()), _sessions(n=3), 1.7)[0]["action"] == "UNKNOWN"   # too little evidence
    blocks = {r["category"]: r for r in pd.audit_blocks(pd.itemize(_body()), 1.7, _sessions())}
    assert blocks["claude_md"]["action"] == "COMPRESS" and blocks["claude_md"]["feasibility"] == pd.FEAS["SUB"]
    assert blocks["system_core"]["action"] == "KEEP" and blocks["system_core"]["feasibility"] == pd.FEAS["ANT"]


def test_counterfactuals_are_monotone_and_reconcile():
    rep = pd.build_report(_body(), _sessions(), env_label="t", real_startup=60000)
    cf = rep["counterfactuals"]
    seq = [cf[k]["prefix_tokens"] for k in ("P0", "P1", "P1b", "P2", "P3_conservative", "P3_realistic", "P4_oracle")]
    assert seq == sorted(seq, reverse=True)                                 # each step removes more
    assert cf["P1"]["note"].startswith("servers: ['gmail']")                # the unused server
    assert cf["subscription_achievable"]["prefix_tokens"] <= cf["P1b"]["prefix_tokens"]
    assert cf["gateway_achievable"]["prefix_tokens"] <= cf["subscription_achievable"]["prefix_tokens"]
    rc = rep["reconciliation"]
    assert rc["measured_cl100k_total"] == sum(rc["by_category_cl100k"].values())
    assert abs(sum(rc["by_category_estimated"].values()) - 60000) <= 5       # proportional attribution sums to the real prefix
    assert rep["totals"]["startup_prefix_tokens"] == 60000
    assert 0 < rep["totals"]["fixed_prefix_share_pct"] <= 100


def test_lean_items_exclude_deferred_tools(tmp_path):
    rows = [{"type": "attachment", "attachment": {"type": "deferred_tools_delta", "addedNames": ["DesignSync", "mcp__gmail__send", "mcp__gmail__list"], "addedLines": ["DesignSync", "mcp__gmail__send"]}},
            {"type": "attachment", "attachment": {"type": "skill_listing", "content": "- pdf: make pdfs\n- xlsx: sheets"}},
            {"type": "attachment", "attachment": {"type": "agent_listing_delta", "addedLines": ["- Explore: read-only search"]}}]
    tp = tmp_path / "s.jsonl"
    tp.write_text("\n".join(json.dumps(r) for r in rows))
    items, notes = pd.lean_items([str(tp)], _body())
    names = {it.name for it in items}
    assert "Bash" in names and "Workflow" in names                          # loaded
    assert "DesignSync" not in names and "mcp__gmail__send" not in names      # deferred ⇒ not resident
    assert {"skills", "agents", "deferred_tools", "system[1]"} <= names      # measured listings + core from reference
    assert any("deferred (not resident)" in n for n in notes)


def test_duplicates_detect_exact_repeats():
    para = "This exact paragraph is repeated verbatim across two different blocks to be detected. " * 3
    items = [pd.Item("system_core", "s", "a", 10, "", para + "\n\nother text " * 20),
             pd.Item("injected_reminder", "m", "b", 10, "", "intro\n\n" + para)]
    d = pd.find_duplicates(items, 1.0)
    assert len(d["exact"]) == 1 and d["exact"][0]["duplicate_of"] == "a" and d["exact_dup_tokens"] > 0


def test_session_stats_first_use_and_startup(tmp_path):
    u = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 30000, "input_tokens": 5, "output_tokens": 3}
    rows = [{"type": "assistant", "requestId": "r1", "message": {"usage": u, "content": [{"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]}},
            {"type": "assistant", "requestId": "r2", "message": {"usage": {**u, "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 100},
                                                                "content": [{"type": "tool_use", "id": "b", "name": "mcp__gmail__send", "input": {}}]}}]
    tp = tmp_path / "s.jsonl"
    tp.write_text("\n".join(json.dumps(r) for r in rows))
    s = pd.session_stats(str(tp))
    assert s.calls == 2 and s.startup_P == 30005 and s.first_use == {"Bash": 1, "mcp__gmail__send": 2}


def test_capture_handler_records_main_request_and_rejects():
    pd._Capture.bodies, pd._Capture.event = [], threading.Event()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pd._CaptureHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        for payload in (json.dumps({"messages": [{"role": "user", "content": "title?"}]}), json.dumps(_body())):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("POST", "/v1/messages", body=payload.encode(), headers={"x-api-key": "sk-secret", "Content-Type": "application/json"})
            r = c.getresponse(); r.read(); c.close()
            assert r.status == 400                                          # never forwarded
        assert pd._Capture.event.is_set() and len(pd._Capture.bodies) == 2
        assert "sk-secret" not in json.dumps(pd._Capture.bodies)            # auth never stored
    finally:
        srv.shutdown()
