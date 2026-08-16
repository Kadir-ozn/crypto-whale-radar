import os
import sys
import re
import json
import time
import asyncio
import threading
import queue
import requests
import websockets
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------------------------------------------
# 1. AYARLAR & SABİTLER
# ----------------------------------------------------
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_BOT_TOKEN = "8921763189:AAE3pCTrBLwUoKAqT25B8WqP6IKMTdCsQxU"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1140305780").strip()

DEFAULT_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "SUIUSDT",
    "NEARUSDT", "ARBUSDT", "OPUSDT", "PEPEUSDT", "SHIBUSDT"
]

VALID_CRYPTO_LIST = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "LINK", "DOGE",
    "ADA", "SUI", "NEAR", "ARB", "OP", "PEPE", "SHIB", "TON",
    "DOT", "MATIC", "POL", "APT", "INJ", "TIA", "RENDER", "FET"
}

CURRENT_ENABLED = True
CURRENT_THRESHOLD = 5000.0  # Varsayılan $5,000
tracked_coins_set = set(DEFAULT_COINS)

telegram_outbox = queue.Queue()
state_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. TELEGRAM GÖNDERİCİ MOTORU
# ----------------------------------------------------
def telegram_worker():
    global TELEGRAM_CHAT_ID
    session = requests.Session()
    while True:
        try:
            item = telegram_outbox.get()
            if item is None:
                break
            text, target_cid, kb = item
            cid = target_cid or TELEGRAM_CHAT_ID

            if not cid:
                telegram_outbox.task_done()
                continue

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": str(cid),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            if kb:
                payload["reply_markup"] = {"inline_keyboard": kb}

            resp = session.post(url, json=payload, timeout=5)
            if resp.ok:
                log(f"📤 Bildirim İletildi: ID {cid}")
            else:
                log(f"❌ Telegram Hatası: {resp.text}")
            telegram_outbox.task_done()
        except Exception as e:
            log(f"Worker Hatası: {e}")
        time.sleep(0.05)

def send_msg(text, target_cid=None, kb=None):
    telegram_outbox.put((text, target_cid, kb))

# ----------------------------------------------------
# 3. EL İLE KOMUT & BUTON İŞLEYİCİ
# ----------------------------------------------------
def handle_cmd(text, sender_id):
    global CURRENT_ENABLED, CURRENT_THRESHOLD, tracked_coins_set, TELEGRAM_CHAT_ID
    if sender_id:
        TELEGRAM_CHAT_ID = str(sender_id)

    if not text:
        return

    raw = str(text).strip()
    log(f"📥 GELEN GİRDİ: '{raw}' | Kullanıcı ID: {sender_id}")

    c = raw.lower().replace("/", "").replace("$", "").replace("usd", "").replace("usdt", "").strip()

    # 1. Menü & Durum
    if c in ["menu", "menü", "meu", "mnu", "kumanda", "panel", "yardim", "help", "durum", "status"]:
        st = "🟢 AKTİF" if CURRENT_ENABLED else "🔴 KAPALI"
        send_msg(
            f"📊 <b>WHALEMETRIC DURUMU</b>\n\n"
            f"• <b>Durum:</b> {st}\n"
            f"• <b>Aktif Eşik:</b> <code>${CURRENT_THRESHOLD:,.0f}</code>\n"
            f"• <b>İzlenen ({len(tracked_coins_set)}):</b> <code>{', '.join([x.replace('USDT','') for x in tracked_coins_set])}</code>",
            sender_id
        )
        return

    # 2. Durdur
    if c in ["dur", "stop", "durdur", "kapat", "sus", "off", "pause"]:
        with state_lock:
            CURRENT_ENABLED = False
        send_msg("🛑 <b>WhaleMetric Durduruldu!</b>\nBildirimler kapalı. Başlatmak için: <code>baslat</code>", sender_id)
        return

    # 3. Başlat
    if c in ["baslat", "start", "ac", "aç", "calistir", "çalıştır", "devam", "on", "resume"]:
        with state_lock:
            CURRENT_ENABLED = True
        send_msg(f"▶️ <b>WhaleMetric Aktif!</b>\n\n• Eşik: <b>${CURRENT_THRESHOLD:,.0f}</b>\n• İzlenen: <code>{len(tracked_coins_set)} Coin</code>", sender_id)
        return

    # 4. Hepsi / Tüm Pariteler
    if c in ["hepsi", "tumu", "tümü", "all", "sifirla", "sıfırla", "reset"]:
        with state_lock:
            tracked_coins_set = set(DEFAULT_COINS)
        send_msg(f"🌐 <b>Tüm Pariteler ({len(DEFAULT_COINS)}) Aktif Edildi!</b>", sender_id)
        return

    # 5. El İle Eşik Girişi (5k, 5000, esik 5k, 25k, 50, 100k vb.)
    clean_val = re.sub(r'^(?:esik|eşik|limit)\s*[:=]?\s*', '', c).strip()
    match = re.search(r'([0-9]+(?:[\.,][0-9]+)?)\s*([km])?', clean_val)
    if match:
        num_str = match.group(1).replace(",", ".")
        raw_val = float(num_str)
        unit = match.group(2)

        if unit == 'k':
            final_val = raw_val * 1000
        elif unit == 'm':
            final_val = raw_val * 1000000
        else:
            final_val = raw_val * 1000 if raw_val < 500 else raw_val

        with state_lock:
            CURRENT_THRESHOLD = float(final_val)

        send_msg(f"🎯 <b>Alarm Eşiği Güncellendi:</b> ${CURRENT_THRESHOLD:,.0f}", sender_id)
        return

    # 6. El İle Coin Girişi (btc eth sol, doge pepe vb.)
    tokens = [t.upper().replace("USDT", "") for t in re.split(r'[,\s+]+', raw) if t]
    if tokens and all(t in VALID_CRYPTO_LIST for t in tokens):
        new_set = {f"{t}USDT" for t in tokens}
        with state_lock:
            tracked_coins_set = new_set
        send_msg(f"🎯 <b>Takip Listesi Güncellendi:</b>\n<code>{', '.join(tokens)}</code>", sender_id)
        return

    send_msg(
        "❓ <b>Komut anlaşılamadı.</b>\n\n"
        "<b>Kullanım Örnekleri:</b>\n"
        "• <code>5k</code> veya <code>5000</code>\n"
        "• <code>50k</code> veya <code>100k</code>\n"
        "• <code>btc eth sol</code>\n"
        "• <code>hepsi</code>\n"
        "• <code>durdur</code> / <code>baslat</code> / <code>durum</code>",
        sender_id
    )

# ----------------------------------------------------
# 4. HTTP & WEB API SERVER (Senkronizasyon)
# ----------------------------------------------------
# ----------------------------------------------------
# 4. HTTP & WEB API SERVER (Senkronizasyon)
# ----------------------------------------------------
class WebApiHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        self._set_headers(200)
        with state_lock:
            thresh = CURRENT_THRESHOLD
            is_enabled = CURRENT_ENABLED
            coins = [c.replace("USDT", "") for c in tracked_coins_set]

        response_data = {
            "status": "ok",
            "threshold": thresh,
            "enabled": is_enabled,
            "tracked_coins": coins
        }
        self.wfile.write(json.dumps(response_data).encode("utf-8"))

    def do_POST(self):
        if self.path == "/api/command":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                cmd = data.get("command", "").strip()
                if cmd:
                    log(f"🌐 Web Siteden Komut: '{cmd}'")
                    handle_cmd(cmd, TELEGRAM_CHAT_ID)
                    self._set_headers(200)
                    with state_lock:
                        coins = [c.replace("USDT", "") for c in tracked_coins_set]
                        thresh = CURRENT_THRESHOLD
                        en = CURRENT_ENABLED
                    self.wfile.write(json.dumps({
                        "ok": True,
                        "threshold": thresh,
                        "enabled": en,
                        "tracked_coins": coins
                    }).encode("utf-8"))
                    return
            except Exception as e:
                log(f"API Hatası: {e}")

        self._set_headers(400)
        self.wfile.write(json.dumps({"ok": False}).encode("utf-8"))

    def log_message(self, format, *args):
        return

def run_http():
    try:
        server = HTTPServer(("0.0.0.0", PORT), WebApiHandler)
        server.serve_forever()
    except Exception as e:
        log(f"HTTP Server Hatası: {e}")

def telegram_poller():
    session = requests.Session()
    
    # Webhook temizliği
    try:
        del_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=True"
        r = session.get(del_url, timeout=10)
        log(f"🧹 Webhook Durumu: {r.json()}")
    except Exception as e:
        log(f"❌ Webhook Hatası: {e}")

    offset = 0
    log("🤖 Telegram dinleme döngüsü BAŞLADI.")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 5}
            resp = session.get(url, params=params, timeout=10)
            data = resp.json()

            if not data.get("ok"):
                log(f"❌ Telegram API Hatası: {data}")
                time.sleep(2)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1

                # Normal mesaj
                if "message" in upd:
                    msg = upd["message"]
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "")
                    log(f"🔥 MESAJ YAKALANDI: '{text}' (Kimden: {chat_id})")
                    handle_cmd(text, chat_id)

                # Buton tıklaması
                elif "callback_query" in upd:
                    cb = upd["callback_query"]
                    chat_id = cb.get("message", {}).get("chat", {}).get("id")
                    data_val = cb.get("data", "")
                    log(f"🔥 BUTON YAKALANDI: '{data_val}' (Kimden: {chat_id})")
                    handle_cmd(data_val, chat_id)

        except Exception as err:
            log(f"❌ Poller Döngü Hatası: {err}")
            time.sleep(2)
            
        time.sleep(0.2)
# ----------------------------------------------------
# 6. BYBIT SPOT WEBSOCKET (Canlı Filtreleme)
# ----------------------------------------------------
async def bybit_ws():
    ws_url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            log("🔗 Bybit WebSocket'e bağlanılıyor...")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                log("✅ Bybit WebSocket Bağlantısı Başarılı!")

                for coin in DEFAULT_COINS:
                    sub_payload = {"op": "subscribe", "args": [f"publicTrade.{coin}"]}
                    await ws.send(json.dumps(sub_payload))
                    await asyncio.sleep(0.02)

                log(f"✅ {len(DEFAULT_COINS)} parite dinlemeye alındı.")

                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)

                    if "topic" not in data:
                        continue

                    topic = data.get("topic", "")
                    sym = topic.replace("publicTrade.", "").upper()
                    trades = data.get("data", [])
                    if not isinstance(trades, list):
                        continue

                    for trade in trades:
                        if not CURRENT_ENABLED:
                            break

                        try:
                            p = float(trade.get("p", 0))
                            v = float(trade.get("v", 0))
                            total = p * v
                        except (ValueError, TypeError):
                            continue

                        with state_lock:
                            if sym not in tracked_coins_set:
                                continue
                            active_threshold = CURRENT_THRESHOLD

                        if total >= active_threshold:
                            side = str(trade.get("S", "")).upper()
                            icon = "🟢 ALIM" if side == "BUY" else "🔴 SATIM"
                            text = (
                                f"⚡ <b>BALİNA HAREKETİ</b>\n\n"
                                f"<b>Parite:</b> {sym}\n"
                                f"<b>İşlem:</b> {icon}\n"
                                f"<b>Tutar:</b> ${total:,.0f}\n"
                                f"<b>Fiyat:</b> ${p:,.2f}"
                            )
                            log(f"🚨 YAKALANDI: {sym} | {icon} | ${total:,.0f}")
                            send_msg(text)

        except Exception as e:
            log(f"⚠️ WS Hatası: {e}. 2 sn sonra yeniden bağlanılıyor...")
            await asyncio.sleep(2)

# ----------------------------------------------------
# 7. ANA ÇALIŞTIRICI
# ----------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=run_http, daemon=True).start()
    threading.Thread(target=telegram_worker, daemon=True).start()
    threading.Thread(target=telegram_poller, daemon=True).start()

    asyncio.run(bybit_ws())