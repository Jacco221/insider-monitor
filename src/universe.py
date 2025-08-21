from src.utils import get
import time

def get_top_coins(limit=100):
    """
    Haal top 'limit' coins op via CoinGecko.
    Retourneert lijst met dicts: {id, symbol, name}.
    (We controleren Binance-pair niet hier; TA-module regelt fallbacks.)
    """
    cg = get("https://api.coingecko.com/api/v3/coins/markets",
             params={"vs_currency":"usd","order":"market_cap_desc","per_page":250,"page":1})
    out = []
    for row in cg:
        sym = (row.get("symbol") or "").upper()
        out.append({"id": row["id"], "symbol": sym, "name": row.get("name") or sym})
        if len(out) >= limit:
            break
        time.sleep(0.1)  # klein rustmomentje
    return out

if __name__ == "__main__":
    coins = get_top_coins(10)
    print(coins)
