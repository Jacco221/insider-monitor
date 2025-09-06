#!/usr/bin/env python3
import argparse
import json
import csv
from pathlib import Path

def load_scores(json_file, csv_file, limit=100):
    scores = []
    if Path(json_file).exists():
        with open(json_file, "r") as f:
            scores = json.load(f)
    elif Path(csv_file).exists():
        with open(csv_file, newline="") as f:
            reader = csv.DictReader(f)
            scores = list(reader)
    else:
        raise FileNotFoundError("Geen scores gevonden (JSON of CSV ontbreekt).")
    return scores[:limit]

def select_moonshots(scores, top=5):
    results = []
    for s in scores:
        try:
            ta = float(s.get("TA_%", 0))
            rs = float(s.get("RS_%", 0))
            macro = float(s.get("Macro_%", 0))
            volume = float(s.get("ta_volume", 0))
            score = (0.4*ta + 0.3*rs + 0.2*macro + 0.1*volume)
            s["_MoonshotScore"] = score
            results.append(s)
        except Exception:
            continue
    results = sorted(results, key=lambda x: x["_MoonshotScore"], reverse=True)
    return results[:top]

def write_reports(results, out_csv, out_md):
    # CSV
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    # Markdown
    lines = []
    lines.append("# 🌙 Moonshot-kandidaten")
    lines.append("Dit rapport toont mid/low-cap kansen met hogere volatiliteit en risico.")
    lines.append("")
    lines.append("| Symbool | Naam | MoonshotScore | TA_% | RS_% | Macro_% |")
    lines.append("|---------|------|---------------|------|------|---------|")
    for r in results:
        lines.append(f"| {r.get('symbol')} | {r.get('name')} | {r['_MoonshotScore']:.1f}% "
                     f"| {r.get('TA_%')} | {r.get('RS_%')} | {r.get('Macro_%')} |")
    lines.append("")
    lines.append("> ⚠️ Let op: hogere volatiliteit en risico. Overweeg kleinere posities.")

    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores-json", required=True)
    parser.add_argument("--scores-csv", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    scores = load_scores(args.scores_json, args.scores_csv, limit=100)
    results = select_moonshots(scores, top=args.top)
    write_reports(results, args.out_csv, args.out_md)

    print(f"✅ Moonshot-rapport opgeslagen: {args.out_csv}, {args.out_md}")

if __name__ == "__main__":
    main()

