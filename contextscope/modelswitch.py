#!/usr/bin/env python3
"""
ModelSwitch analyzer — measures model-switch ("context migration") churn in
Claude Code transcripts, using the lineage-aware parent (same fix as CacheScope v0.2).

Answers the user's #29 questions:
  - how many sessions switched models, how many switch events
  - resident context (established prefix) at each switch
  - cache_read BEFORE-vs-AT switch (tests the "cache island still warm on switch-back" idea)
  - cache_creation reprocessing cost at the switch (the migration overhead)
  - switches at high context (>100k / >400k)
  - switch->break rate, and switch-back WARM hits (no extra cost)
  - transition matrix (which model -> which)
  - proximity to compaction and gap timing buckets
Emits aggregates only.
"""
import argparse, glob, json, os, sys
from collections import Counter, defaultdict
from datetime import datetime

FLOOR = 20_000
BREAK_RATIO = 0.5

def pts(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z","+00:00"))
    except: return None

def fmt(n):
    n=float(n)
    if abs(n)>=1e9: return f"{n/1e9:.2f}B"
    if abs(n)>=1e6: return f"{n/1e6:.2f}M"
    if abs(n)>=1e3: return f"{n/1e3:.1f}k"
    return str(int(n))

def short(m):
    return (m or "?").replace("claude-","").replace("-20251001","")

def analyze(fp):
    node={}; order=[]; seen={}
    for line in open(fp, errors="replace"):
        line=line.strip()
        if not line: continue
        try: rec=json.loads(line)
        except: continue
        uuid=rec.get("uuid"); t=rec.get("type"); parent=rec.get("parentUuid")
        if t=="system" and rec.get("subtype")=="compact_boundary":
            if uuid: node[uuid]=("compact",parent,None)
            continue
        if t=="assistant":
            m=rec.get("message") or {}
            if m.get("model")=="<synthetic>":
                if uuid: node[uuid]=("other",parent,None)
                continue
            u=m.get("usage")
            if not isinstance(u,dict):
                if uuid: node[uuid]=("other",parent,None)
                continue
            rid=rec.get("requestId") or m.get("id")
            if rid in seen:
                node[uuid]=("alias",parent,seen[rid]); continue
            seen[rid]=uuid
            info=dict(cr=u.get("cache_read_input_tokens",0) or 0,
                      cw=u.get("cache_creation_input_tokens",0) or 0,
                      model=m.get("model") or "", ts=rec.get("timestamp"), uuid=uuid)
            node[uuid]=("asst",parent,info); order.append(uuid)
        else:
            if uuid: node[uuid]=("other",parent,None)

    def lp(p):
        steps=0; crossed=False
        while p is not None and steps<100000:
            steps+=1; nd=node.get(p)
            if nd is None: return None, crossed
            k=nd[0]
            if k=="compact": crossed=True; p=nd[1]; continue
            if k=="alias": p=nd[2]; continue
            if k=="asst": return p, crossed
            p=nd[1]
        return None, crossed

    R=dict(n=0, sessions_has_switch=0, switches=0, switch_breaks=0, switchback_warm=0,
           migrate_tokens=0, resident_at_switch=[], hi100=0, hi400=0,
           matrix=Counter(), gap_bucket=Counter(), near_compaction=0,
           models=Counter(), first_ts=None, last_ts=None)
    prev=None
    for uuid in order:
        cur=node[uuid][2]; R["n"]+=1; R["models"][cur["model"]]+=1
        if cur["ts"]:
            R["first_ts"]=R["first_ts"] or cur["ts"]; R["last_ts"]=cur["ts"]
        puuid,crossed=lp(node[uuid][1])
        if puuid is not None:
            p=node[puuid][2]
            if p["model"] and cur["model"] and p["model"]!=cur["model"]:
                est=p["cr"]+p["cw"]
                R["switches"]+=1
                R["matrix"][f"{short(p['model'])} -> {short(cur['model'])}"]+=1
                R["resident_at_switch"].append(est)
                if est>=100_000: R["hi100"]+=1
                if est>=400_000: R["hi400"]+=1
                if crossed: R["near_compaction"]+=1
                gap=None
                a,b=pts(p["ts"]),pts(cur["ts"])
                if a and b: gap=(b-a).total_seconds()/60
                if gap is None: R["gap_bucket"]["?"]+=1
                elif gap<5: R["gap_bucket"]["<5m"]+=1
                elif gap<30: R["gap_bucket"]["5-30m"]+=1
                elif gap<60: R["gap_bucket"]["30-60m"]+=1
                else: R["gap_bucket"][">60m"]+=1
                if est>=FLOOR and cur["cr"]<BREAK_RATIO*est:
                    R["switch_breaks"]+=1
                    R["migrate_tokens"]+=min(cur["cw"], max(0, est-cur["cr"]))
                elif est>=FLOOR:
                    # model changed but cache stayed warm -> switch-back to a live island
                    R["switchback_warm"]+=1
        prev=uuid
    R["sessions_has_switch"]=1 if R["switches"]>0 else 0
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
    print(f"[modelswitch] {len(files)} files", file=sys.stderr)

    G=dict(sessions=0, sessions_switch=0, n=0, switches=0, switch_breaks=0, switchback_warm=0,
           migrate=0, hi100=0, hi400=0, resident=[], matrix=Counter(), gap=Counter(),
           near_comp=0, models=Counter())
    first=last=None
    for i,fp in enumerate(files):
        try: R=analyze(fp)
        except Exception as e:
            print(f"[warn] {os.path.basename(fp)}: {e}", file=sys.stderr); continue
        if R["n"]==0: continue
        G["sessions"]+=1; G["sessions_switch"]+=R["sessions_has_switch"]
        for k,src in [("n","n"),("switches","switches"),("switch_breaks","switch_breaks"),
                      ("switchback_warm","switchback_warm"),("migrate","migrate_tokens"),
                      ("hi100","hi100"),("hi400","hi400"),("near_comp","near_compaction")]:
            G[k]+=R[src]
        G["matrix"].update(R["matrix"]); G["gap"].update(R["gap_bucket"]); G["models"].update(R["models"])
        G["resident"].extend(R["resident_at_switch"])
        if R["first_ts"]: first=min(first or R["first_ts"], R["first_ts"]); last=max(last or R["last_ts"], R["last_ts"])
        if (i+1)%200==0: print(f"[modelswitch] {i+1}/{len(files)}", file=sys.stderr)

    res=sorted(G["resident"])
    def pctl(p):
        return res[min(len(res)-1, int(p*len(res)))] if res else 0
    L=[]; A=L.append
    A("# ModelSwitch analyzer — context-migration churn")
    A("")
    A(f"Generated {datetime.now().isoformat(timespec='seconds')}. {G['sessions']} main sessions, {G['n']:,} requests, "
      f"{(first or '?')[:10]}→{(last or '?')[:10]}.")
    A("")
    A("## Headline")
    A("| Metric | Value |")
    A("|---|---|")
    A(f"| Sessions with ≥1 model switch | {G['sessions_switch']}/{G['sessions']} ({100*G['sessions_switch']/max(G['sessions'],1):.0f}%) |")
    A(f"| Total model-switch events | {G['switches']:,} |")
    A(f"| Switch events per switching session (mean) | {G['switches']/max(G['sessions_switch'],1):.1f} |")
    A(f"| Switches that broke a substantial cache (≥{FLOOR//1000}k) | {G['switch_breaks']:,} |")
    A(f"| Switches that stayed WARM (live cache island / switch-back) | {G['switchback_warm']:,} |")
    A(f"| **Migration reprocess tokens (recache at switch-breaks)** | **{fmt(G['migrate'])}** |")
    A(f"| Switches at ≥100k resident context | {G['hi100']:,} |")
    A(f"| Switches at ≥400k resident context | {G['hi400']:,} |")
    A(f"| Switches near a compaction boundary | {G['near_comp']:,} |")
    A("")
    A("## Resident context at switch (percentiles)")
    A(f"p50 {fmt(pctl(0.5))} · p75 {fmt(pctl(0.75))} · p90 {fmt(pctl(0.90))} · p99 {fmt(pctl(0.99))} · max {fmt(res[-1] if res else 0)}")
    A("")
    A("## Transition matrix (top)")
    A("| From → To | Count |")
    A("|---|---|")
    for k,v in G["matrix"].most_common(15):
        A(f"| {k} | {v:,} |")
    A("")
    A("## Gap at switch")
    for k in ("<5m","5-30m","30-60m",">60m","?"):
        if G["gap"].get(k): A(f"- {k}: {G['gap'][k]:,}")
    A("")
    A("## Context vs the other pools (from CacheScope v0.2)")
    A(f"- TTL-idle recache: 978M · addressable-midwork: 415M · **model-switch recache: {fmt(G['migrate'])}**")
    with open(os.path.join(args.out,"modelswitch.md"),"w") as f: f.write("\n".join(L))
    with open(os.path.join(args.out,"modelswitch.json"),"w") as f:
        json.dump({k:(dict(v) if isinstance(v,Counter) else (v if not isinstance(v,list) else None))
                   for k,v in G.items()}, f, indent=1)
    print("\n".join(L))

if __name__=="__main__":
    main()
