import os
import ssl
import json
import re
import certifi
import asyncio
import logging
import requests
import websockets

from solders.pubkey import Pubkey
from utils.coingecko import get_sol_price

# Logging Setup
log = logging.getLogger("helius_listener")
log.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(message)s"))
log.handlers[:] = [handler]

# Config
TOLERANCE = float(os.getenv("TOLERANCE_PCT", "0.05"))
_endpoint = os.getenv("LUMEDOT_API_ENDPOINT", "").rstrip("/")
if not _endpoint:
    log.error("LUMEDOT_API_ENDPOINT not set!")
HOST_API = _endpoint

# Heartbeat config
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "30"))  # seconds
PING_TIMEOUT = int(os.getenv("PING_TIMEOUT", "10"))  # seconds


class HeliusListener:
    def __init__(self):
        api_key = os.getenv("HELIUS_API_KEY", "")
        if not api_key:
            log.error("HELIUS_API_KEY not set!")
        self.ws_url  = f"wss://mainnet.helius-rpc.com/?api-key={api_key}"
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"

        merchant_str = os.getenv("MERCHANT_WALLET", "")
        if not merchant_str:
            log.error("MERCHANT_WALLET not set!")
        self.merchant = Pubkey.from_string(merchant_str)

        # SSL context trusting certifi’s CA bundle
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        
        # Heartbeat tracking
        self.last_pong = asyncio.get_event_loop().time()
        self.heartbeat_task = None

    async def heartbeat(self, ws):
        """Send periodic pings to keep the connection alive"""
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                
                # Check if received a pong lately
                current_time = asyncio.get_event_loop().time()
                if current_time - self.last_pong > HEARTBEAT_INTERVAL * 2:
                    log.warning("No pong received in %d seconds, connection may be dead", 
                              current_time - self.last_pong)
                
                # Send ping
                log.debug("Sending heartbeat ping")
                pong_waiter = await ws.ping()
                
                # Wait for pong with timeout
                try:
                    await asyncio.wait_for(pong_waiter, timeout=PING_TIMEOUT)
                    self.last_pong = current_time
                    log.debug("Received pong response")
                except asyncio.TimeoutError:
                    log.error("Ping timeout after %d seconds", PING_TIMEOUT)
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                log.warning("WebSocket connection closed during heartbeat")
                break
            except Exception as e:
                log.error("Error in heartbeat: %s", e)
                break

    async def start(self):
        """Start the WebSocket listener with auto-reconnection"""
        while True:
            try:
                await self._connect_and_listen()
            except Exception as e:
                log.error("Connection error: %s", e)
                log.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    async def _connect_and_listen(self):
        """Connect to WebSocket and start listening"""
        log.info("Connecting to Helius at %s", self.ws_url)
        
        async with websockets.connect(
            self.ws_url, 
            ssl=self.ssl_context,
            ping_interval=None,  # Handle pings manually
            ping_timeout=None,
            close_timeout=10
        ) as ws:
            
            # Initialize heartbeat tracking
            self.last_pong = asyncio.get_event_loop().time()
            
            # Start heartbeat task
            self.heartbeat_task = asyncio.create_task(self.heartbeat(ws))
            
            try:
                # Subscribe to logs that mention lumedot wallet
                sub_req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [str(self.merchant)]},
                        {"commitment": "confirmed"},
                    ],
                }
                await ws.send(json.dumps(sub_req))
                ack = await ws.recv()
                log.info("Logs subscription ACK: %s", ack)

                # Listen for messages
                while True:
                    raw = await ws.recv()
                    log.debug("WS RAW >>> %s", raw)
                    data = json.loads(raw).get("params", {}).get("result", {}).get("value", {})
                    sig  = data.get("signature")
                    logs_list = data.get("logs", [])
                    if sig:
                        log.info("Received signature: %s", sig)
                        # pass both signature and logs to handler
                        asyncio.create_task(self.handle_signature(sig, logs_list))
                        
            finally:
                # Cancel heartbeat task when connection ends
                if self.heartbeat_task and not self.heartbeat_task.done():
                    self.heartbeat_task.cancel()
                    try:
                        await self.heartbeat_task
                    except asyncio.CancelledError:
                        pass

    async def handle_signature(self, signature: str, logs_list: list[str]):
        log.debug("Handling signature %s", signature)
        try:
            # ── 1) Try to extract memo from Helius logs
            memo_text = None
            for line in logs_list:
                if line.startswith("Program log: Memo"):
                    # e.g. Program log: Memo (len 9): "ud:3 pl30"
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        memo_text = m.group(1)
                        log.debug("Parsed memo from logs: %s", memo_text)
                    break

            # ── 2) If no memo in logs, fetch transaction and parse SPL-Memo instruction
            if not memo_text:
                def fetch_tx():
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {"encoding": "jsonParsed", "commitment": "confirmed"},
                        ],
                    }
                    return requests.post(self.rpc_url, json=payload, timeout=10).json()

                raw_tx = await asyncio.to_thread(fetch_tx)
                msg = raw_tx.get("result", {}).get("transaction", {}).get("message", {})
                for ix in msg.get("instructions", []):
                    # JSON-parsed memo
                    if ix.get("parsed") and ix.get("program") == "spl-memo":
                        memo_text = ix["parsed"]["info"]["memo"]
                        break
                    # raw Memo program fallback
                    if ix.get("programId") == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                        memo_text = ix.get("data")
                        break
                log.debug("Parsed memo from on-chain: %s", memo_text)

            if not memo_text:
                log.warning("Could not find memo for %s, skipping", signature)
                return

            # ── 3) Fetch balances to compute sol_paid
            def fetch_balances():
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {"encoding": "jsonParsed", "commitment": "confirmed"},
                    ],
                }
                return requests.post(self.rpc_url, json=payload, timeout=10).json()

            raw_bal = await asyncio.to_thread(fetch_balances)
            meta = raw_bal.get("result", {}).get("meta", {})
            if not meta:
                log.debug("No meta for %s, skipping", signature)
                return

            keys_data = raw_bal["result"]["transaction"]["message"]["accountKeys"]
            keys = [(k["pubkey"] if isinstance(k, dict) else k) for k in keys_data]
            try:
                idx = keys.index(str(self.merchant))
            except ValueError:
                log.debug("Merchant not in accounts, skipping")
                return

            lamports = meta["postBalances"][idx] - meta["preBalances"][idx]
            if lamports <= 0:
                log.debug("No lamport credit, skipping")
                return

            sol_paid = lamports / 1e9
            log.info("Tx %s paid %.6f SOL", signature[:8], sol_paid)

            # ── 4) Parse memo_text (e.g. "ud:3 pl30")
            parts = memo_text.split()
            if len(parts) < 2 or not parts[0].startswith("ud:"):
                log.warning("Unexpected memo format: %s", memo_text)
                return
            user_id, plan = parts[0].split(":", 1)[1], parts[1]
            log.debug("Parsed user_id=%s plan=%s", user_id, plan)

            # ── 5) Build GraphQL mutation, trusting sol_paid as sol_expected
            if plan.startswith("pl"):
                sol_expected = sol_paid
                purchase_type = "monthly" if plan == "pl30" else "yearly"
                mutation = f"""
                  mutation {{
                    recordCompletedSubscriptionPurchase(
                      userId:"{user_id}",
                      subscriptionType:"lumedot_plus",
                      purchaseType:"{purchase_type}",
                      price:{sol_paid},
                      currency:"sol",
                      endDate:"2099-01-01",
                      txSignature:"{signature}",
                      reference:"{signature}"
                    ){{id}}
                  }}
                """
            else:
                sol_expected = sol_paid
                ttype = "ebook" if plan.startswith("eb:") else "audiobook"
                book_id = plan.split(":", 1)[1]
                mutation = f"""
                  mutation {{
                    recordCompletedTitlePurchase(
                      userId:"{user_id}",
                      bookId:{book_id},
                      purchaseType:"{ttype}",
                      price:{sol_paid},
                      currency:"sol",
                      txSignature:"{signature}",
                      reference:"{signature}"
                    ){{id}}
                  }}
                """

            log.info(
                "Expected %.6f SOL vs paid %.6f SOL (tol=%.2f%%)",
                sol_expected, sol_paid, TOLERANCE * 100,
            )
            if abs(sol_paid - sol_expected) / sol_expected > TOLERANCE:
                log.warning("Price outside tolerance, forwarding anyway")

            # ── 6) Send to host API
            log.debug("POST %s payload=%s", HOST_API, {"query": mutation})
            def do_post():
                return requests.post(
                    HOST_API,
                    json={"query": mutation},
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )

            resp = await asyncio.to_thread(do_post)
            log.info("Host response: %s %s", resp.status_code, resp.text.replace("\n", " "))
            if resp.status_code != 200:
                log.error("Mutation failed: %s", resp.text)

        except Exception:
            log.exception("Error in handle_signature")
