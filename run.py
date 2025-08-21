from pathlib import Path
import pandas as pd
from src.fetch import fetch_btc_price

def main():
    # 1) Data ophalen
    df = fetch_btc_price()

    # 2) Output-mappen
    outdir = Path("data/reports")
    outdir.mkdir(parents=True, exist_ok=True)

    # 3) Bestanden schrijven
    (outdir / "latest.csv").write_text(df.to_csv(index=False), encoding="utf-8")
    (outdir / "latest.json").write_text(df.to_json(orient="records"), encoding="utf-8")

    # 4) Console-output (zichtbaar in Actions)
    print("Top 1 (dummy voorlopig):")
    print(df.to_string(index=False))

if __name__ == "__main__":
    main()
