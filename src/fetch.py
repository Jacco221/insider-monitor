from src.utils import get
import pandas as pd

def fetch_btc_price():
    # Binance public ticker endpoint
    url = "https://api.binance.com/api/v3/ticker/price"
    data = get(url, params={"symbol":"BTCUSDT"})
    price = float(data["price"])
    return pd.DataFrame([{"symbol": "BTC", "price_usd": price}])

if __name__ == "__main__":
    df = fetch_btc_price()
    print(df)
