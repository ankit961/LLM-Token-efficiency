#!/usr/bin/env python3
"""
CacheScope v0.2 — LINEAGE-AWARE prefix cache lifecycle analyzer.

Fixes the v0.1 objections raised by adversarial review:
 - A2 lineage contamination: use the true parentUuid DAG parent for the established
   prefix, not positional request N-1. Detect FORK/BRANCH (parent is not the latest
   request) so edited-and-regenerated turns aren't mislabeled "unknown".
 - A3 honest waste: geometric recache = min(cache_creation, max(0, established-cache_read)),
   which separates prefix-REBUILD tokens from first-time-cached NEW-suffix tokens.
 - A5 avoidability: cross-tab every cause against gap>60min → avoidable / unavoidable / artifact.

Reads usage rows + record lineage only. Emits aggregates. No content.
"""
import argparse, glob, json, os, sys
from collections import Counter
from datetime import datetime

BREAK_RATIO = 0.5
FLOOR = 20_000
TTL_MIN = 60

def pts(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except: return None

def fmt(n):
    n=float(n)
    if abs(n)>=1e9: return f"{n/1e9:.2f}B"
    if abs(n)>=1e6: return f"{n/1e6:.2f}M"
    if abs(n)>=1e3: return f"{n/1e3:.1f}k"
    return str(int(n))

def analyze(fp):
    # Pass 1: index every record by uuid → (parentUuid, kind, payload)
    # kind: 'asst' (usage-bearing), 'compact', 'other'
    node = {}          # uuid -> dict(parent, kind, info)
    asst_order = []    # uuids of usage-bearing assistant reqs, in file order
    seen_rid = {}      # requestId -> uuid (dedup: keep FIRST uuid, update its usage to last row)
    for line in open(fp, errors="replace"):
        line=line.strip()
        if not line: continue
        try: rec=json.loads(line)
        except: continue
        uuid=rec.get("uuid")
        t=rec.get("type")
        parent=rec.get("parentUuid")
        if t=="system" and rec.get("subtype")=="compact_boundary":
            if uuid: node[uuid]=dict(parent=parent, kind="compact", info=None)
            continue
        if t=="assistant":
            m=rec.get("message") or {}
            if m.get("model")=="<synthetic>":
                if uuid: node[uuid]=dict(parent=parent, kind="other", info=None)
                continue
            u=m.get("usage")
            if not isinstance(u,dict):
                if uuid: node[uuid]=dict(parent=parent, kind="other", info=None)
                continue
            rid=rec.get("requestId") or m.get("id")
            cc=u.get("cache_creation") or {}
            info=dict(
                rid=rid, uuid=uuid, parent=parent,
                cr=u.get("cache_read_input_tokens",0) or 0,
                cw=u.get("cache_creation_input_tokens",0) or 0,
                model=m.get("model") or "", version=rec.get("version") or "",
                ts=rec.get("timestamp"),
                sidechain=bool(rec.get("isSidechain")),
            )
            if rid and rid in seen_rid:
                # duplicate streaming row: keep the FIRST row's lineage+usage as canonical
                # (usage is identical across dup rows); just register this uuid as an alias.
                # Do NOT overwrite the canonical's parent — that corrupts the lineage walk.
                first_uuid=seen_rid[rid]
                node[uuid]=dict(parent=parent, kind="alias", info=first_uuid)
                continue
            if rid: seen_rid[rid]=uuid
            node[uuid]=dict(parent=parent, kind="asst", info=info)
            asst_order.append(uuid)
        else:
            if uuid: node[uuid]=dict(parent=parent, kind="other", info=None)

    # helper: from a uuid, walk parent chain to nearest usage-bearing assistant ancestor,
    # noting whether a compaction boundary was crossed.
    def lineage_parent(start_parent):
        cur=start_parent; crossed_compact=False; steps=0
        while cur is not None and steps<100000:
            steps+=1
            nd=node.get(cur)
            if nd is None:
                return None, crossed_compact
            if nd["kind"]=="compact":
                crossed_compact=True
                cur=nd["parent"]; continue
            if nd["kind"]=="alias":
                cur=nd["info"]; continue
            if nd["kind"]=="asst":
                return cur, crossed_compact
            cur=nd["parent"]
        return None, crossed_compact

    R=dict(n=0, breaks=0, hard=0, forks=0, fork_breaks=0,
           rewritten=0, recache=0, newcache=0,
           total_cw=0, total_cr=0,
           by_cause=Counter(), recache_by_cause=Counter(),
           avoidability=Counter(), cosig=Counter(),
           gaps=[], first_ts=None, last_ts=None)

    latest_uuid=None  # most recent usage-bearing asst in file order (for fork detection)
    for uuid in asst_order:
        cur=node[uuid]["info"]
        R["n"]+=1
        R["total_cw"]+=cur["cw"]; R["total_cr"]+=cur["cr"]
        ts=pts(cur["ts"])
        if cur["ts"]:
            R["first_ts"]=R["first_ts"] or cur["ts"]; R["last_ts"]=cur["ts"]
        puuid, crossed_compact = lineage_parent(cur["parent"])
        is_fork = (puuid is not None and latest_uuid is not None and puuid!=latest_uuid)
        if is_fork: R["forks"]+=1
        if puuid is not None:
            p=node[puuid]["info"]
            established=p["cr"]+p["cw"]
            gap=None
            a,b=pts(p["ts"]),ts
            if a and b:
                gap=(b-a).total_seconds()/60; R["gaps"].append(gap)
            if established>=FLOOR and cur["cr"]<BREAK_RATIO*established:
                R["breaks"]+=1
                rewritten=cur["cw"]
                recache=min(cur["cw"], max(0, established-cur["cr"]))
                newc=max(0, cur["cw"]-recache)
                R["rewritten"]+=rewritten; R["recache"]+=recache; R["newcache"]+=newc
                if cur["cr"]<0.15*established: R["hard"]+=1
                if is_fork: R["fork_breaks"]+=1
                # cause signals
                sig=[]
                if crossed_compact: sig.append("compaction")
                if is_fork: sig.append("fork")
                if cur["model"]!=p["model"]: sig.append("model_change")
                if cur["version"]!=p["version"]: sig.append("version_change")
                ttl = (gap is not None and gap>TTL_MIN)
                if ttl: sig.append("ttl_expiration")
                cause = sig[0] if sig else "unknown"
                R["by_cause"][cause]+=1
                R["recache_by_cause"][cause]+=recache
                R["cosig"]["+".join(sig) if sig else "unknown"]+=1
                # avoidability bucket
                if ttl and cause=="ttl_expiration":
                    R["avoidability"]["unavoidable_ttl_idle"]+=recache
                elif cause=="fork":
                    R["avoidability"]["artifact_or_userfork"]+=recache
                elif cause in ("model_change","version_change") and ttl:
                    R["avoidability"]["unavoidable_ttl_coincident"]+=recache
                elif cause in ("model_change","version_change"):
                    R["avoidability"]["addressable_config"]+=recache
                elif cause=="compaction":
                    R["avoidability"]["compaction"]+=recache
                else:  # unknown, short-gap, no fork
                    R["avoidability"]["addressable_midwork"]+=recache
        latest_uuid=uuid
    return R

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),"reports"))
    ap.add_argument("--max-files", type=int, default=None)
    args=ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    files=[f for f in glob.glob(os.path.join(args.projects_dir,"*","**","*.jsonl"), recursive=True)
           if os.path.basename(f)!="journal.jsonl" and not os.path.basename(f).startswith("agent-")]
    files.sort(key=lambda p: os.path.getsize(p), reverse=True)
    if args.max_files: files=files[:args.max_files]
    print(f"[lineage] {len(files)} files", file=sys.stderr)

    G=dict(sessions=0, n=0, breaks=0, hard=0, forks=0, fork_breaks=0,
           rewritten=0, recache=0, newcache=0, total_cw=0, total_cr=0,
           by_cause=Counter(), recache_by_cause=Counter(), avoidability=Counter(),
           cosig=Counter(), gaps=[])
    first_ts=last_ts=None
    for i,fp in enumerate(files):
        try: R=analyze(fp)
        except Exception as e:
            print(f"[warn] {os.path.basename(fp)}: {e}", file=sys.stderr); continue
        if R["n"]==0: continue
        G["sessions"]+=1
        for k in ("n","breaks","hard","forks","fork_breaks","rewritten","recache","newcache","total_cw","total_cr"):
            G[k]+=R[k]
        G["by_cause"].update(R["by_cause"]); G["recache_by_cause"].update(R["recache_by_cause"])
        G["avoidability"].update(R["avoidability"]); G["cosig"].update(R["cosig"]); G["gaps"].extend(R["gaps"])
        if R["first_ts"]:
            first_ts=min(first_ts or R["first_ts"], R["first_ts"]); last_ts=max(last_ts or R["last_ts"], R["last_ts"])
        if (i+1)%200==0: print(f"[lineage] {i+1}/{len(files)}", file=sys.stderr)

    rc=G["recache"] or 1
    L=[]; A=L.append
    A("# CacheScope v0.2 — lineage-aware cache lifecycle report")
    A("")
    A(f"Generated {datetime.now().isoformat(timespec='seconds')}. {G['sessions']} main sessions, {G['n']:,} deduped requests, "
      f"{(first_ts or '?')[:10]}→{(last_ts or '?')[:10]}.")
    A(f"Break vs true parentUuid parent. Established = parent(cr+cw). Break = cr < {BREAK_RATIO}×established, floor {FLOOR:,}.")
    A("")
    A("## v0.1 → v0.2 correction (lineage + geometry)")
    A("")
    A(f"| Metric | Value |")
    A(f"|---|---|")
    A(f"| Total cache writes | {fmt(G['total_cw'])} |")
    A(f"| Detected breaks | {G['breaks']:,} ({G['hard']:,} near-total) |")
    A(f"| Breaks on a FORK/branch (edited/regen turn) | {G['fork_breaks']:,} |")
    A(f"| **Raw cache_creation at breaks (v0.1 method)** | {fmt(G['rewritten'])} = {100*G['rewritten']/max(G['total_cw'],1):.0f}% of writes |")
    A(f"| **Geometric RECACHE at breaks (honest rebuild)** | **{fmt(G['recache'])} = {100*G['recache']/max(G['total_cw'],1):.0f}% of writes** |")
    A(f"| First-time-cached NEW suffix at breaks (not waste) | {fmt(G['newcache'])} |")
    A("")
    A("## Rebuild (recache) tokens by cause")
    A("")
    A("| Cause | Breaks | Recache tokens | Share |")
    A("|---|---|---|---|")
    for c,cnt in G["by_cause"].most_common():
        A(f"| {c} | {cnt:,} | {fmt(G['recache_by_cause'][c])} | {100*G['recache_by_cause'][c]/rc:.1f}% |")
    A("")
    A("## Avoidability view (the number that actually matters for a subscription product)")
    A("")
    A("| Bucket | Recache tokens | Share | Reachable by… |")
    A("|---|---|---|---|")
    reach={"unavoidable_ttl_idle":"nothing (human stepped away >1h)",
           "unavoidable_ttl_coincident":"nothing (upgrade/switch at an idle boundary)",
           "addressable_config":"pin CLI / lock model (config policy)",
           "addressable_midwork":"admission/prefix-stability (THE product slice)",
           "artifact_or_userfork":"n/a (edited-message fork or detector artifact)",
           "compaction":"n/a (post-compaction prefix already small)"}
    for b,v in G["avoidability"].most_common():
        A(f"| {b} | {fmt(v)} | {100*v/rc:.1f}% | {reach.get(b,'?')} |")
    A("")
    A("## Co-signals at breaks")
    A("")
    for c,n in G["cosig"].most_common(12):
        A(f"- {c}: {n:,}")
    A("")
    A(f"Forks total: {G['forks']:,} ({G['fork_breaks']:,} caused a break). "
      f"A fork means this request's parent was not the latest request — an edited/regenerated turn or resumed branch.")
    with open(os.path.join(args.out,"cachescope_lineage.md"),"w") as f: f.write("\n".join(L))
    with open(os.path.join(args.out,"cachescope_lineage.json"),"w") as f:
        json.dump({k:(dict(v) if isinstance(v,Counter) else v) for k,v in G.items() if k!="gaps"}, f, indent=1)
    print("\n".join(L))

if __name__=="__main__":
    main()
