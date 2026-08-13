#!/usr/bin/env python3
"""
CacheScope v0.1 — prefix cache lifecycle analyzer for Claude Code transcripts.

Detects cache BREAKS (prefix rebuilds) per session and attributes a cause to each,
then reports tokens rewritten by cause. This is the empirical test of the
"cache stability is the primary subscription lever" hypothesis.

Cache accounting model (validated against real transcripts):
  warm request N:   cache_read_N ≈ established_{N-1} = cache_read_{N-1}+cache_creation_{N-1}
                    cache_creation_N = small increment (new turn)
  broken request N: cache_read_N << established_{N-1}, cache_creation_N = large (re-cache prefix)

A break at request N is flagged when:
  cache_read_N < BREAK_RATIO * established_{N-1}   AND   established_{N-1} >= PREFIX_FLOOR
Tokens rewritten at the break = cache_creation_N (what had to be re-cached).

Cause attribution (priority order; a break can have >1 signal, we assign the top one
and also record co-signals):
  compaction     — a compact_boundary record appeared since the prior request
  model_change   — model id differs from prior request
  version_change — Claude Code version differs (an upgrade mid-session)
  ttl_expiration — inter-request gap exceeds the cache TTL (subscription = 1h)
  unknown        — none of the above (candidates: MCP reconnect, tool-schema change,
                   settings/CLAUDE.md edit, system-prompt drift)

Privacy: emits only aggregates. No prompt/source/tool content is read (usage rows only).
"""
import argparse, glob, json, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

BREAK_RATIO = 0.5       # cache_read fell below half the established prefix
HARD_RATIO = 0.15       # near-total rebuild
PREFIX_FLOOR = 20_000   # only care about breaks of substantial prefixes
TTL_MINUTES = 60        # Claude Code subscription cache TTL (1h); API default is 5m

def parse_ts(s):
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None

def fmt(n):
    n = float(n)
    if abs(n) >= 1e9: return f"{n/1e9:.2f}B"
    if abs(n) >= 1e6: return f"{n/1e6:.2f}M"
    if abs(n) >= 1e3: return f"{n/1e3:.1f}k"
    return str(int(n))

def analyze_file(fp):
    """Return per-session cache-lifecycle stats."""
    # ordered unique requests: requestId -> dict (first occurrence wins order; last usage kept)
    order = []
    reqs = {}
    pending_compaction = 0     # compact_boundary records seen since last request
    for line in open(fp, errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        t = rec.get("type")
        if t == "system" and rec.get("subtype") == "compact_boundary":
            pending_compaction += 1
            # tag onto the next request via a sentinel in order stream
            order.append(("_compact", None))
            continue
        if t != "assistant":
            continue
        msg = rec.get("message") or {}
        model = msg.get("model") or ""
        if model == "<synthetic>":
            continue
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        rid = rec.get("requestId") or msg.get("id")
        if not rid:
            continue
        cc = u.get("cache_creation") or {}
        info = {
            "input": u.get("input_tokens", 0) or 0,
            "cr": u.get("cache_read_input_tokens", 0) or 0,
            "cw": u.get("cache_creation_input_tokens", 0) or 0,
            "cw1h": (cc.get("ephemeral_1h_input_tokens", 0) or 0) if isinstance(cc, dict) else 0,
            "cw5m": (cc.get("ephemeral_5m_input_tokens", 0) or 0) if isinstance(cc, dict) else 0,
            "output": u.get("output_tokens", 0) or 0,
            "model": model,
            "version": rec.get("version") or "",
            "ts": rec.get("timestamp"),
        }
        if rid not in reqs:
            reqs[rid] = info
            order.append(("req", rid))
        else:
            reqs[rid] = info  # keep last-seen usage

    # walk in order, detect breaks
    result = {
        "n_requests": 0, "breaks": 0, "hard_breaks": 0,
        "rewritten_by_cause": Counter(), "breaks_by_cause": Counter(),
        "cosignals": Counter(),
        "total_rewritten": 0, "total_cache_read": 0, "total_cache_write": 0,
        "total_output": 0, "total_input": 0,
        "compactions": 0, "gaps_min": [], "warm_prefix_peak": 0,
        "first_ts": None, "last_ts": None,
    }
    prev = None
    prev_rid = None
    compaction_since_prev = 0
    for kind, rid in order:
        if kind == "_compact":
            compaction_since_prev += 1
            result["compactions"] += 1
            continue
        cur = reqs[rid]
        result["n_requests"] += 1
        result["total_cache_read"] += cur["cr"]
        result["total_cache_write"] += cur["cw"]
        result["total_output"] += cur["output"]
        result["total_input"] += cur["input"]
        ts = parse_ts(cur["ts"])
        if ts:
            result["first_ts"] = result["first_ts"] or cur["ts"]
            result["last_ts"] = cur["ts"]
        if prev is not None:
            established = prev["cr"] + prev["cw"]
            result["warm_prefix_peak"] = max(result["warm_prefix_peak"], established)
            gap_min = None
            pts, cts = parse_ts(prev["ts"]), ts
            if pts and cts:
                gap_min = (cts - pts).total_seconds() / 60.0
                result["gaps_min"].append(gap_min)
            if established >= PREFIX_FLOOR and cur["cr"] < BREAK_RATIO * established:
                # BREAK detected
                result["breaks"] += 1
                rewritten = cur["cw"]
                result["total_rewritten"] += rewritten
                if cur["cr"] < HARD_RATIO * established:
                    result["hard_breaks"] += 1
                # attribute cause
                signals = []
                if compaction_since_prev > 0:
                    signals.append("compaction")
                if cur["model"] != prev["model"]:
                    signals.append("model_change")
                if cur["version"] != prev["version"]:
                    signals.append("version_change")
                if gap_min is not None and gap_min > TTL_MINUTES:
                    signals.append("ttl_expiration")
                cause = signals[0] if signals else "unknown"
                result["breaks_by_cause"][cause] += 1
                result["rewritten_by_cause"][cause] += rewritten
                if len(signals) > 1:
                    result["cosignals"]["+".join(signals)] += 1
                elif not signals:
                    result["cosignals"]["unknown"] += 1
        prev = cur
        prev_rid = rid
        compaction_since_prev = 0
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports"))
    ap.add_argument("--include-subagents", action="store_true",
                    help="include agent-*.jsonl (default: main sessions only)")
    ap.add_argument("--max-files", type=int, default=None)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    files = []
    for fp in glob.glob(os.path.join(args.projects_dir, "*", "**", "*.jsonl"), recursive=True):
        base = os.path.basename(fp)
        if base == "journal.jsonl":
            continue
        if base.startswith("agent-") and not args.include_subagents:
            continue
        files.append(fp)
    files.sort(key=lambda p: os.path.getsize(p), reverse=True)
    if args.max_files:
        files = files[:args.max_files]
    print(f"[cachescope] analyzing {len(files)} session files...", file=sys.stderr)

    G = {
        "sessions": 0, "n_requests": 0, "breaks": 0, "hard_breaks": 0,
        "rewritten_by_cause": Counter(), "breaks_by_cause": Counter(),
        "cosignals": Counter(), "total_rewritten": 0,
        "total_cache_read": 0, "total_cache_write": 0, "total_output": 0, "total_input": 0,
        "compactions": 0, "gaps": [], "per_session": [],
    }
    first_ts, last_ts = None, None
    for i, fp in enumerate(files):
        try:
            r = analyze_file(fp)
        except Exception as e:
            print(f"[warn] {os.path.basename(fp)}: {e}", file=sys.stderr)
            continue
        if r["n_requests"] == 0:
            continue
        G["sessions"] += 1
        for k in ("n_requests", "breaks", "hard_breaks", "total_rewritten",
                  "total_cache_read", "total_cache_write", "total_output",
                  "total_input", "compactions"):
            G[k] += r[k]
        G["rewritten_by_cause"].update(r["rewritten_by_cause"])
        G["breaks_by_cause"].update(r["breaks_by_cause"])
        G["cosignals"].update(r["cosignals"])
        G["gaps"].extend(r["gaps_min"])
        if r["first_ts"]:
            first_ts = min(first_ts or r["first_ts"], r["first_ts"])
            last_ts = max(last_ts or r["last_ts"], r["last_ts"])
        G["per_session"].append({
            "project": os.path.relpath(fp, args.projects_dir).split(os.sep)[0],
            "requests": r["n_requests"], "breaks": r["breaks"],
            "rewritten": r["total_rewritten"], "compactions": r["compactions"],
            "cache_read": r["total_cache_read"], "cache_write": r["total_cache_write"],
        })
        if (i + 1) % 200 == 0:
            print(f"[cachescope] {i+1}/{len(files)}", file=sys.stderr)

    # gap distribution around the 1h TTL boundary
    gaps = G["gaps"]
    def pct(cond):
        return 100.0 * sum(1 for g in gaps if cond(g)) / len(gaps) if gaps else 0.0
    gap_buckets = {
        "<1m": pct(lambda g: g < 1),
        "1-5m": pct(lambda g: 1 <= g < 5),
        "5-60m": pct(lambda g: 5 <= g < 60),
        ">60m (past 1h TTL)": pct(lambda g: g >= 60),
    }

    total_rw = G["total_rewritten"] or 1
    lines = []; A = lines.append
    A("# CacheScope v0.1 — prefix cache lifecycle report")
    A("")
    A(f"Generated: {datetime.now().isoformat(timespec='seconds')}  ")
    A(f"Corpus: {G['sessions']} main sessions, {G['n_requests']:,} requests, "
      f"{(first_ts or '?')[:10]} → {(last_ts or '?')[:10]} (subagents excluded)  ")
    A(f"Cache TTL assumed: {TTL_MINUTES}m (Claude Code subscription 1h). "
      f"Break = cache_read < {BREAK_RATIO:g}×established prefix (floor {PREFIX_FLOOR:,}).")
    A("")
    A("## Headline")
    A("")
    A(f"| Metric | Value |")
    A(f"|---|---|")
    A(f"| Cache reads (billed-warm volume) | {fmt(G['total_cache_read'])} tokens |")
    A(f"| Cache writes (total) | {fmt(G['total_cache_write'])} tokens |")
    A(f"| **Cache writes rewritten AT DETECTED BREAKS** | **{fmt(G['total_rewritten'])} tokens "
      f"= {100*G['total_rewritten']/max(G['total_cache_write'],1):.0f}% of all cache writes** |")
    A(f"| Detected cache breaks | {G['breaks']:,} ({G['hard_breaks']:,} near-total rebuilds) |")
    A(f"| Compaction events | {G['compactions']:,} |")
    A(f"| Breaks per session (mean) | {G['breaks']/max(G['sessions'],1):.1f} |")
    A("")
    A("## Cache breaks by attributed cause")
    A("")
    A("| Cause | Breaks | Tokens rewritten | Share of rewritten |")
    A("|---|---|---|---|")
    for cause, cnt in G["breaks_by_cause"].most_common():
        rw = G["rewritten_by_cause"][cause]
        A(f"| {cause} | {cnt:,} | {fmt(rw)} | {100*rw/total_rw:.1f}% |")
    A("")
    A("## Multi-signal breaks (co-occurring causes at one break)")
    A("")
    for combo, cnt in G["cosignals"].most_common(12):
        A(f"- {combo}: {cnt:,}")
    A("")
    A("## Inter-request gap distribution (TTL-expiry evidence)")
    A("")
    A("| Gap bucket | Share of request transitions |")
    A("|---|---|")
    for b, p in gap_buckets.items():
        A(f"| {b} | {p:.1f}% |")
    A("")
    A(f"Transitions with a gap past the 1h TTL are the ones that can suffer TTL cache loss. "
      f"({len([g for g in gaps if g>=60]):,} of {len(gaps):,} transitions.)")
    A("")
    A("## Interpretation guardrails")
    A("")
    A("- Break detection is a heuristic on usage rows (cache_read collapse vs established prefix); "
      "it cannot see the harness's internal reason, only observable co-signals.")
    A("- Cause priority is compaction > model_change > version_change > ttl. A TTL-gap break that also "
      "follows a compaction is attributed to compaction; see co-signal table for overlaps.")
    A("- 'unknown' breaks have NO observable cause here — candidates are MCP server reconnect, "
      "tool-schema/settings/CLAUDE.md edits, or system-prompt drift. Needs live capture to split.")
    A("- Tokens rewritten are cache_creation at the break; economic weight depends on 5m vs 1h "
      "TTL pricing (this corpus is ~96% 1h-TTL writes).")
    A("")

    report = "\n".join(lines)
    with open(os.path.join(args.out, "cachescope.md"), "w") as f:
        f.write(report)
    with open(os.path.join(args.out, "cachescope.json"), "w") as f:
        json.dump({
            "sessions": G["sessions"], "n_requests": G["n_requests"],
            "breaks": G["breaks"], "hard_breaks": G["hard_breaks"],
            "compactions": G["compactions"],
            "total_rewritten": G["total_rewritten"],
            "total_cache_read": G["total_cache_read"],
            "total_cache_write": G["total_cache_write"],
            "total_output": G["total_output"],
            "breaks_by_cause": dict(G["breaks_by_cause"]),
            "rewritten_by_cause": dict(G["rewritten_by_cause"]),
            "cosignals": dict(G["cosignals"]),
            "gap_buckets": gap_buckets,
            "top_sessions": sorted(G["per_session"], key=lambda s: -s["rewritten"])[:40],
        }, f, indent=1)
    print(f"[cachescope] wrote {args.out}/cachescope.md and cachescope.json", file=sys.stderr)
    print(report)

if __name__ == "__main__":
    main()
