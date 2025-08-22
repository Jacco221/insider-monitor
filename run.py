from pathlib import Path
import time
import pandas as pd

from src.universe import get_top_coins
from src.ta import ta_indicators, weighted_group_score
from src.macro import dxy_indicator
from src.rs import rs_vs_btc_indicator

TEST_LIMIT = 20          # eerst testen; later naar 100
SLEEP_PER_COIN = 1.2     # API rust
BATCH_SIZE = 20
SLEEP_PER_BATCH = 20.0

# weging groepen
W_TA = 0.60
W_MACRO = 0.20
W_RS = 0.20

def pct_from_group(avg_m1_to_p1: float) -> float:
    return max(0.0, min(100.0, (avg_m1_to_p1 + 1.0) * 50.0))

def time_weight(hours: float | None) -> float:
    if hours is None:
        return 1.0
    if hours < 24: return 3.0
    if hours < 24*3: return 2.0
    if hours < 24*7: return 1.0
    return 0.5

def main():
    coins = get_top_coins(limit=TEST_LIMIT)
    rows = []

    # 1) Macro (één keer per run)
    m_score, m_ages, m_w = dxy_indicator()
    m_avg = weighted_group_score({"dxy": m_score}, m_w, age_weights={k: time_weight(v) for k,v in m_ages.items()})
    m_pct = pct_from_group(m_avg)

    for i, c in enumerate(coins, 1):
        print(f"[{i}/{len(coins)}] Verwerk {c['symbol']} ({c['id']})", flush=True)

        # 2) TA per munt
        ta_scores, ta_ages, ta_w = ta_indicators(c["symbol"], c["id"])
        ta_avg = weighted_group_score(ta_scores, ta_w, age_weights={k: time_weight(v) for k,v in ta_ages.items()})
        ta_pct = pct_from_group(ta_avg)

        # 3) RS t.o.v. BTC per munt
        rs_score, rs_ages, rs_w = rs_vs_btc_indicator(c["id"])
        rs_avg = weighted_group_score({"rs": rs_score}, rs_w, age_weights={k: time_weight(v) for k,v in rs_ages.items()})
        rs_pct = pct_from_group(rs_avg)

        # 4) Totaal (gewogen som van groeps-pcts)
        total_pct = (W_TA * ta_pct) + (W_MACRO * m_pct) + (W_RS * rs_pct)

        # Gemiddelde leeftijd (uren)
        all_ages = [a for a in list(ta_ages.values()) + list(rs_ages.values()) + list(m_ages.values()) if a is not None]
        avg_age = float(pd.Series(all_ages).mean()) if all_ages else 0.0

        rows.append({
            "symbol": c["symbol"], "name": c["name"],
            # TA sub-scores (‑1/0/+1)
            "ta_ma": ta_scores.get("ma_crossover"),
            "ta_volume": ta_scores.get("volume_trend"),
            "ta_funding": ta_scores.get("funding_rate"),
            # Groepspercentages
            "TA_%": round(ta_pct, 1),
            "Macro_%": round(m_pct, 1),
            "RS_%": round(rs_pct, 1),
            "Total_%": round(total_pct, 1),
            "AvgDataAge_h": round(avg_age, 1),
        })

        time.sleep(SLEEP_PER_COIN)
        if i % BATCH_SIZE == 0 and i < len(coins):
            print(f"  - batchpauze {SLEEP_PER_BATCH}s", flush=True)
            time.sleep(SLEEP_PER_BATCH)

    df = pd.DataFrame(rows).sort_values("Total_%", ascending=False)

    outdir = Path("data/reports"); outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir/"scores_latest.csv", index=False)
    df.to_json(outdir/"scores_latest.json", orient="records")

    top5 = df[["symbol","Total_%","TA_%","Macro_%","RS_%","AvgDataAge_h"]].head(5)
    lines = ["# Top 5 totaalscore (TA+Macro+RS)\n"]
    for _, r in top5.iterrows():
        hints = []
        if r["TA_%"] >= 66: hints.append("sterke TA")
        if r["RS_%"] >= 66: hints.append("outperform vs BTC")
        if r["Macro_%"] >= 66: hints.append("gunstige macro")
        motive = ", ".join(hints) if hints else "signalen gemengd"
        lines.append(f"- **{r['symbol']}** — Total {r['Total_%']}% · {motive} (avg leeftijd {r['AvgDataAge_h']}h)")
    (outdir/"top5_latest.md").write_text("\n".join(lines), encoding="utf-8")

    print("Top 5 (Total):")
    print(top5.to_string(index=False))

if __name__ == "__main__":
    main()
