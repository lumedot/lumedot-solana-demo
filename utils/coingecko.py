import os, requests, logging
log = logging.getLogger("coingecko")

def get_sol_price() -> float:
    """
    Fetch from coingecko.
    """
    url = os.getenv("COINGECKO_URL","https://api.coingecko.com/api/v3/simple/price")
    try:
        r = requests.get(url, params={"ids":"solana","vs_currencies":"usd"}, timeout=5)
        r.raise_for_status()
        return r.json()["solana"]["usd"]
    except Exception as e:
        log.error("coingecko error: %s", e)
        # fallback or raise
        raise
