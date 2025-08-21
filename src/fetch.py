from src.utils import get
import pandas as pd

def fetch_btc_price():
    url = "https://api.coindesk.com/v1/bpi/currentprice/BTC.json"
    data = get(url)
    price = data["bpi"]["USD"]["rate_float"]
    return pd.DataFrame([{"symbol": "BTC", "price_usd": price}])

if __name__ == "__main__":
    df = fetch_btc_price()
    print(df)
