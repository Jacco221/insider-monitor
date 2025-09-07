#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
import sys
import json
import pandas as pd
import numpy as np

# -------------------------
# Helpers
# -------------------------

BLUECHIPS_DEFAULT = {
    "BTC", "WBTC", "ETH", "WBETH", "WEETH", "STETH", "WSTETH",
    "SOL", "BNB", "XRP", "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD",
    "TRX", "ADA", "DOGE", "TON", "AVAX", "DOT", "MATIC", "POL", "LTC",
    "BCH", "LINK", "SHIB", "APT", "ARB", "OP"
}

def norm_float_col(df, col, default=np.nan):
    if col not in df.columns:
        df[col] = default
    df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def read_scores(scores_csv, scores_json=None):
    df = pd.read_csv(scores_csv)

    if scores_json and Path(scores_json).exists():
        try:
            with open(scores_json, "r", encoding="utf-8") as f:
                json.load(f)  # voorlopig negeren
        except Exception:
            pass

    needed_numeric = ["TA_%", "RS_%", "Macro_%", "Total_%",
                      "ta_volume", "ta_funding", "age_h", "AvgDataAge_h", "rank"]
    for c in needed_numeric:
        df = norm_float_col(df, c)

    if "AvgDataAge_h" in df.columns:
        df["AvgDataAge_h_"] = df["AvgDataAge_h"]
    else:
        df["AvgDataAge_h_"] = df["age_h"]

    return df

# -------------------------
# Parser
# -------------------------

def build_parser():
    p = argparse.ArgumentParser(description="Moonshot v2: filter voor small/mid caps.")
    p.add_argument("--scores-csv", required=True)
    p.add_argument("--scores-json", required=False, default=None)
    p.add_argument("--out-csv", required=True)
    p.add_argument("--out-md", required=True)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--min-volume", type=float, default=1.0)
    # officiële vlag
    p.add_argument("--exclude-top-rank", type=int, default=None,
                   help="Sluit top N marketcap coins uit (bijv. 30 = top30 eruit).")
    # alias vlag (backwards compatibiliteit)
    p.add_argument("--min-rank", dest="alias_min_rank", type=int, default=None,
                   help="(Alias, deprecated) Gebruik --exclude-top-rank i.p.v. --min-rank.")
    p.add_argument("--exclude-bluechips", dest="exclude_bluechips", action="store_true", default=True)
    p.add_argument("--no-exclude-bluechips", dest="exclude_bluechips", action="store_false")
    return p

# -------------------------
# Kern
# -------------------------

def main():
    args = build_parser().parse_args()

    # alias doorzetten
    if args.exclude_top_rank is None and args.alias_min_rank is not None:
        args.exclude_top_rank = args.alias_min_rank

    scores = read_scores(args.scores_csv, args.scores_json)

    base = (
        (scores["Total_%"] >= 60.0) &
        (scores["RS_%"] >= 50.0) &
        (scores["ta_volume"] >= args.min_volume)
    )
    moonshots = scores.loc[base].copy()

    if args.exclude_top_rank is not None:
        moonshots = moonshots.loc[moonshots["rank"] > int(args.exclude_top_rank)]

    if args.exclude_bluechips:
        moonshots = moonshots.loc[~moonshots["symbol"].str.upper().isin(BLUECHIPS_DEFAULT)]

    moonshots["MoonshotScore"] = (
        0.5 * moonshots["TA_%"] +
        0.3 * moonshots["RS_%"] +
        0.2 * moonshots["Macro_%"]
    )

    moonshots = moonshots.sort_values("MoonshotScore", ascending=False)
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    moonshots.to_csv(args.out_csv, index=False)

    topk = moonshots.head(int(args.top))
    lines = []
    lines.append("# 🌙 Moonshot-kandidaten (v2)")
    lines.append("")
    if args.exclude_top_rank:
        lines.append(f"- Top {args.exclude_top_rank} by marketcap uitgesloten")
    lines.append(f"- Bluechips uitgesloten: {args.exclude_bluechips}")
    lines.append("")
    if topk.empty:
        lines.append("⚠️ Geen kandidaten gevonden.")
    else:
        lines.append("| # | Symbol | Score | TA_% | RS_% | Macro_% | Rank |")
        lines.append("|---|--------|-------:|-----:|-----:|--------:|-----:|")
        for i, row in enumerate(topk.itertuples(index=False), 1):
            lines.append(
                f"| {i} | {row.symbol} | {row.MoonshotScore:.1f} | "
                f"{row._asdict().get('TA_%',0):.1f} | {row._asdict().get('RS_%',0):.1f} | "
                f"{row._asdict().get('Macro_%',0):.1f} | {int(row.rank)} |"
            )

    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Moonshot-rapport opgeslagen:\n - {args.out_csv}\n - {args.out_md}")

if __name__ == "__main__":
    sys.exit(main())

