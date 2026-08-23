"""`cr doctor --prefix` — measure and itemize the FIXED prefix (system prompt + tool definitions +
injected context) that is re-read on every API call.

`docs/path-to-50.md` measured that this fixed prefix is ~73% of all resident token-turns on lean
sessions and ~99% in heavy-MCP environments (82k tokens before any work starts). It is the largest
lever in the program, and it is mostly configuration — so the doctor does three things:

1. CAPTURE (zero model quota): start a local capture proxy, run `claude -p` against it, record the
   FIRST request body (system + tools + first message) and answer with a non-retryable 400. The
   request never reaches Anthropic. Auth headers are never stored.
2. ITEMIZE: token-count every tool definition (grouped by MCP server / builtin) and every system /
   injected block (CLAUDE.md, memory, skills, agents, env, core).
3. EVIDENCE + ADVICE: scan the user's recent session transcripts for which MCP servers, skills and
   agents were ACTUALLY used, and recommend disabling/deferring the unused, heavy ones — sized in
   tokens per call and token-turns per session.

Heuristic token counts are calibrated against the REAL `cache_creation_input_tokens` observed on the
first call of recent sessions when available.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .reducers.base import tokens as _tok

try:
    from corpus.transcript_util import merged_records          # optional (corpus may be absent)
except Exception:      # noqa: BLE001
    merged_records = None


# --------------------------------------------------------------------------- capture
class _Capture:
    bodies = []                                      # every /v1/messages body seen (auth never kept)
    event = threading.Event()


class _CaptureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _reply(self, code, payload: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if self.path.split("?", 1)[0].endswith("/v1/messages"):
            try:
                body = json.loads(raw)
                if isinstance(body, dict) and "messages" in body:
                    _Capture.bodies.append(body)          # headers deliberately NOT kept
                    if body.get("tools"):                 # the MAIN agent request carries the tools
                        _Capture.event.set()
            except Exception:      # noqa: BLE001
                pass
        # non-retryable client error so the CLI stops immediately; nothing reaches Anthropic
        self._reply(400, b'{"type":"error","error":{"type":"invalid_request_error","message":"cr-doctor prefix capture: request recorded, not forwarded"}}')

    def do_GET(self):
        self._reply(404, b'{"type":"error","error":{"type":"not_found_error","message":"cr-doctor capture"}}')


def capture_first_request(cwd: str, *, timeout: float = 90.0, prompt: str = "Reply with the single word: ok",
                          extra_args=()) -> dict:
    """Run `claude -p` against a local capture proxy and return the MAIN /v1/messages body (the one
    carrying tool definitions; Claude Code also fires small auxiliary calls first). Zero model quota.
    Raises RuntimeError if nothing was captured."""
    _Capture.bodies, _Capture.event = [], threading.Event()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    env = dict(os.environ, ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}")
    env.pop("CR_GATEWAY_MODE", None)
    argv = ["claude", "-p", prompt, "--output-format", "json", "--max-budget-usd", "0.05",
            "--no-session-persistence", *extra_args]
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        got = _Capture.event.wait(timeout)
    finally:
        try:
            proc.wait(timeout=15)
        except Exception:      # noqa: BLE001
            proc.kill()
        httpd.shutdown()
    if not _Capture.bodies:
        raise RuntimeError("no /v1/messages request captured (is `claude` on PATH and logged in?)")
    # prefer the request with tools (main agent call); else the largest
    with_tools = [b for b in _Capture.bodies if b.get("tools")]
    pool = with_tools or _Capture.bodies
    return max(pool, key=lambda b: len(json.dumps(b)))


# --------------------------------------------------------------------------- itemize
@dataclass
class Item:
    group: str            # 'tool:<server>' | 'tool:builtin' | 'system' | 'injected'
    name: str
    tokens: int
    kind: str = ""        # tool | block
    detail: str = ""


_BLOCK_RULES = (
    ("claude_md", re.compile(r"CLAUDE\.md|claudeMd", re.I)),
    ("memory", re.compile(r"MEMORY\.md|auto-memory|memory directory", re.I)),
    ("skills", re.compile(r"skills? (are )?available|Skill tool|invoke a skill", re.I)),
    ("agents", re.compile(r"agent types|subagent_type|Available agent", re.I)),
    ("deferred_tools", re.compile(r"deferred tools|ToolSearch", re.I)),
    ("mcp_instructions", re.compile(r"MCP Server Instructions|MCP servers? (have )?provided", re.I)),
    ("environment", re.compile(r"Primary working directory|Is a git repository|Platform:|OS Version", re.I)),
)


def _classify_block(text: str) -> str:
    for label, rx in _BLOCK_RULES:
        if rx.search(text or ""):
            return label
    return "core"


def _blocks(x):
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        out = []
        for b in x:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
            elif isinstance(b, str):
                out.append(b)
        return out
    return []


def itemize(body: dict) -> list:
    """Token-count every tool definition and every system/injected text block of a request."""
    items = []
    for t in body.get("tools") or []:
        name = t.get("name", "?")
        server = name.split("__")[1] if name.startswith("mcp__") and name.count("__") >= 2 else "builtin"
        items.append(Item(f"tool:{server}", name, _tok(json.dumps(t, ensure_ascii=False)), "tool"))
    for i, txt in enumerate(_blocks(body.get("system"))):
        items.append(Item("system", f"system[{i}]:{_classify_block(txt)}", _tok(txt), "block",
                          (txt[:80] if txt else "").replace("\n", " ")))
    msgs = body.get("messages") or []
    if msgs:
        for j, txt in enumerate(_blocks(msgs[0].get("content"))):
            lab = "injected" if "<system-reminder>" in txt or _classify_block(txt) != "core" else "user_prompt"
            items.append(Item("injected" if lab == "injected" else "user", f"msg0[{j}]:{_classify_block(txt)}",
                              _tok(txt), "block", txt[:80].replace("\n", " ")))
    return items


def summarize_items(items: list) -> dict:
    by_group = Counter()
    for it in items:
        by_group[it.group] += it.tokens
    tools_total = sum(v for k, v in by_group.items() if k.startswith("tool:"))
    return {"total_est_tokens": sum(it.tokens for it in items), "tools_est_tokens": tools_total,
            "n_tools": sum(1 for it in items if it.kind == "tool"),
            "by_group": dict(by_group.most_common())}


# --------------------------------------------------------------------------- evidence
@dataclass
class Evidence:
    sessions: int = 0
    startup_prefix: list = field(default_factory=list)     # real first-call P per session
    calls_per_session: list = field(default_factory=list)
    sum_P: int = 0
    per_session_P: list = field(default_factory=list)
    mcp_server_uses: Counter = field(default_factory=Counter)
    builtin_tool_uses: Counter = field(default_factory=Counter)
    skill_uses: Counter = field(default_factory=Counter)
    agent_uses: Counter = field(default_factory=Counter)


def _iter_records(path):
    if merged_records is not None:
        yield from merged_records(path)
        return
    for line in open(path, errors="replace"):
        try:
            yield json.loads(line)
        except Exception:      # noqa: BLE001
            continue


def usage_evidence(projects_dir: str = None, *, max_sessions: int = 40, project_filter: str = None) -> Evidence:
    """Scan recent transcripts: real startup prefix, calls/session, and which MCP servers / skills /
    agents were actually invoked."""
    projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")
    files = glob.glob(os.path.join(projects_dir, "*", "*.jsonl"))
    if project_filter:
        files = [f for f in files if project_filter in f]
    files = sorted(files, key=os.path.getmtime, reverse=True)[:max_sessions]
    ev = Evidence()
    for f in files:
        first_P, calls, sP = None, 0, 0
        for rec in _iter_records(f):
            m = rec.get("message") or {}
            if rec.get("type") != "assistant" or not isinstance(m.get("content"), list):
                continue
            u = m.get("usage")
            if u:
                P = u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("input_tokens", 0)
                calls += 1
                sP += P
                if first_P is None:
                    first_P = P
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n, inp = b.get("name", ""), (b.get("input") or {})
                    if n.startswith("mcp__") and n.count("__") >= 2:
                        ev.mcp_server_uses[n.split("__")[1]] += 1
                    else:
                        ev.builtin_tool_uses[n] += 1
                    if n == "Skill":
                        ev.skill_uses[inp.get("skill", "?")] += 1
                    if n == "Agent":
                        ev.agent_uses[inp.get("subagent_type", "general-purpose")] += 1
        if calls:
            ev.sessions += 1
            ev.startup_prefix.append(first_P or 0)
            ev.calls_per_session.append(calls)
            ev.per_session_P.append(sP)
            ev.sum_P += sP
    return ev


def config_usage_records() -> dict:
    """Claude Code's own skill/plugin usage counters (~/.claude.json), if present."""
    try:
        d = json.load(open(os.path.expanduser("~/.claude.json")))
        return {"skillUsage": d.get("skillUsage") or {}, "pluginUsage": d.get("pluginUsage") or {},
                "mcpServers": list((d.get("mcpServers") or {}).keys())}
    except Exception:      # noqa: BLE001
        return {"skillUsage": {}, "pluginUsage": {}, "mcpServers": []}


# --------------------------------------------------------------------------- report
READ_MULT, WRITE_MULT_1H = 0.1, 2.0
HEAVY_TOOL = 1500            # calibrated tokens/call above which an unused tool is worth disallowing


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def build_report(items: list, ev: Evidence, cfg: dict, *, real_first_P: int = None,
                 price_per_mtok_input: float = 3.0) -> dict:
    s = summarize_items(items)
    est = s["total_est_tokens"]
    calib = (real_first_P / est) if (real_first_P and est) else 1.0     # anchor to the REAL first call
    med_calls = _median(ev.calls_per_session)
    med_startup = _median(ev.startup_prefix)
    shares = [min(p * c / sp, 1.0) for p, c, sp in zip(ev.startup_prefix, ev.calls_per_session, ev.per_session_P) if sp]
    fixed_share = _median(shares)
    # component rows (by group) with usage evidence
    rows = []
    for group, toks in s["by_group"].items():
        used = None
        if group.startswith("tool:"):
            srv = group.split(":", 1)[1]
            used = ev.mcp_server_uses.get(srv, 0) if srv != "builtin" else sum(ev.builtin_tool_uses.values())
        rows.append({"group": group, "est_tokens": round(toks * calib), "uses_in_recent_sessions": used})
    rows.sort(key=lambda r: -r["est_tokens"])
    # per-tool rows (the actionable granularity)
    tools = []
    for it in items:
        if it.kind != "tool":
            continue
        srv = it.group.split(":", 1)[1]
        uses = ev.builtin_tool_uses.get(it.name, 0) if srv == "builtin" else ev.mcp_server_uses.get(srv, 0)
        tools.append({"name": it.name, "server": srv, "tokens": round(it.tokens * calib), "uses": uses})
    tools.sort(key=lambda t: -t["tokens"])
    # recommendations: unused heavy MCP servers (whole server), then unused heavy builtins (per tool)
    recs = []
    by_srv = defaultdict(lambda: [0, 0])
    for t in tools:
        by_srv[t["server"]][0] += 1
        by_srv[t["server"]][1] += t["tokens"]
    for srv, (n, toks) in by_srv.items():
        if srv != "builtin" and ev.mcp_server_uses.get(srv, 0) == 0 and toks >= HEAVY_TOOL:
            recs.append({"action": f"disconnect MCP server '{srv}' for this project, or run with --disallowedTools \"mcp__{srv}__*\"",
                         "tokens_per_call": toks, "token_turns_per_session": round(toks * (med_calls or 1)),
                         "reason": f"{n} tools, 0 uses in the last {ev.sessions} sessions"})
    for t in tools:
        if t["server"] == "builtin" and t["uses"] == 0 and t["tokens"] >= HEAVY_TOOL:
            recs.append({"action": f"--disallowedTools {t['name']} (or permissions.deny in settings)",
                         "tokens_per_call": t["tokens"], "token_turns_per_session": round(t["tokens"] * (med_calls or 1)),
                         "reason": f"0 uses in the last {ev.sessions} sessions"})
    for it in items:
        if it.kind == "block" and it.tokens * calib >= 3000:
            recs.append({"action": f"trim {it.name}", "tokens_per_call": round(it.tokens * calib),
                         "token_turns_per_session": round(it.tokens * calib * (med_calls or 1)),
                         "reason": "large always-resident block"})
    recs.sort(key=lambda r: -r["tokens_per_call"])
    potential = sum(r["tokens_per_call"] for r in recs)
    startup = round(est * calib)
    return {"startup_prefix_est_tokens": startup, "calibration_factor": round(calib, 3),
            "measured_startup_prefix_median": med_startup,
            "n_tools": s["n_tools"], "tools_est_tokens": round(s["tools_est_tokens"] * calib),
            "sessions_scanned": ev.sessions, "median_calls_per_session": med_calls,
            "fixed_prefix_share_of_sum_P_median": round(100 * fixed_share, 1) if fixed_share is not None else None,
            "rows": rows, "tools": tools[:25], "recommendations": recs,
            "potential_tokens_per_call_saved": potential,
            "potential_share_of_startup": round(100 * potential / startup, 1) if startup else None,
            "est_cost_per_session_usd_fixed_reads": round(startup * (med_calls or 1) * READ_MULT * price_per_mtok_input / 1e6, 3) if med_calls else None,
            "mcp_servers_used": dict(ev.mcp_server_uses.most_common()),
            "skills_used": dict(ev.skill_uses.most_common()), "agents_used": dict(ev.agent_uses.most_common())}


def format_report(rep: dict) -> str:
    L = ["=== cr doctor --prefix : the fixed prefix re-read on EVERY API call ==="]
    L.append(f"  startup prefix: ~{rep['startup_prefix_est_tokens']:,} tokens, of which {rep['n_tools']} tool definitions = ~{rep['tools_est_tokens']:,}"
             + (f"  (calibrated to the real first call; median over recent sessions {rep['measured_startup_prefix_median']:,})" if rep.get("measured_startup_prefix_median") else "  (uncalibrated heuristic)"))
    if rep.get("median_calls_per_session"):
        L.append(f"  recent sessions: {rep['sessions_scanned']} scanned; median {rep['median_calls_per_session']} API calls/session"
                 + (f"; fixed prefix = {rep['fixed_prefix_share_of_sum_P_median']}% of resident token-turns (median)" if rep.get("fixed_prefix_share_of_sum_P_median") is not None else "")
                 + (f"; ~${rep['est_cost_per_session_usd_fixed_reads']}/session re-reading it" if rep.get("est_cost_per_session_usd_fixed_reads") else ""))
    L.append("")
    L.append(f"  {'component':<30} {'tokens/call':>11} {'used (recent)':>14}")
    for r in rep["rows"]:
        u = "" if r["uses_in_recent_sessions"] is None else str(r["uses_in_recent_sessions"])
        L.append(f"  {r['group']:<30} {r['est_tokens']:>11,} {u:>14}")
    L.append("")
    L.append(f"  {'heaviest tool definitions':<42} {'tokens/call':>11} {'uses':>6}")
    for t in rep["tools"][:12]:
        L.append(f"  {t['name']:<42} {t['tokens']:>11,} {t['uses']:>6}")
    L.append("")
    if rep["recommendations"]:
        L.append(f"  RECOMMENDATIONS — potential −{rep['potential_tokens_per_call_saved']:,} tokens on EVERY call "
                 f"({rep['potential_share_of_startup']}% of the startup prefix):")
        for r in rep["recommendations"]:
            L.append(f"   - −{r['tokens_per_call']:>6,}/call  {r['action']}   [{r['reason']}]")
    else:
        L.append("  no unused heavy components found")
    if rep.get("mcp_servers_used"):
        L.append(f"\n  MCP servers actually used: {rep['mcp_servers_used']}")
    if rep.get("skills_used"):
        L.append(f"  skills actually used: {rep['skills_used']}")
    return "\n".join(L)


def run(cwd: str = None, *, capture: bool = True, sessions: int = 40, project_filter: str = None,
        body: dict = None) -> dict:
    cwd = cwd or os.getcwd()
    if body is None and capture:
        body = capture_first_request(cwd)
    items = itemize(body) if body else []
    ev = usage_evidence(max_sessions=sessions, project_filter=project_filter)
    cfg = config_usage_records()
    # calibrate heuristic tokens against REAL first-call prefixes of sessions from THIS project only
    same = usage_evidence(max_sessions=sessions, project_filter=os.path.abspath(cwd).replace("/", "-"))
    real_first = _median(same.startup_prefix) if same.startup_prefix else None
    return build_report(items, ev, cfg, real_first_P=real_first)
