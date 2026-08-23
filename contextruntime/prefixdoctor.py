"""`cr doctor --prefix` (v1) — measure, attribute and audit the FIXED prefix re-read on every API call.

`docs/path-to-50.md` measured that the fixed prefix (system prompt + tool definitions + injected
context) is ~73% of resident token-turns on lean sessions and ~99% in heavy-MCP environments. It is the
largest lever in the program and mostly configuration. This doctor is DIAGNOSTIC ONLY: it never
disables or rewrites anything.

Pipeline
  1. CAPTURE (zero model quota): a local capture proxy records the MAIN `/v1/messages` body that
     `claude -p` sends (system + tools + first message) and answers a non-retryable 400. Nothing
     reaches Anthropic; auth headers are never stored.
  2. ATTRIBUTE + RECONCILE: tokenize every tool definition and system/injected block with cl100k
     (tiktoken) or the stdlib heuristic, then reconcile against the REAL first-call prefix
     (`cache_read + cache_creation + input` of the first API call of sessions in the same environment).
     Claude's tokenizer counts ~1.74× cl100k on coding-agent content (empirical: 455 clean call-deltas,
     IQR 1.61–1.89), so every category is reported as MEASURED (cl100k), ESTIMATED (× factor), and the
     RESIDUAL is stated, never hidden.
  3. AUDIT: per tool — schema resident? ever invoked? first-use call? calls before first use? — from
     real sessions (requestId-merged); wasted residency = tokens × calls-before-first-use (× N if never
     used). Conservative actions KEEP / DEFER / DISABLE? / COMPRESS / UNKNOWN, each tagged
     SUBSCRIPTION_CONFIG / GATEWAY_CONTROLLABLE / ANTHROPIC_CLIENT_REQUIRED.
  4. COUNTERFACTUALS P0..P4 replayed over the observed call counts: ΔT ≈ (P0 − Pk) × N_calls and the
     share of observed Σ P_t — counterfactual opportunities, not realized savings.
  5. DUPLICATES: exact / near-duplicate paragraphs across static instruction blocks (mechanical only).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .reducers.base import tokens as _heur

try:
    from corpus.transcript_util import merged_records          # optional (corpus may be absent)
except Exception:      # noqa: BLE001
    merged_records = None

CLAUDE_PER_CL100K = 1.74        # empirical Claude-tokens per cl100k-token on coding-agent content (n=455)
CLAUDE_PER_CL100K_IQR = (1.61, 1.89)
FEAS = {"SUB": "SUBSCRIPTION_CONFIG", "GW": "GATEWAY_CONTROLLABLE", "ANT": "ANTHROPIC_CLIENT_REQUIRED"}
HEAVY_CL100K = 300              # below this (cl100k) a tool is not worth acting on
LATE_FIRST_USE = 0.30           # first used after 30% of a session's calls ⇒ DEFER candidate


# --------------------------------------------------------------------------- tokenizer
def _tokenizer():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return "cl100k_base", (lambda s: len(enc.encode(s or "", disallowed_special=())))
    except Exception:      # noqa: BLE001
        return "stdlib-heuristic", _heur


TOKENIZER_NAME, tok = _tokenizer()


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
        self._reply(400, b'{"type":"error","error":{"type":"invalid_request_error","message":"cr-doctor prefix capture: request recorded, not forwarded"}}')

    def do_GET(self):
        self._reply(404, b'{"type":"error","error":{"type":"not_found_error","message":"cr-doctor capture"}}')


def capture_first_request(cwd: str, *, timeout: float = 90.0, prompt: str = "Reply with the single word: ok",
                          extra_args=()) -> dict:
    """Run `claude -p` against a local capture proxy; return the MAIN /v1/messages body (the one that
    carries `tools`; Claude Code fires small auxiliary calls first). Zero model quota."""
    _Capture.bodies, _Capture.event = [], threading.Event()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    env = dict(os.environ, ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}")
    env.pop("CR_GATEWAY_MODE", None)
    argv = ["claude", "-p", prompt, "--output-format", "json", "--max-budget-usd", "0.05",
            "--no-session-persistence", *extra_args]
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,   # -p reads a non-TTY stdin into the prompt
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _Capture.event.wait(timeout)
    finally:
        try:
            proc.wait(timeout=15)
        except Exception:      # noqa: BLE001
            proc.kill()
        httpd.shutdown()
    if not _Capture.bodies:
        raise RuntimeError("no /v1/messages request captured (is `claude` on PATH and logged in?)")
    with_tools = [b for b in _Capture.bodies if b.get("tools")]
    return max(with_tools or _Capture.bodies, key=lambda b: len(json.dumps(b)))


# --------------------------------------------------------------------------- items
@dataclass
class Item:
    category: str          # mcp_tool | builtin_tool | system_core | claude_md | memory | skills | agents | deferred_tools | mcp_instructions | environment | injected_reminder | user_prompt
    source: str
    name: str
    tokens_cl100k: int
    server: str = ""
    text: str = ""         # for duplicate detection only (never emitted)


_CORE_HEADINGS = ("# Doing tasks", "# Using your tools", "# Tone and style", "# Executing actions with care", "# System")
_BLOCK_RULES = (                                    # injection MARKERS, not mere mentions
    ("claude_md", re.compile(r"^# claudeMd\b|Contents of \S*CLAUDE\.md", re.M)),
    ("memory", re.compile(r"Contents of \S*MEMORY\.md|^# userMemory\b", re.M)),
    ("skills", re.compile(r"^# (skills|Skills)\b|skill_listing|The following skills are available", re.M)),
    ("agents", re.compile(r"Available agent types|agent_listing", re.M)),
    ("deferred_tools", re.compile(r"deferred tools are now available|ToolSearch", re.M)),
    ("mcp_instructions", re.compile(r"^# MCP Server Instructions", re.M)),
)


def _classify_block(text: str) -> str:
    t = text or ""
    if t.lstrip().startswith("You are an interactive agent") or sum(h in t for h in _CORE_HEADINGS) >= 3:
        return "system_core"                          # Claude Code's own prompt (it MENTIONS memory/env/CLAUDE.md)
    for label, rx in _BLOCK_RULES:
        if rx.search(t):
            return label
    if "<system-reminder>" in t:
        return "injected_reminder"
    return "system_core"


def _blocks(x):
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return [b.get("text", "") if isinstance(b, dict) else str(b) for b in x
                if (isinstance(b, dict) and b.get("type") == "text") or isinstance(b, str)]
    return []


def itemize(body: dict) -> list:
    """Every tool definition and every system / first-message block of a captured request."""
    items = []
    for t in body.get("tools") or []:
        name = t.get("name", "?")
        txt = json.dumps(t, ensure_ascii=False)
        if name.startswith("mcp__") and name.count("__") >= 2:
            items.append(Item("mcp_tool", "tools[]", name, tok(txt), name.split("__")[1], t.get("description", "")))
        else:
            items.append(Item("builtin_tool", "tools[]", name, tok(txt), "", t.get("description", "")))
    for i, txt in enumerate(_blocks(body.get("system"))):
        items.append(Item(_classify_block(txt), f"system[{i}]", f"system[{i}]", tok(txt), "", txt))
    msgs = body.get("messages") or []
    if msgs:
        for j, txt in enumerate(_blocks(msgs[0].get("content"))):
            cls = _classify_block(txt)
            if cls in ("claude_md", "memory", "skills", "agents", "deferred_tools", "mcp_instructions"):
                cat = cls                                   # a reminder CARRYING that content keeps its class
            elif "<system-reminder>" in txt or cls == "injected_reminder":
                cat = "injected_reminder"
            else:
                cat = "user_prompt"
            items.append(Item(cat, "messages[0]", f"msg0[{j}]", tok(txt), "", txt))
    return items


# --------------------------------------------------------------------------- evidence
@dataclass
class SessionStats:
    transcript: str
    calls: int
    startup_P: int
    sum_P: int
    first_use: dict = field(default_factory=dict)      # tool name -> first call index (1-based)
    uses: Counter = field(default_factory=Counter)


def _iter_records(path):
    if merged_records is not None:
        yield from merged_records(path)
        return
    for line in open(path, errors="replace"):
        try:
            yield json.loads(line)
        except Exception:      # noqa: BLE001
            continue


def session_stats(path: str):
    calls, startup, sP = 0, None, 0
    first, uses = {}, Counter()
    for rec in _iter_records(path):
        m = rec.get("message") or {}
        if rec.get("type") != "assistant" or not isinstance(m.get("content"), list):
            continue
        u = m.get("usage")
        if u:
            calls += 1
            P = u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("input_tokens", 0)
            sP += P
            if startup is None:
                startup = P
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                n = b.get("name", "")
                uses[n] += 1
                first.setdefault(n, max(calls, 1))
    return SessionStats(path, calls, startup or 0, sP, first, uses) if calls else None


def collect_sessions(projects_dir: str = None, *, max_sessions: int = 40, project_filter: str = None,
                     transcripts: list = None) -> list:
    if transcripts is None:
        projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")
        files = glob.glob(os.path.join(projects_dir, "*", "*.jsonl"))
        if project_filter:
            files = [f for f in files if project_filter in f]
        transcripts = sorted(files, key=os.path.getmtime, reverse=True)[:max_sessions]
    out = []
    for f in transcripts:
        try:
            s = session_stats(f)
        except Exception:      # noqa: BLE001
            s = None
        if s:
            out.append(s)
    return out


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def transcript_listings(path: str) -> dict:
    """What the client INJECTED in this session, read from its attachment records: deferred tool names
    (schemas NOT resident — listed by name only), the skill listing, agent listing and MCP instructions
    (all resident text, token-countable), plus per-turn reminder kinds."""
    out = {"deferred_names": set(), "deferred_text": "", "skills": "", "agents": "", "mcp_instructions": "", "reminders": Counter()}
    for rec in _iter_records(path):
        if rec.get("type") != "attachment":
            continue
        a = rec.get("attachment") or {}
        t = a.get("type")
        if t == "deferred_tools_delta":
            out["deferred_names"] |= set(a.get("addedNames") or [])
            out["deferred_text"] += "\n".join(a.get("addedLines") or [])
        elif t == "skill_listing":
            out["skills"] += a.get("content") or ""
        elif t == "agent_listing_delta":
            out["agents"] += "\n".join(a.get("addedLines") or [])
        elif t == "mcp_instructions_delta":
            out["mcp_instructions"] += json.dumps(a.get("instructions") or a.get("content") or a)
        else:
            out["reminders"][t] += 1
    return out


def lean_items(transcripts: list, reference_body: dict) -> tuple:
    """Items for an environment we could NOT capture: loaded tool schemas = reference tools minus the
    names this environment DEFERRED (from its transcripts), sized from the reference capture (same
    Claude Code version/model); listings measured from the transcripts; core prompt from the reference.
    Returns (items, notes)."""
    ref = {it.name: it for it in itemize(reference_body)}
    lst = [transcript_listings(tp) for tp in transcripts[:20]]
    deferred = set.intersection(*[x["deferred_names"] for x in lst if x["deferred_names"]]) if any(x["deferred_names"] for x in lst) else set()
    items, notes = [], []
    for name, it in ref.items():
        if it.category in ("mcp_tool", "builtin_tool"):
            if name in deferred:
                continue                                   # schema NOT resident here
            items.append(Item(it.category, "tools[] (reference sizes)", name, it.tokens_cl100k, it.server))
    core = [it for it in ref.values() if it.category == "system_core"]
    for it in core:
        items.append(Item("system_core", "reference capture", it.name, it.tokens_cl100k, "", it.text))
    pick = max(lst, key=lambda x: len(x["skills"]) + len(x["agents"])) if lst else None
    if pick:
        for cat, key in (("deferred_tools", "deferred_text"), ("skills", "skills"), ("agents", "agents"), ("mcp_instructions", "mcp_instructions")):
            if pick[key]:
                items.append(Item(cat, "transcript attachment (measured)", cat, tok(pick[key]), "", pick[key]))
        notes.append(f"deferred (not resident) in this environment: {len(deferred)} tools; per-turn reminders: {dict(pick['reminders'])}")
    notes.append("tool schema + core prompt sizes from the reference capture (same Claude Code version/model); listings measured from transcripts; CLAUDE.md/memory unattributed")
    return items, notes


# --------------------------------------------------------------------------- audit
def audit_tools(items: list, sessions: list, factor: float) -> list:
    """Per tool: residency, usage evidence, wasted residency, conservative action + feasibility."""
    N = _median([s.calls for s in sessions]) or 0
    n_sess = len(sessions)
    rows = []
    for it in items:
        if it.category not in ("mcp_tool", "builtin_tool"):
            continue
        used_in = [s for s in sessions if it.name in s.uses]
        uses = sum(s.uses[it.name] for s in used_in)
        first = _median([s.first_use[it.name] for s in used_in]) if used_in else None
        frac_first = [s.first_use[it.name] / s.calls for s in used_in]
        before = [(s.first_use[it.name] - 1) if it.name in s.uses else s.calls for s in sessions]
        cbfu = (sum(before) / len(before)) if before else N     # calls before first use, mean over ALL sessions
        toks = round(it.tokens_cl100k * factor)
        heavy = it.tokens_cl100k >= HEAVY_CL100K
        if n_sess < 5:
            action, why = "UNKNOWN", f"only {n_sess} sessions of evidence"
        elif not used_in and heavy:
            action, why = "DISABLE?", f"never invoked in {n_sess} sessions"
        elif not used_in:
            action, why = "KEEP", "unused but small"
        elif frac_first and _median(frac_first) > LATE_FIRST_USE and heavy:
            action, why = "DEFER", f"first used at call {first} (median), ~{100 * _median(frac_first):.0f}% into the session"
        else:
            action, why = "KEEP", f"used in {len(used_in)}/{n_sess} sessions, first at call {first}"
        # disconnecting a server / --disallowedTools works on the subscription client TODAY;
        # deferring a schema until first use needs Tool Search in the client (or a custom loop)
        feas = FEAS["SUB"] if action == "DISABLE?" else (FEAS["ANT"] if action == "DEFER" else FEAS["ANT"] if it.category == "builtin_tool" else FEAS["SUB"])
        rows.append({"source": it.source, "category": it.category, "server": it.server, "name": it.name,
                     "tokens": toks, "tokens_cl100k": it.tokens_cl100k, "enabled": True,
                     "sessions_used": len(used_in), "uses": uses, "median_first_use_call": first,
                     "calls_before_first_use_mean": round(cbfu, 1),
                     "resident_token_turns": round(toks * N), "wasted_residency": round(toks * cbfu),
                     "action": action, "reason": why, "feasibility": feas})
    rows.sort(key=lambda r: -r["wasted_residency"])
    return rows


def audit_blocks(items: list, factor: float, sessions: list) -> list:
    N = _median([s.calls for s in sessions]) or 0
    rows = []
    for it in items:
        if it.category in ("mcp_tool", "builtin_tool", "user_prompt"):
            continue
        toks = round(it.tokens_cl100k * factor)
        if it.category in ("claude_md", "memory"):
            action, feas, why = ("COMPRESS" if toks >= 1500 else "KEEP"), FEAS["SUB"], "user-owned static instructions"
        elif it.category in ("skills", "agents", "deferred_tools", "mcp_instructions"):
            action, feas, why = ("COMPRESS" if toks >= 1500 else "KEEP"), FEAS["SUB"], "listing shrinks by disabling unused plugins/servers"
        elif it.category == "injected_reminder":
            action, feas, why = "UNKNOWN", FEAS["ANT"], "client-injected reminder; not user-controllable"
        else:
            action, feas, why = "KEEP", FEAS["ANT"], "Claude Code core prompt; not ours"
        rows.append({"source": it.source, "category": it.category, "name": it.name, "tokens": toks,
                     "tokens_cl100k": it.tokens_cl100k, "resident_token_turns": round(toks * N),
                     "action": action, "reason": why, "feasibility": feas})
    rows.sort(key=lambda r: -r["tokens"])
    return rows


# --------------------------------------------------------------------------- duplicates
_WS = re.compile(r"\s+")


def _paragraphs(text: str):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if len(p.strip()) >= 80]


def find_duplicates(items: list, factor: float) -> dict:
    """Exact duplicates (normalized hash) and near-duplicates (5-gram shingle Jaccard >= 0.8) across
    static instruction blocks and tool descriptions. Mechanical only; no semantic rewriting."""
    paras = [(it.name, p) for it in items if it.category != "user_prompt" and it.text for p in _paragraphs(it.text)]
    seen, exact, exact_tokens = {}, [], 0
    for name, p in paras:
        h = hashlib.sha1(_WS.sub(" ", p).lower().encode()).hexdigest()
        if h in seen and seen[h] != name:
            exact.append({"in": name, "duplicate_of": seen[h], "tokens": round(tok(p) * factor)})
            exact_tokens += tok(p)
        seen.setdefault(h, name)

    def shingles(p):
        w = _WS.sub(" ", p).lower().split()
        return {" ".join(w[i:i + 5]) for i in range(max(len(w) - 4, 1))}
    near, near_tokens = [], 0
    sh = [(n, p, shingles(p)) for n, p in paras if len(p) >= 200]
    for i in range(len(sh)):
        for j in range(i + 1, len(sh)):
            a, b = sh[i][2], sh[j][2]
            if a and b and sh[i][0] != sh[j][0]:
                jac = len(a & b) / len(a | b)
                if 0.8 <= jac < 1.0:
                    near.append({"a": sh[i][0], "b": sh[j][0], "jaccard": round(jac, 2), "tokens": round(tok(sh[j][1]) * factor)})
                    near_tokens += tok(sh[j][1])
    return {"exact": exact[:20], "near": near[:20], "exact_dup_tokens": round(exact_tokens * factor),
            "near_dup_tokens": round(near_tokens * factor), "paragraphs_scanned": len(paras)}


# --------------------------------------------------------------------------- counterfactuals
def counterfactuals(tool_rows: list, block_rows: list, P0: int, N: float, fixed_share: float,
                    *, compress_conservative=0.3, compress_realistic=0.5) -> dict:
    """P0 current → P1 unused optional MCP servers removed → P2 + rare/late tools deferred → P3 +
    static instruction compression → P4 oracle. Prefix tokens per call, ΔT over N calls, % of Σ P_t."""
    by_server = defaultdict(list)
    for r in tool_rows:
        if r["category"] == "mcp_tool":
            by_server[r["server"]].append(r)
    unused_servers = {s: sum(r["tokens"] for r in rs) for s, rs in by_server.items() if all(r["sessions_used"] == 0 for r in rs)}
    p1_cut = sum(unused_servers.values())
    p1b_cut = sum(r["tokens"] for r in tool_rows if r["action"] == "DISABLE?" and r["server"] not in unused_servers)
    defer_rows = [r for r in tool_rows if r["action"] == "DEFER"]
    frac = (lambda r: min(r["calls_before_first_use_mean"] / N, 1.0)) if N else (lambda r: 0.0)
    p2_cut = sum(r["tokens"] * frac(r) for r in defer_rows)
    compressible_sub = sum(r["tokens"] for r in block_rows if r["action"] == "COMPRESS" and r["feasibility"] == FEAS["SUB"])
    compressible_all = sum(r["tokens"] for r in block_rows if r["action"] == "COMPRESS")
    oracle_tools = sum(r["tokens"] * frac(r) for r in tool_rows)
    oracle_blocks = sum(r["tokens"] for r in block_rows if r["feasibility"] == FEAS["SUB"]) * compress_realistic
    core = sum(r["tokens"] for r in block_rows if r["feasibility"] == FEAS["ANT"])

    def row(label, prefix, feas, note=""):
        delta = max(P0 - prefix, 0)
        return {"config": label, "prefix_tokens": round(prefix), "delta_per_call": round(delta),
                "delta_T_over_N_calls": round(delta * N), "pct_of_prefix": round(100 * delta / P0, 1) if P0 else None,
                "pct_of_session_sum_P": round(100 * (delta / P0) * fixed_share, 1) if P0 else None,
                "feasibility": feas, "note": note}
    P1 = P0 - p1_cut
    P1b = P1 - p1b_cut
    P2 = P1b - p2_cut
    return {"P0": row("P0 current", P0, "—"),
            "P1": row("P1 unused optional MCP servers removed", P1, FEAS["SUB"], f"servers: {sorted(unused_servers)}"),
            "P1b": row("P1b + never-used tool schemas disallowed (--disallowedTools / permissions.deny)", P1b, FEAS["SUB"], "validated: --disallowedTools strips the definition from the request"),
            "P2": row("P2 + late-used tools deferred to first use", P2, FEAS["ANT"], f"{len(defer_rows)} tools; needs Tool Search/defer_loading in the client (custom loop: API feature)"),
            "P3_conservative": row("P3 + static instructions compressed 30%", P2 - compressible_all * compress_conservative, FEAS["SUB"] + "+" + FEAS["ANT"]),
            "P3_realistic": row("P3 + static instructions compressed 50%", P2 - compressible_all * compress_realistic, FEAS["SUB"] + "+" + FEAS["ANT"]),
            "P4_oracle": row("P4 oracle: every tool at first use, user blocks −50%, core kept", P0 - oracle_tools - oracle_blocks, "mixed", f"core floor ≈ {round(core):,}"),
            "subscription_achievable": row("subscription-achievable: P1b + user-owned blocks −30%", P1b - compressible_sub * compress_conservative, FEAS["SUB"]),
            "gateway_achievable": row("gateway/custom-loop-achievable: P1b + defer via API Tool Search + blocks −50%", P2 - compressible_all * compress_realistic, FEAS["GW"],
                                      "defer_loading/Tool Search is an API feature a custom loop can use; Claude Code cannot yet"),
            "core_floor_tokens": round(core)}


# --------------------------------------------------------------------------- report
def build_report(body, sessions: list, *, env_label: str, real_startup: int = None,
                 reference_body: dict = None) -> dict:
    """body=None ⇒ evidence-only environment (no capture): items come from `lean_items` — reference
    sizes for the tools this environment actually LOADED (deferred names excluded) + listings measured
    from its transcripts — flagged as such."""
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    N = _median([s.calls for s in sessions]) or 0
    sum_P_med = _median([s.sum_P for s in sessions]) or 0
    startup_med = _median([s.startup_P for s in sessions]) or 0
    real_startup = real_startup or startup_med
    fixed_share = _median([min(s.startup_P * s.calls / s.sum_P, 1.0) for s in sessions if s.sum_P]) or 0.0
    if body is not None:
        items = itemize(body)
        attribution = "captured request body (measured)"
    else:
        items, notes = lean_items([s.transcript for s in sessions], reference_body)
        attribution = "no capture — " + "; ".join(notes)
    measured = sum(it.tokens_cl100k for it in items)
    factor = (real_startup / measured) if measured else CLAUDE_PER_CL100K
    by_cat = Counter()
    for it in items:
        by_cat[it.category] += it.tokens_cl100k
    residual_emp = real_startup - measured * CLAUDE_PER_CL100K
    tool_rows = audit_tools(items, sessions, factor)
    block_rows = audit_blocks(items, factor, sessions)
    dups = find_duplicates(items, factor)
    cf = counterfactuals(tool_rows, block_rows, real_startup, N, fixed_share)
    controllable = (sum(r["tokens"] for r in tool_rows if r["category"] == "mcp_tool" or r["action"] == "DISABLE?")
                    + sum(r["tokens"] for r in block_rows if r["feasibility"] == FEAS["SUB"]))
    unused = sum(r["tokens"] for r in tool_rows if r["sessions_used"] == 0)
    deferrable = sum(r["tokens"] * min(r["calls_before_first_use_mean"] / N, 1.0) for r in tool_rows) if N else 0
    return {
        "version": "prefix-doctor-v1", "environment": env_label, "source_commit": sha, "attribution": attribution,
        "tokenizer": {"name": TOKENIZER_NAME, "claude_per_cl100k_empirical": CLAUDE_PER_CL100K, "iqr": CLAUDE_PER_CL100K_IQR,
                      "proportional_factor_used": round(factor, 3)},
        "observation": {"sessions": len(sessions), "median_calls_per_session": N,
                        "median_startup_prefix_real": startup_med, "real_startup_used": real_startup,
                        "median_session_sum_P": sum_P_med, "fixed_prefix_share_of_sum_P_median": round(100 * fixed_share, 1)},
        "reconciliation": {"measured_cl100k_total": measured,
                           "by_category_cl100k": dict(by_cat.most_common()),
                           "by_category_estimated": {k: round(v * factor) for k, v in by_cat.most_common()},
                           "estimated_total_empirical_factor": round(measured * CLAUDE_PER_CL100K),
                           "residual_vs_real_empirical": round(residual_emp),
                           "residual_pct_of_real": round(100 * residual_emp / real_startup, 1) if real_startup else None,
                           "note": "proportional attribution (factor = real/measured) makes the residual 0 by construction; "
                                   "the empirical-factor residual is the honest unattributed remainder"},
        "totals": {"startup_prefix_tokens": real_startup, "fixed_prefix_share_pct": round(100 * fixed_share, 1),
                   "controllable_est": round(controllable), "non_controllable_est": round(real_startup - controllable),
                   "unused_tool_schema_tokens": round(unused), "deferrable_resident_tokens_per_call": round(deferrable),
                   "resident_token_turns_fixed": round(real_startup * N),
                   "cache_read_weighted_units": round(real_startup * N * 0.1)},
        "tools": tool_rows, "blocks": block_rows, "duplicates": dups, "counterfactuals": cf,
        "summary_actions": dict(Counter(r["action"] for r in tool_rows + block_rows)),
    }


def format_report(rep: dict) -> str:
    o, t, cf, rc = rep["observation"], rep["totals"], rep["counterfactuals"], rep["reconciliation"]
    L = [f"=== cr doctor --prefix v1 — {rep['environment']} (commit {rep['source_commit']}; {rep['tokenizer']['name']} ×{rep['tokenizer']['proportional_factor_used']}; {rep['attribution']}) ===",
         f"  startup prefix (real first call): {t['startup_prefix_tokens']:,} | sessions {o['sessions']} | median {o['median_calls_per_session']} calls | fixed prefix = {t['fixed_prefix_share_pct']}% of Σ P_t",
         f"  resident token-turns: {t['resident_token_turns_fixed']:,} | controllable ≈ {t['controllable_est']:,} | non-controllable ≈ {t['non_controllable_est']:,} | unused schemas {t['unused_tool_schema_tokens']:,} | deferrable/call {t['deferrable_resident_tokens_per_call']:,}",
         f"  reconciliation: measured cl100k {rc['measured_cl100k_total']:,} × {rep['tokenizer']['claude_per_cl100k_empirical']} = {rc['estimated_total_empirical_factor']:,} vs real {t['startup_prefix_tokens']:,} → residual {rc['residual_vs_real_empirical']:,} ({rc['residual_pct_of_real']}%)",
         "", f"  {'category':<22} {'cl100k':>8} {'estimated':>10}"]
    for k, v in rc["by_category_cl100k"].items():
        L.append(f"  {k:<22} {v:>8,} {rc['by_category_estimated'][k]:>10,}")
    L += ["", f"  {'tool (by wasted residency)':<40} {'tok':>7} {'used':>4} {'1st':>4} {'wasted':>9}  action    feasibility"]
    for r in rep["tools"][:14]:
        L.append(f"  {r['name'][:40]:<40} {r['tokens']:>7,} {r['sessions_used']:>4} {str(r['median_first_use_call'] or '-'):>4} {r['wasted_residency']:>9,}  {r['action']:<9} {r['feasibility']}")
    if rep["blocks"]:
        L += ["", "  static blocks"]
        for r in rep["blocks"]:
            L.append(f"  {r['name']:<14} {r['category']:<18} {r['tokens']:>7,}  {r['action']:<9} {r['feasibility']}")
    d = rep["duplicates"]
    L += ["", f"  duplicates: exact {len(d['exact'])} ({d['exact_dup_tokens']:,} tok), near {len(d['near'])} ({d['near_dup_tokens']:,} tok) / {d['paragraphs_scanned']} paragraphs",
          "", "  COUNTERFACTUALS — replayed over the observed call count; opportunities, NOT realized savings"]
    for k in ("P0", "P1", "P1b", "P2", "P3_conservative", "P3_realistic", "P4_oracle", "subscription_achievable", "gateway_achievable"):
        r = cf[k]
        L.append(f"  {r['config'][:66]:<66} {r['prefix_tokens']:>8,}  −{r['pct_of_prefix'] or 0:>5}% prefix  −{r['pct_of_session_sum_P'] or 0:>5}% Σ P_t  [{r['feasibility']}]")
    L.append(f"  core floor (Claude Code's own prompt + client-injected) ≈ {cf['core_floor_tokens']:,}")
    return "\n".join(L)


def run(cwd: str = None, *, capture: bool = True, sessions: int = 40, project_filter: str = None,
        body: dict = None, env_label: str = "this machine") -> dict:
    cwd = cwd or os.getcwd()
    if body is None and capture:
        body = capture_first_request(cwd)
    sess = collect_sessions(max_sessions=sessions, project_filter=project_filter)
    same = collect_sessions(max_sessions=sessions, project_filter=os.path.abspath(cwd).replace("/", "-"))
    real_first = _median([s.startup_P for s in same]) if same else None
    return build_report(body, sess, env_label=env_label, real_startup=real_first)
