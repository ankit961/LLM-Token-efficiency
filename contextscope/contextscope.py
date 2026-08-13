#!/usr/bin/env python3
"""
ContextScope Phase-0 profiler (v0.1)

Analyzes Claude Code session transcripts (~/.claude/projects/**/*.jsonl) and
produces a dual-ledger token report:

  1. Context Occupancy Ledger  (attention view)  — exact, from per-request usage:
         O_r = input_tokens + cache_read + cache_creation
  2. Economic Ledger           (cost view)       — priced via pricing.json,
         never hard-coded ratios.

Waste is reported in strict evidence tiers:
  Tier A  measured redundancy      — hash-identical content delivered 2+ times
  Tier B  mechanically removable   — superseded/stale results still occupying window
  Tier C  NOT computed as a claim  — informational opportunity pools only

Privacy: this tool reads transcripts locally and emits ONLY aggregates
(categories, counts, token estimates, file paths of top offenders).
No prompt/source/tool-output content is written to the report.

Usage:
    python3 contextscope.py [--projects-dir ~/.claude/projects] [--out reports/]
                            [--since-days N] [--max-files N]
"""

import argparse
import hashlib
import json
import os
import re
import sys
import glob
import difflib
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------- tokens

CHARS_PER_TOKEN = 4.0          # estimate; labeled "estimated" in output
IMAGE_TOKENS = 1100            # rough per-image estimate, labeled

def est_tokens(text):
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN)

def sha1(text):
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]

# ---------------------------------------------------------------- pricing

def load_pricing(path):
    with open(path) as f:
        table = json.load(f)
    return table

def price_for(model, table):
    for entry in table.get("models", []):
        if entry["match"] in (model or ""):
            return entry, entry.get("verified", False)
    return table["default"], False

# ---------------------------------------------------------------- bash classification

TEST_RUNNERS = re.compile(
    r"^(pytest|python3?\s+-m\s+pytest|npm\s+(run\s+)?test|npx\s+jest|jest|vitest|"
    r"go\s+test|cargo\s+test|ctest|gradle\w*\s+test|mvn\s+test|rspec|phpunit|tox)\b")
BUILDERS = re.compile(
    r"^(make|cmake|ninja|cargo\s+build|npm\s+run\s+build|npx\s+tsc|tsc|gcc|g\+\+|clang|"
    r"xcodebuild|gradle|mvn|pio\b|platformio|idf\.py|arduino-cli|swift\s+build|python3?\s+setup\.py)\b")
FILE_DUMPERS = re.compile(r"^(cat|head|tail|less|more|bat)\b")
SED_PRINT = re.compile(r"^sed\s+(-n\s+)?['\"]?[0-9,]+p")
SEARCHERS = re.compile(r"^(grep|rg|ripgrep|ag|find|fd|ls|tree|wc|du|file|stat)\b")
GIT_CMD = re.compile(r"^git\b")
NET_CMD = re.compile(r"^(curl|wget|http|ping|ssh|scp|rsync)\b")
PY_INLINE_READ = re.compile(r"python3?\s+-c\s+.*open\(")

def _classify_segment(s):
    if FILE_DUMPERS.match(s) or SED_PRINT.match(s) or PY_INLINE_READ.search(s):
        return "shell_file_dump"
    if TEST_RUNNERS.match(s):
        return "shell_test"
    if BUILDERS.match(s):
        return "shell_build"
    if GIT_CMD.match(s):
        return "shell_git"
    if SEARCHERS.match(s):
        return "shell_search"
    if NET_CMD.match(s):
        return "shell_net"
    return "shell_other"

TRIVIAL_LEAD = re.compile(r"^(cd|echo|export|set|source|\.)\b")

def classify_bash(command):
    """Classify a bash command by its LEADING command; pipeline tails like
    `pio run | tail -50` must not turn a build into a file dump. Later
    segments are only consulted when the leading segment is trivial (cd/echo)
    or unclassified."""
    if not command:
        return "shell_other", ""
    segments = [s.strip() for s in re.split(r"&&|\|\||;|\|", command) if s.strip()]
    if not segments:
        return "shell_other", ""
    first_tok = segments[0].split()[0] if segments[0].split() else ""
    lead_idx = 0
    while lead_idx < len(segments) - 1 and TRIVIAL_LEAD.match(segments[lead_idx]):
        lead_idx += 1
    lead_cls = _classify_segment(segments[lead_idx])
    if lead_cls != "shell_other":
        return lead_cls, first_tok
    for seg in segments[lead_idx + 1:]:
        c = _classify_segment(seg)
        if c not in ("shell_other", "shell_file_dump", "shell_search"):
            return c, first_tok
    return "shell_other", first_tok

FAIL_PAT = re.compile(r"(FAILED|FAILURES|ERROR|Traceback|✗|✘|\bfailed\b|AssertionError)", re.I)

# ---------------------------------------------------------------- tool categorization

def categorize_tool(name):
    if name == "Read":
        return "file_read"
    if name in ("Grep", "Glob"):
        return "search_tool"
    if name == "Bash":
        return "shell"          # refined by classify_bash
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return "edit_result"
    if name in ("Agent", "Task", "Workflow"):
        return "subagent_result"
    if name in ("WebFetch", "WebSearch"):
        return "web"
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
                "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "ToolSearch",
                "ScheduleWakeup", "Monitor", "Skill", "SendUserFile", "Artifact"):
        return "agent_meta"
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else "mcp"
        if "chrome" in server.lower() or "browser" in server.lower():
            return "mcp_browser"
        return "mcp_other"
    return "other_tool"

# ---------------------------------------------------------------- record content helpers

def text_and_images_of_blocks(blocks):
    """Extract concatenated text + image count from a content field."""
    if blocks is None:
        return "", 0
    if isinstance(blocks, str):
        return blocks, 0
    texts, images = [], 0
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, str):
                texts.append(b)
            elif isinstance(b, dict):
                bt = b.get("type")
                if bt in ("text", "tool_reference"):
                    texts.append(str(b.get("text", "")))
                elif bt == "image":
                    images += 1
                elif bt == "document":
                    images += 5  # PDFs count as several pages of images, rough
    return "\n".join(texts), images

# ---------------------------------------------------------------- session analysis

class SessionResult:
    __slots__ = ("path", "project", "kind", "n_requests", "first_ts", "last_ts",
                 "usage", "cost", "cost_known", "models", "occupancy_tt",
                 "cat_tt", "cat_inject", "tierA_tt", "tierA_inject",
                 "tierB_tt", "tierA_events", "tierB_events",
                 "repeat_items", "file_tt", "rewrite", "opp_pools",
                 "attributed_inject", "total_inject_capacity", "tool_calls",
                 "bash_classes", "read_calls", "read_full", "read_partial",
                 "compactions", "edits_new_tokens", "writes_no_prior", "max_occ")

def analyze_session(fp, project, kind, pricing):
    """Single pass over one transcript file."""
    r = SessionResult()
    r.path = fp; r.project = project; r.kind = kind
    r.usage = Counter()          # input, cache_read, cw5, cw1h, output
    r.cost = 0.0
    r.cost_known = True
    r.models = Counter()
    r.cat_tt = Counter()         # token-turns by category (occupancy attribution)
    r.cat_inject = Counter()     # first-delivery tokens by category
    r.tierA_tt = 0; r.tierA_inject = 0
    r.tierB_tt = 0
    r.tierA_events = 0; r.tierB_events = 0
    r.repeat_items = Counter()   # (path_or_label) -> wasted inject tokens
    r.file_tt = Counter()        # file path -> token-turns
    r.rewrite = []               # (gen_tokens, changed_tokens) per Write w/ known prior
    r.opp_pools = Counter()      # informational Tier-C pools
    r.tool_calls = Counter()
    r.bash_classes = Counter()
    r.read_calls = 0; r.read_full = 0; r.read_partial = 0
    r.compactions = 0
    r.first_ts = None; r.last_ts = None
    r.edits_new_tokens = 0; r.writes_no_prior = 0; r.max_occ = 0
    user_text_turns = []

    # per-request usage dedup: rid -> (turn_idx, model, usage dict)
    requests = {}                # rid -> dict
    turn_order = []              # rids in order of first appearance
    # content events: (entry_turn, tokens, category, hash, label)
    events = []
    seg_starts = [0]             # turn indices where occupancy segments start
    tool_use_reg = {}            # tool_use_id -> (name, meta)
    seen_hashes = {}             # hash -> (entry_turn, tokens, label, [turns])
    last_by_key = {}             # (tool, args_hash) -> (turn, tokens, hash)
    path_content = {}            # file path -> last known text (bounded)
    MAX_PATH_CONTENT = 400_000

    def cur_turn():
        return len(turn_order)

    with open(fp, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get("type")
            ts = rec.get("timestamp")
            if ts:
                r.first_ts = r.first_ts or ts
                r.last_ts = ts

            if t == "system":
                if rec.get("subtype") == "compact_boundary":
                    r.compactions += 1
                    seg_starts.append(cur_turn())
                else:
                    sys_text = ""
                    if isinstance(rec.get("content"), str):
                        sys_text += rec["content"]
                    hac = rec.get("hookAdditionalContext")
                    if isinstance(hac, str):
                        sys_text += hac
                    if sys_text:
                        events.append((cur_turn(), est_tokens(sys_text),
                                       "hooks_system_msgs", None, None))
                continue

            if t == "attachment":
                att = rec.get("attachment")
                if att is not None:
                    try:
                        att_text = json.dumps(att, default=str)
                    except Exception:
                        att_text = str(att)
                    events.append((cur_turn(), est_tokens(att_text),
                                   "attachment_record", None, None))
                continue

            if t == "assistant":
                msg = rec.get("message") or {}
                model = msg.get("model") or ""
                if model == "<synthetic>":
                    continue
                usage = msg.get("usage")
                rid = rec.get("requestId") or msg.get("id")
                if usage and rid:
                    if rid not in requests:
                        turn_order.append(rid)
                        requests[rid] = {"turn": len(turn_order) - 1, "model": model}
                    # keep last-seen usage per rid (handles dup lines)
                    cc = usage.get("cache_creation") or {}
                    requests[rid]["usage"] = {
                        "input": usage.get("input_tokens", 0) or 0,
                        "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                        "cw5": cc.get("ephemeral_5m_input_tokens",
                                      usage.get("cache_creation_input_tokens", 0) or 0),
                        "cw1h": cc.get("ephemeral_1h_input_tokens", 0) or 0,
                        "output": usage.get("output_tokens", 0) or 0,
                    }
                    if isinstance(cc, dict) and cc.get("ephemeral_1h_input_tokens"):
                        requests[rid]["usage"]["cw5"] = cc.get("ephemeral_5m_input_tokens", 0) or 0
                # content: register tool_use, count assistant text
                content = msg.get("content")
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        bt = b.get("type")
                        if bt == "tool_use":
                            name = b.get("name", "?")
                            binput = b.get("input") or {}
                            tool_use_reg[b.get("id")] = (name, binput)
                            r.tool_calls[name] += 1
                            args_text = json.dumps(binput, default=str)[:200000]
                            args_cat = ("edit_write_args"
                                        if name in ("Write", "Edit", "MultiEdit", "NotebookEdit")
                                        else "tool_args")
                            events.append((cur_turn(), est_tokens(args_text),
                                           args_cat, None, None))
                            if name == "Bash":
                                cls, tok = classify_bash(str(binput.get("command", "")))
                                r.bash_classes[cls] += 1
                            elif name == "Read":
                                r.read_calls += 1
                                if binput.get("limit") or binput.get("offset"):
                                    r.read_partial += 1
                                else:
                                    r.read_full += 1
                            elif name == "Write" and binput.get("file_path"):
                                newc = str(binput.get("content", ""))
                                gen = est_tokens(newc)
                                prior = path_content.get(binput["file_path"])
                                if prior is not None and len(prior) < MAX_PATH_CONTENT and len(newc) < MAX_PATH_CONTENT:
                                    changed = diff_changed_tokens(prior, newc)
                                    r.rewrite.append((gen, changed))
                                else:
                                    r.writes_no_prior += 1
                                path_content[binput["file_path"]] = newc[:MAX_PATH_CONTENT]
                            elif name in ("Edit", "MultiEdit"):
                                r.edits_new_tokens += est_tokens(str(binput.get("new_string", "")))
                        elif bt == "text":
                            events.append((cur_turn(), est_tokens(b.get("text", "")),
                                           "assistant_text", None, None))
                        elif bt == "thinking":
                            # thinking IS re-sent while the tool-use loop continues,
                            # then dropped at the next real user turn
                            events.append((cur_turn(), est_tokens(b.get("thinking", "")),
                                           "thinking", None, None))
                continue

            if t == "user":
                msg = rec.get("message") or {}
                content = msg.get("content")
                is_meta = rec.get("isMeta", False)
                if isinstance(content, str):
                    cat = "rules_skill_injection" if is_meta else "user_text"
                    if not is_meta:
                        user_text_turns.append(cur_turn())
                    events.append((cur_turn(), est_tokens(content), cat, None, None))
                    continue
                if not isinstance(content, list):
                    continue
                for b in content:
                    if isinstance(b, str):
                        cat = "rules_skill_injection" if is_meta else "user_text"
                        if not is_meta:
                            user_text_turns.append(cur_turn())
                        events.append((cur_turn(), est_tokens(b), cat, None, None))
                        continue
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        cat = "rules_skill_injection" if is_meta else "user_text"
                        if not is_meta:
                            user_text_turns.append(cur_turn())
                        events.append((cur_turn(), est_tokens(b.get("text", "")), cat, None, None))
                    elif bt == "image":
                        events.append((cur_turn(), IMAGE_TOKENS, "user_attachment", None, None))
                    elif bt == "tool_result":
                        tuid = b.get("tool_use_id")
                        name, binput = tool_use_reg.get(tuid, ("?", {}))
                        text, images = text_and_images_of_blocks(b.get("content"))
                        if not text and rec.get("toolUseResult") is not None:
                            tur = rec.get("toolUseResult")
                            if isinstance(tur, str):
                                text = tur
                            else:
                                try:
                                    text = json.dumps(tur, default=str)
                                except Exception:
                                    text = str(tur)
                        tokens = est_tokens(text) + images * IMAGE_TOKENS
                        cat = categorize_tool(name)
                        label = None
                        if name == "Bash":
                            cmd = str(binput.get("command", ""))
                            cat, first_tok = classify_bash(cmd)
                            label = f"$ {first_tok}"
                            # Tier-C opportunity pools (informational only)
                            if cat == "shell_test" and tokens > 400 and not FAIL_PAT.search(text[-4000:]):
                                tail = "\n".join(text.splitlines()[-15:])
                                r.opp_pools["passing_test_verbosity"] += max(0, tokens - est_tokens(tail))
                            if cat == "shell_file_dump":
                                r.opp_pools["bash_file_dump"] += tokens
                            if cat == "shell_search" and tokens > 800:
                                r.opp_pools["oversized_search_dump"] += tokens
                        elif name == "Read":
                            fpath = str(binput.get("file_path", "?"))
                            label = fpath
                            path_content[fpath] = text[:MAX_PATH_CONTENT]
                        elif name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
                            label = str(binput.get("file_path", "?"))
                        elif name in ("Grep", "Glob"):
                            if tokens > 800:
                                r.opp_pools["oversized_search_dump"] += tokens
                        h = sha1(text) if len(text) >= 200 else None  # only track sizeable content
                        events.append((cur_turn(), tokens, cat, h, label))
                        # Tier A: identical re-delivery
                        if h:
                            if h in seen_hashes:
                                seen_hashes[h][3].append(cur_turn())
                                r.tierA_inject += tokens
                                r.tierA_events += 1
                                r.repeat_items[label or f"{name} result"] += tokens
                            else:
                                seen_hashes[h] = [cur_turn(), tokens, label, [cur_turn()]]
                            # Tier B: superseded stale result (same tool+args, new content)
                            akey = (name, sha1(json.dumps(binput, sort_keys=True, default=str)))
                            prev = last_by_key.get(akey)
                            if prev and prev[2] != h:
                                # previous result is now stale from this turn onward
                                events.append(("_stale", prev[0], prev[1], cur_turn()))
                            last_by_key[akey] = (cur_turn(), tokens, h)
                continue
            # other record types (queue-operation, ai-title, ...) carry no context

    # ---------------- finalize
    n = len(turn_order)
    r.n_requests = n
    if n == 0:
        return r

    verified_all = True
    for rid, req in requests.items():
        u = req.get("usage")
        if not u:
            continue
        r.usage["input"] += u["input"]
        r.usage["cache_read"] += u["cache_read"]
        r.usage["cw5"] += u["cw5"]
        r.usage["cw1h"] += u["cw1h"]
        r.usage["output"] += u["output"]
        r.max_occ = max(r.max_occ, u["input"] + u["cache_read"] + u["cw5"] + u["cw1h"])
        r.models[req["model"]] += 1
        p, verified = price_for(req["model"], pricing)
        verified_all = verified_all and verified
        r.cost += (u["input"] * p["input"] + u["cache_read"] * p["cache_read"]
                   + u["cw5"] * p["cache_write_5m"] + u["cw1h"] * p["cache_write_1h"]
                   + u["output"] * p["output"]) / 1e6
    r.cost_known = verified_all

    # segment end turn for a given entry turn
    seg_bounds = seg_starts + [n]
    def seg_end(turn):
        for i in range(len(seg_bounds) - 1):
            if seg_bounds[i] <= turn < seg_bounds[i + 1]:
                return seg_bounds[i + 1] - 1
        return n - 1

    import bisect
    user_text_turns.sort()

    def next_user_turn(turn):
        i = bisect.bisect_right(user_text_turns, turn)
        return user_text_turns[i] if i < len(user_text_turns) else n

    TOOL_RESULT_CATS = {"file_read", "shell_test", "shell_build", "shell_git",
                        "shell_file_dump", "shell_search", "shell_net", "shell_other",
                        "search_tool", "mcp_browser", "mcp_other", "subagent_result",
                        "edit_result", "other_tool", "web"}
    AGE_HORIZON = 50  # turns after which a tool result is likely dead weight

    total_attr = 0
    for ev in events:
        if ev[0] == "_stale":
            _, t_in, tokens, t_stale = ev
            end = seg_end(t_stale)
            if end >= t_stale:
                r.tierB_tt += tokens * (end - t_stale + 1)
                r.tierB_events += 1
            continue
        entry, tokens, cat, h, label = ev
        if tokens <= 0:
            continue
        entry_c = min(entry, n - 1)
        end = seg_end(entry_c)
        if cat == "thinking":
            # dropped at the next real user turn
            end = min(end, max(entry_c, next_user_turn(entry_c) - 1))
        turns_present = max(1, end - entry_c + 1)
        tt = tokens * turns_present
        r.cat_tt[cat] += tt
        r.cat_inject[cat] += tokens
        total_attr += tokens
        if cat in TOOL_RESULT_CATS and turns_present > AGE_HORIZON:
            r.opp_pools["aged_tool_results"] += tokens * (turns_present - AGE_HORIZON)
        if label and cat == "file_read":
            r.file_tt[label] += tt
    # Tier A token-turns: re-delivered copies occupy window from their entry
    for h, (t0, tokens, label, turns) in seen_hashes.items():
        for extra_turn in turns[1:]:
            end = seg_end(extra_turn)
            r.tierA_tt += tokens * max(1, end - extra_turn + 1)

    r.attributed_inject = total_attr
    r.total_inject_capacity = r.usage["input"] + r.usage["cw5"] + r.usage["cw1h"]
    # top repeats bookkeeping
    r.repeat_items = Counter(dict(r.repeat_items.most_common(20)))
    r.file_tt = Counter(dict(r.file_tt.most_common(20)))
    return r


def diff_changed_tokens(old, new):
    """Line-level diff; returns estimated tokens of genuinely changed lines."""
    try:
        old_l = old.splitlines()
        new_l = new.splitlines()
        if len(old_l) > 20000 or len(new_l) > 20000:
            return est_tokens(new)
        sm = difflib.SequenceMatcher(None, old_l, new_l, autojunk=True)
        changed_chars = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("replace", "insert"):
                changed_chars += sum(len(l) + 1 for l in new_l[j1:j2])
        return est_tokens("x" * changed_chars)
    except Exception:
        return est_tokens(new)

# ---------------------------------------------------------------- aggregation / report

def fmt(n):
    if n >= 1e9: return f"{n/1e9:.2f}B"
    if n >= 1e6: return f"{n/1e6:.2f}M"
    if n >= 1e3: return f"{n/1e3:.1f}k"
    return str(int(n))

def occupancy_of(u):
    return u["input"] + u["cache_read"] + u["cw5"] + u["cw1h"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports"))
    ap.add_argument("--since-days", type=int, default=None)
    ap.add_argument("--max-files", type=int, default=None)
    ap.add_argument("--pricing", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.json"))
    args = ap.parse_args()

    pricing = load_pricing(args.pricing)
    os.makedirs(args.out, exist_ok=True)

    files = []
    for fp in glob.glob(os.path.join(args.projects_dir, "*", "**", "*.jsonl"), recursive=True):
        base = os.path.basename(fp)
        if base == "journal.jsonl":
            continue
        kind = "subagent" if base.startswith("agent-") else "session"
        if args.since_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.since_days)
            if datetime.fromtimestamp(os.path.getmtime(fp), tz=timezone.utc) < cutoff:
                continue
        project = os.path.relpath(fp, args.projects_dir).split(os.sep)[0]
        files.append((fp, project, kind))
    files.sort(key=lambda x: os.path.getsize(x[0]), reverse=True)
    if args.max_files:
        files = files[:args.max_files]

    print(f"[contextscope] analyzing {len(files)} transcript files...", file=sys.stderr)

    results = []
    for i, (fp, project, kind) in enumerate(files):
        try:
            res = analyze_session(fp, project, kind, pricing)
            if res.n_requests > 0:
                results.append(res)
        except Exception as e:
            print(f"[warn] {os.path.basename(fp)}: {e}", file=sys.stderr)
        if (i + 1) % 100 == 0:
            print(f"[contextscope] {i+1}/{len(files)}", file=sys.stderr)

    # ---------- aggregate
    G = {"usage": Counter(), "cost": 0.0, "occupancy_tt": 0,
         "cat_tt": Counter(), "cat_inject": Counter(),
         "tierA_tt": 0, "tierA_inject": 0, "tierB_tt": 0,
         "tierA_events": 0, "tierB_events": 0,
         "repeat_items": Counter(), "file_tt": Counter(), "opp": Counter(),
         "tool_calls": Counter(), "bash": Counter(), "models": Counter(),
         "rewrite": [], "attributed": 0, "inject_capacity": 0,
         "read_calls": 0, "read_full": 0, "read_partial": 0, "compactions": 0,
         "n_requests": 0, "sessions": 0, "subagents": 0,
         "sub_usage": Counter(), "sub_cost": 0.0}
    first_ts, last_ts = None, None
    per_session_rows = []

    for res in results:
        occ = occupancy_of(res.usage)
        G["n_requests"] += res.n_requests
        if res.kind == "subagent":
            G["subagents"] += 1
            G["sub_usage"].update(res.usage)
            G["sub_cost"] += res.cost
        else:
            G["sessions"] += 1
        G["usage"].update(res.usage)
        G["cost"] += res.cost
        G["cat_tt"].update(res.cat_tt)
        G["cat_inject"].update(res.cat_inject)
        G["tierA_tt"] += res.tierA_tt
        G["tierA_inject"] += res.tierA_inject
        G["tierB_tt"] += res.tierB_tt
        G["tierA_events"] += res.tierA_events
        G["tierB_events"] += res.tierB_events
        G["repeat_items"].update(res.repeat_items)
        G["file_tt"].update(res.file_tt)
        G["opp"].update(res.opp_pools)
        G["tool_calls"].update(res.tool_calls)
        G["bash"].update(res.bash_classes)
        G["models"].update(res.models)
        G["rewrite"].extend(res.rewrite)
        G["attributed"] += res.attributed_inject
        G["inject_capacity"] += res.total_inject_capacity
        G["read_calls"] += res.read_calls
        G["read_full"] += res.read_full
        G["read_partial"] += res.read_partial
        G["compactions"] += res.compactions
        if res.first_ts:
            first_ts = min(first_ts or res.first_ts, res.first_ts)
            last_ts = max(last_ts or res.last_ts, res.last_ts)
        G["edits_new_tokens"] = G.get("edits_new_tokens", 0) + res.edits_new_tokens
        G["writes_no_prior"] = G.get("writes_no_prior", 0) + res.writes_no_prior
        per_session_rows.append({
            "file": os.path.basename(res.path), "project": res.project, "kind": res.kind,
            "requests": res.n_requests, "occupancy": occ,
            "peak_request_occupancy": res.max_occ, "cost_usd": round(res.cost, 4),
            "tierA_tt": res.tierA_tt, "tierB_tt": res.tierB_tt,
            "compactions": res.compactions})

    total_occ = occupancy_of(G["usage"])          # exact Σ O_r  (== total token-turns)
    u = G["usage"]

    # economic decomposition (exact tiers × pricing table)
    # weighted-average price per token of the whole corpus:
    total_cost = G["cost"]
    # price the A/B waste: duplicates are billed as new input (cw5) once, then cache_read
    # for their remaining turns; stale content sits at cache_read for its stale turns.
    # Use corpus-average tier prices derived from actual spend to avoid per-model loops:
    def safe_div(a, b): return a / b if b else 0.0
    # effective per-token prices actually paid, per tier:
    # (derive from pricing table weighted by model mix)
    eff = {"input": 0.0, "cache_read": 0.0, "cw5": 0.0, "cw1h": 0.0, "output": 0.0}
    tot_reqs = sum(G["models"].values()) or 1
    for model, cnt in G["models"].items():
        p, _ = price_for(model, pricing)
        w = cnt / tot_reqs
        eff["input"] += w * p["input"]
        eff["cache_read"] += w * p["cache_read"]
        eff["cw5"] += w * p["cache_write_5m"]
        eff["cw1h"] += w * p["cache_write_1h"]
        eff["output"] += w * p["output"]
    # blended cache-write price by the corpus' actual 5m/1h token mix
    cw_tok = u["cw5"] + u["cw1h"]
    eff_cw = ((u["cw5"] * eff["cw5"] + u["cw1h"] * eff["cw1h"]) / cw_tok) if cw_tok else eff["cw5"]
    tierA_cost = (G["tierA_inject"] * eff_cw
                  + max(0, G["tierA_tt"] - G["tierA_inject"]) * eff["cache_read"]) / 1e6
    tierB_cost = (G["tierB_tt"] * eff["cache_read"]) / 1e6
    addressable_cost = tierA_cost + tierB_cost

    boxed_econ_pct = 100 * safe_div(addressable_cost, total_cost)
    boxed_occ_pct = 100 * safe_div(G["tierA_tt"] + G["tierB_tt"], total_occ)

    # cache churn: cache-write volume not explained by new content.
    # New content streamed ≈ non-assistant attributed content + exact output
    # (assistant text/thinking/args re-enter as input but are already counted in output).
    assistant_side = sum(G["cat_inject"].get(c, 0) for c in
                         ("assistant_text", "thinking", "tool_args", "edit_write_args"))
    non_assistant_attr = G["attributed"] - assistant_side
    new_stream_total = u["input"] + u["cw5"] + u["cw1h"]
    cache_churn = max(0, new_stream_total - non_assistant_attr - u["output"])
    cache_churn_cost = cache_churn * eff_cw / 1e6

    # rewrite amplification
    rw = [(g, c) for (g, c) in G["rewrite"] if g > 0]
    rw_gen = sum(g for g, _ in rw)
    rw_changed = sum(c for _, c in rw)
    rw_amp = safe_div(rw_gen, max(rw_changed, 1))
    rw_unchanged = max(0, rw_gen - rw_changed)

    attribution_cov = 100 * safe_div(G["attributed"], G["inject_capacity"])

    # ---------- report
    lines = []
    A = lines.append
    A("# ContextScope Phase-0 Report")
    A("")
    A(f"Generated: {datetime.now().isoformat(timespec='seconds')}  ")
    A(f"Corpus: {G['sessions']} sessions + {G['subagents']} subagent transcripts, "
      f"{G['n_requests']:,} API requests, {first_ts[:10] if first_ts else '?'} → {last_ts[:10] if last_ts else '?'}  ")
    A(f"Models: " + ", ".join(f"{m} ({c:,} req)" for m, c in G["models"].most_common()))
    A("")
    A("## Headline numbers (strict tiers only)")
    A("")
    A(f"| Metric | Value | Quality |")
    A(f"|---|---|---|")
    A(f"| Total context occupancy Σ(input+cache_read+cache_write) | {fmt(total_occ)} tokens | exact (reconciled JSONL) |")
    A(f"| Total output tokens | {fmt(u['output'])} | exact (reconciled JSONL) |")
    A(f"| Total estimated spend | ${total_cost:,.2f} | estimated (pricing.json, unverified prices) |")
    A(f"| **Tier A+B — occupancy addressable** | {fmt(G['tierA_tt'] + G['tierB_tt'])} token-turns "
      f"= **{boxed_occ_pct:.1f}%** of all occupancy | A exact-hash, B reconstructed |")
    A(f"| **Cache churn** (cache-writes not explained by new content) | {fmt(cache_churn)} tokens, "
      f"~${cache_churn_cost:,.2f} | estimated (residual) |")
    A(f"| **Tier A+B — economic addressable** | ${addressable_cost:,.2f} "
      f"= **{boxed_econ_pct:.1f}%** of spend | estimated (pricing + cache-tier model) |")
    A(f"| Tier A duplicate deliveries | {G['tierA_events']:,} events, {fmt(G['tierA_inject'])} re-injected tokens | exact hash matches |")
    A(f"| Tier B stale superseded results | {G['tierB_events']:,} events, {fmt(G['tierB_tt'])} token-turns | reconstructed window model |")
    A("")
    A("## Dual ledgers")
    A("")
    A("### Attention view — occupancy token-turns by category (estimated attribution)")
    A("")
    A("| Category | Token-turns | Share of attributed |")
    A("|---|---|---|")
    cat_total = sum(G["cat_tt"].values()) or 1
    for cat, tt in G["cat_tt"].most_common():
        A(f"| {cat} | {fmt(tt)} | {100*tt/cat_total:.1f}% |")
    A("")
    unattr = max(0, total_occ - cat_total)
    A(f"Unattributed occupancy (system prompt, tool definitions, per-request wrappers): "
      f"~{fmt(unattr)} token-turns ({100*unattr/max(total_occ,1):.1f}% of total). "
      f"Transcripts cannot decompose this; needs OTel/live capture.")
    A("")
    A("### Cost view — exact usage tiers priced")
    A("")
    A("| Tier | Tokens | Est. cost |")
    A("|---|---|---|")
    A(f"| Uncached input | {fmt(u['input'])} | ${u['input']*eff['input']/1e6:,.2f} |")
    A(f"| Cache read | {fmt(u['cache_read'])} | ${u['cache_read']*eff['cache_read']/1e6:,.2f} |")
    A(f"| Cache write (5m) | {fmt(u['cw5'])} | ${u['cw5']*eff['cw5']/1e6:,.2f} |")
    A(f"| Cache write (1h) | {fmt(u['cw1h'])} | ${u['cw1h']*eff['cw1h']/1e6:,.2f} |")
    A(f"| Output | {fmt(u['output'])} | ${u['output']*eff['output']/1e6:,.2f} |")
    A("")
    A(f"Note how the two views rank differently: cache reads dominate billing volume "
      f"({fmt(u['cache_read'])} tokens) — the loop multiplier in action — while attention "
      f"waste concentrates in tool results and file reads.")
    A("")
    A("## The seven questions")
    A("")
    A("**1. Where do context tokens come from?** See attention table above.")
    A("")
    top_tools = ", ".join(f"{t} ({c:,})" for t, c in G["tool_calls"].most_common(8))
    A(f"**2. What stays in the window?** Mean occupancy per request "
      f"{fmt(safe_div(total_occ, G['n_requests']))} tokens; {G['compactions']} compaction events "
      f"across the corpus. Tool call mix: {top_tools}.")
    A("")
    A(f"**3. True cost after cache tiers?** ${total_cost:,.2f} total; effective blended "
      f"cache-read share of occupancy {100*safe_div(u['cache_read'], total_occ):.1f}%. "
      f"A token that enters context is re-read on every later request — the token-turn ledger "
      f"prices that explicitly.")
    A("")
    A(f"**4. What is repeated?** {G['tierA_events']:,} hash-identical re-deliveries "
      f"({fmt(G['tierA_inject'])} tokens re-injected). Top repeated items:")
    A("")
    for label, tok in G["repeat_items"].most_common(12):
        A(f"- {fmt(tok)} tokens: `{label}`")
    A("")
    A(f"**5. Which files/tools drive context growth?** Top files by token-turns:")
    A("")
    for label, tt in G["file_tt"].most_common(12):
        A(f"- {fmt(tt)} token-turns: `{label}`")
    A("")
    A(f"Bash output classes: " + ", ".join(f"{k}={v:,}" for k, v in G["bash"].most_common()))
    A(f"Read calls: {G['read_calls']:,} ({G['read_partial']:,} bounded with offset/limit, "
      f"{G['read_full']:,} unbounded full-file)")
    A("")
    A(f"**6. Unchanged regeneration in output?** Across {len(rw):,} full-file Write events with a known prior "
      f"({G.get('writes_no_prior', 0):,} more Writes had no observable prior): "
      f"{fmt(rw_gen)} generated tokens, {fmt(rw_changed)} actually changed → "
      f"**rewrite amplification {rw_amp:.1f}×**, ~{fmt(rw_unchanged)} unchanged regenerated tokens "
      f"(output-priced). Edits are already patch-style: {fmt(G.get('edits_new_tokens', 0))} tokens of "
      f"new_string across all Edit calls. Quality: estimated, line-diff.")
    A("")
    A(f"**7. Conservative lower bound on addressable overhead?** "
      f"**{boxed_occ_pct:.1f}% of occupancy / ${addressable_cost:,.2f} ({boxed_econ_pct:.1f}% of spend)** "
      f"from Tiers A+B alone — before any semantic optimization, reducers, or policy.")
    A("")
    A("## Informational opportunity pools (Tier C — NOT claims, needs A/B testing)")
    A("")
    A("| Pool | Est. tokens (single-injection) | What would address it |")
    A("|---|---|---|")
    pool_names = {"passing_test_verbosity": ("Passing-test verbose output beyond summary", "result reducers"),
                  "bash_file_dump": ("Files materialized via cat/head/tail/sed", "SemanticFS / Bash policy"),
                  "oversized_search_dump": ("Search/grep dumps >800 tokens", "ranked symbol summaries"),
                  "aged_tool_results": ("Tool results still occupying window >50 turns after delivery (token-turns)", "eviction / working-set mgmt")}
    for k, v in G["opp"].most_common():
        name, fix = pool_names.get(k, (k, "reducers / SemanticFS"))
        A(f"| {name} | {fmt(v)} | {fix} |")
    A("")
    A("## Measurement quality")
    A("")
    A("- Usage totals: reconciled — deduplicated by requestId (duplicate JSONL usage rows are real and were excluded).")
    A("- Category attribution: estimated — chars/4 tokenization, images ~1,100 tok. "
      f"Attributed content covers {attribution_cov:.0f}% of raw new-token capacity (input+cache-write); "
      f"much of the remainder is cache churn (prefix re-writes on TTL expiry/invalidation, reported above), "
      f"system prompt + tool definitions, and estimator error.")
    A("- Token-turns assume content stays in window until next compact_boundary or session end; "
      "microcompaction/eviction inside the harness is invisible to transcripts, so token-turn figures are upper bounds.")
    A("- Thinking blocks are attributed occupancy only until the next real user turn (they are dropped from "
      "the window at that point); exact thinking size is inside the exact output totals.")
    A("- Dollar figures: pricing.json is UNVERIFIED (models here are newer than the analyst's price knowledge). Edit pricing.json and re-run; token figures are unaffected.")
    A("- Subagent transcripts occupy separate context windows; included in totals, tagged in per-session data.")
    A("")

    report_md = "\n".join(lines)
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write(report_md)

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "headline": {
            "occupancy_total_tokens": total_occ,
            "output_total_tokens": u["output"],
            "est_total_cost_usd": round(total_cost, 2),
            "tierA_tt": G["tierA_tt"], "tierB_tt": G["tierB_tt"],
            "occupancy_addressable_pct": round(boxed_occ_pct, 2),
            "economic_addressable_usd": round(addressable_cost, 2),
            "economic_addressable_pct": round(boxed_econ_pct, 2),
            "cache_churn_tokens": cache_churn,
            "cache_churn_est_usd": round(cache_churn_cost, 2),
        },
        "usage": dict(G["usage"]),
        "cat_token_turns": dict(G["cat_tt"]),
        "cat_injected": dict(G["cat_inject"]),
        "opportunity_pools_tierC": dict(G["opp"]),
        "tool_calls": dict(G["tool_calls"]),
        "bash_classes": dict(G["bash"]),
        "models": dict(G["models"]),
        "rewrite": {"events": len(rw), "generated": rw_gen, "changed": rw_changed,
                    "amplification": round(rw_amp, 2)},
        "read_calls": {"total": G["read_calls"], "full": G["read_full"], "partial": G["read_partial"]},
        "sessions": sorted(per_session_rows, key=lambda r: -r["occupancy"])[:100],
    }
    with open(os.path.join(args.out, "report.json"), "w") as f:
        json.dump(payload, f, indent=1)

    print(f"[contextscope] wrote {args.out}/report.md and report.json", file=sys.stderr)
    # terminal summary
    print(report_md.split("## Dual ledgers")[0])

if __name__ == "__main__":
    main()
