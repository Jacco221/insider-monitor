from src.utils import get
import time

def get_top_coins(limit=100):
    """
    Haal top 'limit' coins op via CoinGecko en koppel aan Binance USDT-ticker
    waar mogelijk. Geeft list van dicts: {id, symbol, name, pair}
    """
    # CoinGecko top-markets (max per_page=250)
    cg = get("https://api.coingecko.com/api/v3/coins/markets",
             params={"vs_currency":"usd","order":"market_cap_desc","per_page":250,"page":1})
    out = []
    for row in cg:
        sym = (row.get("symbol") or "").upper()
        name = row.get("name") or sym
        pair = f"{sym}USDT"
        # check of Binance het pair kent (451/404 -> skip)
        try:
            _ = get("https://api.binance.com/api/v3/ticker/24hr", params={"symbol": pair})
            out.append({"id": row["id"], "symbol": sym, "name": name, "pair": pair})
        except Exception:
            continue
        if len(out) >= limit:
            break
        time.sleep(0.1)  # klein rustmomentje tegen rate limits
    return out

if __name__ == "__main__":
    coins = get_top_coins(10)
    print(coins)
