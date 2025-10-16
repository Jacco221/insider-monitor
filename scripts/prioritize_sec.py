#!/usr/bin/env python3
import json, math, os, re
from pathlib import Path
from collections import defaultdict

REP = Path("data/reports")
SECJ = REP / "sec_events.jsonl"
EUJ  = REP / "eu_events.jsonl"
RANKED = REP / "sec_headlines_ranked.txt"
RAW    = REP / "sec_headlines.txt"

def log1p(x): 
    try: return math.log1p(max(0.0, float(x)))
    except: return 0.0

BAD_PAT = re.compile(r"(?i)\b(424|497|425|485[A-Z]*|FUND|TRUST|ACCOUNT|BANK|FINANCE|VARIABLE\s+ANNUITY)\b")

def role_bucket(role:str)->str:
    r=(role or "").lower()
    if any(k in r for k in ["ceo","chief executive","cfo","president","chair"]): return "TOP"
    if "officer" in r or "dir" in r: return "OFF"
    return "OTHER"

def role_weight(role:str)->float:
    b=role_bucket(role)
    return 3.0 if b=="TOP" else (2.0 if b=="OFF" else 1.0)

def human(v):
    v=float(v); 
    return f"${v/1_000_000:.2f}M" if v>=1_000_000 else (f"${v/1_000:.0f}k" if v>=1_000 else f"${v:.0f}")

def load_events():
    evts=[]
    for p in (SECJ, EUJ):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                try: evts.append(json.loads(line))
                except: pass
    return evts

def normalize_notional(e):
    buy=float(e.get("buy",0.0)); sell=float(e.get("sell",0.0))
    m=float(e.get("m",0.0)); f=float(e.get("f",0.0))
    # haal uit txs als hoofdvelden leeg zijn
    if (buy==0 and sell==0) and isinstance(e.get("txs"), list):
        b=s=0.0
        for t in e["txs"]:
            tot=float(t.get("total",0.0))
            code=(t.get("code","") or "").upper(); ad=(t.get("ad","") or "").upper()
            if code=="P" or ad=="A": b+=tot
            if code=="S" or ad=="D": s+=tot
        buy=b; sell=s
    return buy, sell, m, f

def score_events(evts):
    owners_per=defaultdict(set)
    for e in evts: owners_per[e.get("ticker","UNKNOWN")].add(e.get("who","?"))
    ranked=[]
    for e in evts:
        # bronfilter
        titleblob=" ".join(str(x or "") for x in (e.get("ticker"),e.get("issuer"),e.get("who")))
        if BAD_PAT.search(titleblob): 
            continue
        role = e.get("role","Insider")
        buy,sell,m,f = normalize_notional(e)
        rw = role_weight(role)
        net_sell=max(0.0, sell - (m+f))
        cluster=len(owners_per[e.get("ticker","UNKNOWN")])
        score = 2.8*rw*log1p(buy) - 1.1*rw*log1p(net_sell) + 1.0*log1p(cluster)
        ranked.append((score, {**e,"buy":buy,"sell":sell,"m":m,"f":f}))
    ranked.sort(key=lambda x:x[0], reverse=True)
    return ranked

def build_line(e):
    who=e.get("who","Insider")
    tkr=(e.get("ticker") or e.get("issuer") or "UNKNOWN").upper()
    role=e.get("role","Insider")
    when=(e.get("when","") or "").replace("T"," ").replace("Z"," UTC")
    buy=float(e.get("buy",0.0)); sell=float(e.get("sell",0.0)); m=float(e.get("m",0.0)); f=float(e.get("f",0.0))
    parts=[]
    if buy:  parts.append(f"BUY {human(buy)}")
    if sell: parts.append(f"SELL {human(sell)}")
    if m:    parts.append(f"M {human(m)}")
    if f:    parts.append(f"F {human(f)}")
    body=", ".join(parts) if parts else "Form 4 filed"
    return f"- [SEC] {tkr} – {who} ({role}): {body} ({when})"

def main():
    evts=load_events()
    if not evts:
        if RAW.exists():
            RANKED.write_text(RAW.read_text(encoding="utf-8"), encoding="utf-8")
            print("[rank] no events; copied raw headlines."); 
            return 0
        print("[rank] nothing to rank."); return 0

    ranked=score_events(evts)
    # WhatsApp-lijst: prioriteit aan BUY ≥ $25k, max 30; anders gefilterde raw top-30
    lines=[build_line(e) for s,e in ranked if float(e.get("buy",0.0))>=25_000][:30]
    if not lines:
        raw=[l.strip() for l in RAW.read_text(encoding="utf-8",errors="ignore").splitlines() if l.strip()]
        raw=[l for l in raw if not BAD_PAT.search(l)]
        lines=raw[:30]
        print("[rank] no qualifying BUY; fell back to filtered raw top-30.")
    RANKED.write_text("\n".join(lines)+("\n" if lines else ""), encoding="utf-8")
    print(f"[rank] wrote: {RANKED} ({len(lines)} lines) from {len(evts)} events.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
