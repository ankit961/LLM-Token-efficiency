#!/usr/bin/env python3
"""B7 — offline policy replay: what would each retirement-scheduling policy have cost, in dollars,
on the same sessions? Zero quota; grounded in the exact-calibrated prefix-cache model
(`contextruntime.cachemodel`, 0.0% error on 11/12 B6 native sessions).

Base timeline = a NATIVE session (observed per-call P_t and timestamps — the same counterfactual
grammar as B3/joint replay). Overlays, all derived from that same transcript:
  - retirement stream: superseded + cold-tail objects, lag 5 (gateway-equivalent safety), object
    sizes cl100k×1.74 to the API-token axis (the established request-accounting factor);
  - thinking stream: per-call retained thinking = max(out_t − 1.51×cl100k(visible_t), 0).

Policies differ ONLY in WHEN mutations fire:
  native     no mutations (prices to the observed session exactly, by calibration)
  unaligned  the B6 gateway behavior: retire in batches every 10 calls, strip thinking keep-1
             EVERY call — maximal residency saving, maximal cache damage
  cold_gap   fire pending mutations only when the cache is already cold (session start or the
             1h TTL expired during an idle gap) — zero marginal cache damage by construction
  gated      cold_gap + a break-even trigger: fire when 0.1·pending·Ê_remaining ≥ 1.9·suffix
             (Ê fixed at 8 calls — deliberately simple and preregistered)
  oracle     gated with the TRUE remaining-call count (upper bound for any break-even rule)

Each policy reports absolute BITE/$ (read 0.1 / 1h-write 2.0 / out 5.0 of base input), Δ$ vs
native, and residency Σ P′ vs native. The unaligned policy doubles as validation: its modeled Δ$
on N-arm timelines should reconcile with B6's LIVE T-vs-N dollar delta (−2.5% CLI / −2.8% list).
"""
from __future__ import annotations

import json
import sys

from contextruntime.cachemodel import (PrefixCacheSim, bite, extract_calls, load_b6_sessions,
                                       usd, WRITE_MULT_1H, READ_MULT)
from corpus.b3_context_retirement import assign_obsolescence, parse_context_objects
from corpus.joint_stack_replay import FACTOR, FACTOR_OUT, visible_out_per_call

LAG = 5
BATCH = 10
THINK_MIN = 100     # output-factor noise floor: smaller estimates are not real thinking blocks
E_REMAINING = 8          # fixed, conservative expected-remaining-calls for the gated policy


def mutation_streams(transcript_path, n_calls):
    """(retire_events, think) on the API-token axis.
    retire_events: list of {eligible, turn, tokens} — eligible = first call the retirement is SAFE
    (obsolescence + lag, the gateway rule); turn = where the object lives (the edit depth).
    think[t] = retained thinking generated at call t (1-indexed)."""
    objects, total_turns, _usage = parse_context_objects(transcript_path)
    assign_obsolescence(objects, total_turns)
    events = []
    for o in objects:
        obs = o.get("obsolete_turn") or o.get("tail_turn")
        if obs is None:
            continue
        eligible = obs + LAG
        if eligible <= total_turns:
            events.append({"eligible": eligible, "turn": o["turn"],
                           "tokens": int(round(o["size"] * FACTOR))})
    vis = visible_out_per_call(transcript_path)
    calls = extract_calls(transcript_path)
    think = [0] * (n_calls + 1)
    for t in range(1, min(n_calls, len(vis), len(calls)) + 1):
        est = max(calls[t - 1].out - int(round(FACTOR_OUT * vis[t - 1])), 0)
        think[t] = est if est >= THINK_MIN else 0    # sub-floor estimates are factor noise, not blocks
    return sorted(events, key=lambda e: e["eligible"]), think


def run_policy(calls, retire_events, think, policy, *, warm=None, e_remaining=E_REMAINING,
               profile=None):
    """Price one session under one policy. Returns totals + the residency stream.
    `profile` (contextruntime.providers.ProviderProfile) sets the cache TTL, the break-even
    constants, and the pricing — omitted = the live-validated anthropic-1h constants."""
    read_mult = profile.read_mult if profile else READ_MULT
    write_mult = profile.write_mult if profile else WRITE_MULT_1H
    out_mult = profile.out_mult if profile else 5.0
    n = len(calls)
    warm = calls[0].read if warm is None else warm
    sim = PrefixCacheSim(ttl_s=profile.ttl_s if profile else 3600.0)
    t0 = calls[0].ts or 0.0
    if warm:
        sim.prefixes.append([warm, t0 + sim.ttl_s])

    pending = []                     # retire events eligible but not fired
    ev_i = 0
    retired_cum = 0
    stripped_cum = 0
    strip_frontier = 0               # thinking stripped through this call index (aligned policies)
    Phist = [warm]                   # Phist[k] = request size of call k under this policy (k 1-based)
    tot = {"read": 0, "creation": 0, "input": 0, "out": 0, "sum_P": 0, "fires": 0,
           "retired_tokens": 0, "stripped_tokens": 0}

    for t in range(1, n + 1):
        c = calls[t - 1]
        ts = c.ts or 0.0
        while ev_i < len(retire_events) and retire_events[ev_i]["eligible"] <= t:
            pending.append(retire_events[ev_i])
            ev_i += 1

        edit_depth = None            # earliest call whose content this request rewrites
        fire = False
        if policy == "native":
            pass
        elif policy == "unaligned":
            fire = pending and (t % BATCH == 0 or t == n)
            if t >= 3 and think[t - 2]:              # keep-1: strip call t-2's thinking at call t
                stripped_cum += think[t - 2]
                tot["stripped_tokens"] += think[t - 2]
                edit_depth = t - 2
        else:                                        # aligned family
            free = sim.cold(ts)
            if policy in ("gated", "oracle") and pending and not free:
                pend_tok = sum(e["tokens"] for e in pending)
                pend_think = sum(think[s] for s in range(strip_frontier + 1, max(t - 1, 1)))
                depth = min(min(e["turn"] + 1 for e in pending),
                            strip_frontier + 1 if pend_think else n + 1)
                suffix = Phist[-1] - Phist[min(depth - 1, len(Phist) - 1)]
                remaining = (n - t) if policy == "oracle" else e_remaining
                free = read_mult * (pend_tok + pend_think) * remaining >= (write_mult - read_mult) * suffix
            fire = free and (bool(pending) or strip_frontier < t - 2)

        if fire:
            tot["fires"] += 1
            depths = []
            if pending:
                depths.append(min(e["turn"] + 1 for e in pending))
                fired_tok = sum(e["tokens"] for e in pending)
                retired_cum += fired_tok
                tot["retired_tokens"] += fired_tok
                pending = []
            if policy != "unaligned" and strip_frontier < t - 2:   # aligned: strip thinking with the batch
                pend_think = sum(think[s] for s in range(strip_frontier + 1, t - 1))
                if pend_think:
                    depths.append(strip_frontier + 1)
                    stripped_cum += pend_think
                    tot["stripped_tokens"] += pend_think
                strip_frontier = t - 2
            if depths:
                d = min(depths)
                edit_depth = d if edit_depth is None else min(edit_depth, d)

        P_prime = max(c.P - retired_cum - stripped_cum, warm)
        unchanged = None
        if edit_depth is not None:
            unchanged = Phist[min(max(edit_depth - 1, 0), len(Phist) - 1)]
        read, creation = sim.request(ts, P_prime, unchanged)
        Phist.append(P_prime)
        tot["read"] += read
        tot["creation"] += creation
        tot["input"] += c.input
        tot["out"] += c.out
        tot["sum_P"] += P_prime

    tot["bite"] = bite(tot["read"], tot["creation"], tot["input"], tot["out"],
                       write_mult=write_mult, read_mult=read_mult, out_mult=out_mult)
    tot["usd"] = usd(tot["bite"])
    return tot


POLICIES = ("native", "unaligned", "cold_gap", "gated", "oracle")


def calibrate_t_arm(results_path="corpus/analysis/b6-live-results.json",
                    gw_dir="corpus/analysis/b6-gw-logs"):
    """Validate the EDIT branch of the cache model against the 12 live ENFORCE sessions: feed each
    T-arm's OBSERVED P stream through the simulator with that session's actual mutation schedule
    (keep-1 strip whenever the prior-prior call had thinking; retirement batches at the
    gateway-logged turns, edit depth = earliest pending object) and compare predicted vs observed
    cache read/creation. This is the honesty bound for every policy number built on the model."""
    rows = []
    for tid, key, tp in load_b6_sessions(results_path, "T"):
        calls = extract_calls(tp)
        n = len(calls)
        events, think = mutation_streams(tp, n)
        gw_path = f"{gw_dir}/{tid}-{key}.gw.jsonl"
        batch_turns = set()
        try:
            for line in open(gw_path):
                r = json.loads(line)
                if r.get("applied"):
                    batch_turns.add(r["turn"])
        except FileNotFoundError:
            continue
        sim = PrefixCacheSim()
        warm = calls[0].read
        t0 = calls[0].ts or 0.0
        if warm:
            sim.prefixes.append([warm, t0 + sim.ttl_s])
        pending, ev = [], 0
        Ph = [warm]
        pred_read = pred_cre = obs_read = obs_cre = 0
        for t in range(1, n + 1):
            c = calls[t - 1]
            while ev < len(events) and events[ev]["eligible"] <= t:
                pending.append(events[ev])
                ev += 1
            depth = None
            if t >= 3 and think[t - 2]:
                depth = t - 2
            if t in batch_turns and pending:
                d = min(e["turn"] + 1 for e in pending)
                depth = d if depth is None else min(depth, d)
                pending = []
            unchanged = None if depth is None else Ph[min(max(depth - 1, 0), len(Ph) - 1)]
            r, w = sim.request(c.ts or 0.0, c.P, unchanged)     # observed P: mutations already inside
            Ph.append(c.P)
            pred_read += r
            pred_cre += w
            obs_read += c.read
            obs_cre += c.creation
        rows.append({"task": tid, "rep": key,
                     "read_err_pct": round(100 * (pred_read - obs_read) / obs_read, 1) if obs_read else None,
                     "creation_err_pct": round(100 * (pred_cre - obs_cre) / obs_cre, 1) if obs_cre else None,
                     "pred_creation": pred_cre, "obs_creation": obs_cre})
    return rows


def replay_sessions(sessions, *, label, calib_max_err=20.0):
    from contextruntime.cachemodel import calibrate_append_only
    rows = []
    for tid, key, tp in sessions:
        calls = extract_calls(tp)
        if len(calls) < 3:
            continue
        events, think = mutation_streams(tp, len(calls))
        row = {"task": tid, "rep": key, "calls": len(calls)}
        cal = calibrate_append_only(calls)
        row["calib_err_pct"] = cal.get("creation_err_pct") if cal else None
        for pol in POLICIES:
            row[pol] = run_policy(calls, events, think, pol)
        rows.append(row)

    def _agg(rs):
        out = {}
        base_b = sum(r["native"]["bite"] for r in rs)
        base_P = sum(r["native"]["sum_P"] for r in rs)
        for pol in POLICIES:
            b = sum(r[pol]["bite"] for r in rs)
            P = sum(r[pol]["sum_P"] for r in rs)
            per = sorted(100 * (r[pol]["bite"] / r["native"]["bite"] - 1) for r in rs)
            out[pol] = {
                "usd": round(usd(b), 4),
                "usd_delta_pct": round(100 * (b / base_b - 1), 2),
                "usd_delta_median_pct": round(per[len(per) // 2], 2) if per else None,
                "residency_delta_pct": round(100 * (P / base_P - 1), 2),
                "fires": sum(r[pol]["fires"] for r in rs),
                "retired_tokens": sum(r[pol]["retired_tokens"] for r in rs),
                "stripped_tokens": sum(r[pol]["stripped_tokens"] for r in rs),
            }
        return out

    agg = {"label": label, "n_sessions": len(rows), "policies": _agg(rows)}
    # robustness cut: only sessions where the model reproduces the NATIVE billing within the bound
    good = [r for r in rows if r["calib_err_pct"] is not None and abs(r["calib_err_pct"]) <= calib_max_err]
    agg["n_well_calibrated"] = len(good)
    agg["policies_well_calibrated"] = _agg(good) if good else None
    return {"aggregate": agg, "sessions": rows}


def main(out_path=None, session_list=None):
    if session_list:
        import os
        paths = [r["path"] for r in json.load(open(session_list))]
        sessions = [(os.path.basename(os.path.dirname(p))[-28:], f"s{i}", p) for i, p in enumerate(paths)]
        label = f"interactive sessions ({session_list})"
        from contextruntime.cachemodel import calibrate_append_only
        errs = []
        for _, _, tp in sessions:
            try:
                c = calibrate_append_only(extract_calls(tp))
            except Exception:      # noqa: BLE001
                continue
            if c and c.get("creation_err_pct") is not None:
                errs.append(abs(c["creation_err_pct"]))
        errs.sort()
        if errs:
            print(f"calibration on this set (append-only + TTL model vs observed billing): "
                  f"median |creation err| {errs[len(errs)//2]:.1f}%  p90 {errs[int(len(errs)*0.9)]:.1f}%  n={len(errs)}")
        res = replay_sessions(sessions, label=label)
    else:
        sessions = load_b6_sessions("corpus/analysis/b6-live-results.json", "N")
        res = replay_sessions(sessions, label="B6 native timelines (headless, back-to-back)")
    agg = res["aggregate"]
    for title, block in (("all", agg["policies"]),
                         (f"well-calibrated (|err|<=20%, n={agg['n_well_calibrated']})",
                          agg.get("policies_well_calibrated"))):
        if not block:
            continue
        print(f"== {agg['label']} — {agg['n_sessions']} sessions — {title} ==")
        print(f"{'policy':<10} {'$':>9} {'Δ$ pooled':>10} {'Δ$ median':>10} {'Δresidency':>11} {'fires':>6} {'retired':>10} {'stripped':>11}")
        for pol in POLICIES:
            p = block[pol]
            print(f"{pol:<10} {p['usd']:>9.3f} {p['usd_delta_pct']:>9.2f}% {p['usd_delta_median_pct']:>9.2f}% "
                  f"{p['residency_delta_pct']:>10.2f}% {p['fires']:>6} {p['retired_tokens']:>10,} {p['stripped_tokens']:>11,}")
    if out_path:
        json.dump(res, open(out_path, "w"), indent=1)
        print("wrote", out_path)
    return res


def provider_sensitivity(session_list=None):
    """The generic-framework demonstration: the SAME sessions and the SAME mutation streams,
    priced and scheduled under each provider profile. Only `anthropic-1h` is live-validated;
    the others are modeling presets (see contextruntime/providers.py) — this table shows how the
    break-even constant alone flips the scheduler's behavior and the verdict on mid-session
    retirement."""
    from contextruntime.providers import PROFILES
    if session_list:
        import os
        paths = [r["path"] for r in json.load(open(session_list))]
        sessions = [(os.path.basename(os.path.dirname(pp))[-28:], f"s{i}", pp) for i, pp in enumerate(paths)]
        label = "interactive"
    else:
        sessions = load_b6_sessions("corpus/analysis/b6-live-results.json", "N")
        label = "B6 headless"
    streams = []
    for tid, key, tp in sessions:
        try:
            calls = extract_calls(tp)
        except Exception:      # noqa: BLE001
            continue
        if len(calls) < 3:
            continue
        streams.append((calls, *mutation_streams(tp, len(calls))))
    print(f"== provider sensitivity — {label}, {len(streams)} sessions, same mutation streams ==")
    print(f"{'profile':<16} {'break-even':>10} {'unaligned Δ$':>13} {'gated Δ$':>10} {'gated Δres':>11} {'gated fires':>11}")
    out = {}
    for name, prof in PROFILES.items():
        nat = una = gat = 0.0
        natP = gatP = 0
        fires = 0
        for calls, events, think in streams:
            nat += run_policy(calls, events, think, "native", profile=prof)["bite"]
            una += run_policy(calls, events, think, "unaligned", profile=prof)["bite"]
            g = run_policy(calls, events, think, "gated", profile=prof)
            gat += g["bite"]
            fires += g["fires"]
            natP += run_policy(calls, events, think, "native", profile=prof)["sum_P"]
            gatP += g["sum_P"]
        out[name] = {"unaligned_pct": round(100 * (una / nat - 1), 2),
                     "gated_pct": round(100 * (gat / nat - 1), 2),
                     "gated_res_pct": round(100 * (gatP / natP - 1), 2), "gated_fires": fires,
                     "validated": prof.validated}
        tag = "" if prof.validated else "  (modeling preset — unvalidated)"
        print(f"{name:<16} {prof.break_even_reads:>10.1f} {out[name]['unaligned_pct']:>12.2f}% "
              f"{out[name]['gated_pct']:>9.2f}% {out[name]['gated_res_pct']:>10.2f}% {fires:>11}{tag}")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "providers":
        provider_sensitivity(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else None,
             session_list=sys.argv[2] if len(sys.argv) > 2 else None)
