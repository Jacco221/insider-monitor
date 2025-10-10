#!/usr/bin/env python3
import json, math, os
from pathlib import Path
from collections import defaultdict

REP = Path("data/reports")
EVJ = REP / "sec_events.jsonl"
RANKED = REP / "sec_headlines_ranked.txt"
RAW = REP / "sec_headlines.txt"

def log1p(x): return math.log1p(max(0.0, float(x)))

# ---- thresholds (ENV override) ----
def _envf(n, d):
    try: return float(os.getenv(n,"").replace("_",""))
    except: return d
TH_TOP_BUY    = _envf("SEC_THRESH_TOP_BUY",    100_000)
TH_OFF_BUY    = _envf("SEC_THRESH_OFF_BUY",    150_000)
TH_OTHER_BUY  = _envf("SEC_THRESH_OTHER_BUY",1_000_000)
TH_TOP_SELL   = _envf("SEC_THRESH_TOP_SELL",   750_000)
TH_OFF_SELL   = _envf("SEC_THRESH_OFF_SELL", 1_000_000)
TH_OTHER_SELL = _envf("SEC_THRESH_OTHER_SELL",2_000_000)

def role_bucket(role:str)->str:
    r=(role or "").lower()
    if "ceo" in r or "chief executive" in r or "cfo" in r or "president" in r or "chair" in r: return "TOP"
    if "officer" in r or "dir" in r: return "OFF"
    return "OTHER"

def role_weight(role:str)->float:
    b=role_bucket(role)
    return 3.0 if b=="TOP" else (2.0 if b=="OFF" else 1.0)

def hot_flag(role:str, buy:float, sell:float, m:float, f:float)->bool:
    b=role_bucket(role)
    if buy>0:
        th = TH_TOP_BUY if b=="TOP" else (TH_OFF_BUY if b=="OFF" else TH_OTHER_BUY)
        return buy >= th
    if sell>0:
        th = TH_TOP_SELL if b=="TOP" else (TH_OFF_SELL if b=="OFF" else TH_OTHER_SELL)
        net = max(0.0, sell - (m+f))
        return net >= th
    return False

def human(v):
    v=float(v)
    return f"${v/1_000_000:.2f}M" if v>=1_000_000 else (f"${v/1_000:.0f}k" if v>=1_000 else f"${v:.0f}")

def load_events():
    evts=[]
    if EVJ.exists():
        for line in EVJ.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try: evts.append(json.loads(line))
            except: pass
    return evts

def score_events(evts):
    owners_per = defaultdict(set)
    for e in evts:
        owners_per[e.get("ticker","UNKNOWN")].add(e.get("who","?"))

    ranked=[]
    for e in evts:
        role = e.get("role","Insider")
        buy  = float(e.get("buy",0.0))
        sell = float(e.get("sell",0.0))
        m    = float(e.get("m",0.0))
        f    = float(e.get("f",0.0))
        # filter: alleen events met echte notional in WA-lijst
        if buy==0.0 and sell==0.0 and m==0.0 and f==0.0:
            # we still allow them to be present but score super low
            score = -10.0
        else:
            rw = role_weight(role)
            net_sell = max(0.0, sell - (m+f))
            cluster = len(owners_per[e.get("ticker","UNKNOWN")])
            score = 2.5*rw*log1p(buy) - 1.2*rw*log1p(net_sell) + 1.0*log1p(cluster)
            # kleine bonus voor HOT
            if hot_flag(role, buy, sell, m, f):
                score += 2.0
        ranked.append((score, e))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked

def build_line(e):
    who = e.get("who","Insider")
    tkr = e.get("ticker","UNKNOWN")
    role= e.get("role","Insider")
    when= e.get("when","time:n/a").replace("T"," ").replace("Z"," UTC")
    buy = float(e.get("buy",0.0)); sell=float(e.get("sell",0.0)); m=float(e.get("m",0.0)); f=float(e.get("f",0.0))
    parts=[]
    if buy:  parts.append(f"BUY {human(buy)}")
    if sell: parts.append(f"SELL {human(sell)}")
    if m:    parts.append(f"M {human(m)}")
    if f:    parts.append(f"F {human(f)}")
    hot = hot_flag(role, buy, sell, m, f)
    tag = "🔥HOT🔥 " if hot else ""
    body = ", ".join(parts) if parts else "Form 4 filed"
    return f"- [SEC] {tag}{tkr} – {who} ({role}): {body} ({when})"

def main():
    evts = load_events()
    if not evts:
        # no events: copy raw
        if RAW.exists():
            RANKED.write_text(RAW.read_text(encoding="utf-8"), encoding="utf-8")
            print("[rank] no events; copied raw headlines.")
            return 0
        print("[rank] nothing to rank.")
        return 0

    ranked = score_events(evts)
    # -- relevancy filter for WhatsApp output --
    clean = []
    for s_,e in ranked:
        name=(e.get('ticker','')+e.get('who','')).lower()
        if any(bad in name for bad in ['fund','trust','account','bank','finance']):
            continue  # skip non-corporate filings
        if (e.get('buy',0)<5e4) and (e.get('sell',0)<2.5e5):
            continue  # skip small/zero trades
        clean.append((s_,e))
    ranked = clean[:30]  # WhatsApp top N

    # neem vooral events met bedrag, vul aan met 'filed' als er te weinig zijn
    lines = [build_line(e) for s,e in ranked if s>-9.99][:120]
    if not lines:  # edge case: alles zonder bedragen
        lines = [build_line(e) for s,e in ranked][:120]

    RANKED.write_text("\n".join(lines)+("\n" if lines else ""), encoding="utf-8")
    print(f"[rank] wrote: {RANKED} ({len(lines)} lines) from {len(evts)} events.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
