from pathlib import Path
import pandas as pd
from src.universe import get_top_coins
from src.ta import ta_indicators, weighted_group_score

def pct_from_group(avg_m1_to_p1: float) -> float:
    return max(0.0, min(100.0, (avg_m1_to_p1 + 1.0) * 50.0))

def main():
    coins = get_top_coins(limit=100)   # [{'id','symbol','name'}...]
    rows = []

    for c in coins:
        scores, ages, w = ta_indicators(c["symbol"], c["id"])
        ta_avg = weighted_group_score(scores, w)    # -1..+1
        ta_pct = pct_from_group(ta_avg)             # 0..100

        avg_age = pd.Series([a for a in ages.values() if a is not None]).mean()
        rows.append({
            "symbol": c["symbol"],
            "name":   c["name"],
            "ta_ma":       scores.get("ma_crossover"),
            "ta_volume":   scores.get("volume_trend"),
            "ta_funding":  scores.get("funding_rate"),
            "TA_%":        round(ta_pct, 1),
            "AvgDataAge_h": round(float(avg_age) if pd.notna(avg_age) else 0.0, 1)
        })

    df = pd.DataFrame(rows).sort_values("TA_%", ascending=False)

    outdir = Path("data/reports"); outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir/"scores_latest.csv", index=False)
    df.to_json(outdir/"scores_latest.json", orient="records")

    top5 = df[["symbol","TA_%","ta_ma","ta_volume","ta_funding","AvgDataAge_h"]].head(5)
    lines = ["# Top 5 TA-score (3 indicatoren)\n"]
    for _, r in top5.iterrows():
        hints = []
        if r["ta_ma"] == 1:      hints.append("MA50>MA200")
        elif r["ta_ma"] == -1:   hints.append("MA50<MA200")
        if r["ta_volume"] == 1:  hints.append("volume↑")
        elif r["ta_volume"] == -1: hints.append("volume↓")
        if r["ta_funding"] == 1: hints.append("funding contrarian bullish")
        elif r["ta_funding"] == -1: hints.append("funding oververhit")
        motive = ", ".join(hints) if hints else "signalen gemengd"
        lines.append(f"- **{r['symbol']}** — TA {r['TA_%']}% · {motive} (avg leeftijd {r['AvgDataAge_h']}h)")

    (outdir/"top5_latest.md").write_text("\n".join(lines), encoding="utf-8")
    print("Top 5 (TA):")
    print(top5.to_string(index=False))

if __name__ == "__main__":
    main()
