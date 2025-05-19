import os
import requests
import secrets
import base58

from utils.coingecko import get_sol_price
from urllib.parse import urlencode, quote

LUMEDOT_API = os.getenv("LUMEDOT_API_ENDPOINT", "")
MERCHANT    = os.getenv("MERCHANT_WALLET", "")

def _random_ref() -> str:
    random_bytes = secrets.token_bytes(32)
    return base58.b58encode(random_bytes).decode("utf-8")


def create_sub_session(user_id: str, purchase_type: str):
    if not MERCHANT:
        raise RuntimeError("MERCHANT_WALLET not set")

    # fetch from host
    gql = """
    query {
      getSubscriptionPricing(subscriptionType:"lumedot_plus") {
        monthlyPrice
        yearlyPrice
      }
    }
    """
    resp = requests.post(LUMEDOT_API, json={"query": gql}, timeout=6).json()
    subp = resp["data"]["getSubscriptionPricing"]

    # monthly or yearly
    if purchase_type.lower() == "monthly":
        usd = subp["monthlyPrice"]
        memo_tag = "pl30"
    else:
        usd = subp["yearlyPrice"]
        memo_tag = "pl365"

    # 2) convert to SOL
    sol_price = get_sol_price()
    amount_sol = round(usd / sol_price, 6)

    # 3) build memo + reference
    memo = f"ud:{user_id} {memo_tag}"
    ref  = _random_ref()  # base58 string

    # Query params for "solana:" URL
    params = {
        "amount":    str(amount_sol),
        "reference": ref,
        "label":     "lumedot plus",
        "message":   "Subscription",
        "memo":      memo,
    }
    query = urlencode(params, quote_via=quote)

    return {
        "solanaPayUrl": f"solana:{MERCHANT}?{query}",
        "recipient":    MERCHANT,
        "amount":       amount_sol,
        "reference":    ref,                # base58-encoded 32-byte string
        "label":        "lumedot plus",
        "message":      "Subscription",
        "memo":         memo,
    }


def create_title_session(user_id: str, book_id: str, purchase_type: str):
    if not MERCHANT:
        raise RuntimeError("MERCHANT_WALLET not set")

    gql = f"""
    query {{
      getTitlePricing(bookId:"{book_id}") {{
        ebook_price
        audiobook_price
      }}
    }}
    """
    resp = requests.post(LUMEDOT_API, json={"query": gql}, timeout=6).json()
    pr   = resp["data"]["getTitlePricing"]

    if purchase_type.lower() == "ebook":
        usd       = pr["ebook_price"]
        memo_tag  = f"eb:{book_id}"
        label_txt = "eBook purchase"
    else:
        usd       = pr["audiobook_price"]
        memo_tag  = f"au:{book_id}"
        label_txt = "Audiobook purchase"

    sol_price  = get_sol_price()
    amount_sol = round(usd / sol_price, 6)

    memo = f"ud:{user_id} {memo_tag}"
    ref  = _random_ref()

    params = {
        "amount":    str(amount_sol),
        "reference": ref,
        "label":     "lumedot title",
        "message":   label_txt,
        "memo":      memo,
    }
    query = urlencode(params, quote_via=quote)

    return {
        "solanaPayUrl": f"solana:{MERCHANT}?{query}",
        "recipient":    MERCHANT,
        "amount":       amount_sol,
        "reference":    ref,
        "label":        "lumedot title",
        "message":      label_txt,
        "memo":         memo,
    }
