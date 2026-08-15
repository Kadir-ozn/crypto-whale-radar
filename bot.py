import os
import sys
import json
import time
import asyncio
import threading
import requests
import websockets
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------------------------------------------
# 1. AYARLAR & ORTAM DEĞİŞKENLERİ
# ----------------------------------------------------
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8623857901:AAH2mMnEC4qMjG3fdpimhZyA4bFDgTqvugM").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

bot_state = {
    "enabled": True,
    "threshold": 100000,
    "last_update_id": 0,
    "last_alert_time": 0
}

COIN_LIST = [
    "btcusdt", "ethusdt", "solusdt", "bnbusdt", "xrpusdt",
    "avaxusdt", "linkusdt", "dogeusdt", "adausdt", "suiusdt",
    "nearusdt", "arbusdt", "opusdt", "pepeusdt", "shibusdt"
]

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. RENDER SAĞLIK KONTROLÜ (HTTP SERVER)
# ----------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"WhaleRadar 7/24 Cloud Bot Aktif!")

    def log_message(self, format, *args):
        return

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    log(f"Render HTTP Port {PORT} dinleniyor (Live)")
    server.serve_forever()

# ----------------------------------------------------
# 3. TELEGRAM MESAJ GÖNDERİCİ
# ----------------------------------------------------
def send_telegram_msg(text, target_chat_id=None, inline_keyboard=None):
    cid = target_chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        log(f"TG Gönderme Hatası: {e}")

def parse_smart_amount(text):
    try:
        t = text.lower().replace("$", "").replace(",", "").strip()
        mult = 1
        if t.endswith("k"):
            mult = 1000
            t = t[:-1]
        elif t.endswith("m"):
            mult = 1000000
            t = t[:-1]
        val = float(t)
        return val * mult
    except Exception:
        return None

def get_control_keyboard():
    return [
        [
            {"text": "🛑 Durdur", "callback_data": "cmd_stop"},
            {"text": "▶️ Başlat", "callback_data": "cmd_start"}
        ],
        [
            {"text": "🎯 $10k", "callback_data": "cmd_set_10k"},
            {"text": "🎯 $50k", "callback_data": "cmd_set_50k"},
            {"text": "🎯 $100k", "callback_data": "cmd_set_100k"}
        ],
        [
            {"text": "🎯 $250k", "callback_data": "cmd_set_250k"},
            {"text": "🎯 $500k", "callback_data": "cmd_set_500k"},
            {"text": "🎯 $1M", "callback_data": "cmd_set_1m"}
        ],
        [
            {"text": "📊 Durum", "callback_data": "cmd_status"},
            {"text": "🔄 Sıfırla", "callback_data": "cmd_reset"}
        ]
    ]

# ----------------------------------------------------
# 4. TELEGRAM KOMUT YÖNETİCİSİ
# ----------------------------------------------------
def handle_telegram_command(cmd_text, sender_id):
    global bot_state, TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID = str(sender_id)
    c = cmd_text.lower().strip()
    if c.startswith("/"):
        c = c[1:].strip()

    log(f"Gelen Komut: '{c}' (Kullanıcı: {sender_id})")

    if c in ["dur", "stop", "durdur", "kapat", "cmd_stop"]:
        bot_state["enabled"] = False
        send_telegram_msg("🛑 <b>WhaleRadar Bulut Servisi Susturuldu!</b>\nTekrar açmak için <code>baslat</code> yazın.", sender_id)

    elif c in ["baslat", "start", "ac", "calistir", "cmd_start"]:
        bot_state["enabled"] = True
        send_telegram_msg(f"▶️ <b>WhaleRadar 7/24 Bulut Aktif!</b>\nAlarm Eşiği: <b>${bot_state['threshold']:,.0f}</b>", sender_id)

    elif c in ["sifirla", "reset", "cmd_reset"]:
        bot_state["enabled"] = True
        bot_state["threshold"] = 100000
        send_telegram_msg("🔄 <b>WhaleRadar Fabrika Ayarlarına Sıfırlandı!</b>\n\n• Durum: 🟢 Aktif\n• Alarm Limiti: <b>$100,000</b>", sender_id)

    elif c in ["menu", "kumanda", "yardim", "help"]:
        msg = (
            f"🎮 <b>WHALERADAR 7/24 BULUT KUMANDASI</b>\n\n"
            f"• <b>Mevcut Eşik:</b> <code>${bot_state['threshold']:,.0f}</code>\n"
            f"• <b>Durum:</b> {'🟢 Aktif' if bot_state['enabled'] else '🔴 Susturuldu'}\n\n"
            f"Butonlarla seçebilir veya <code>esik 50k</code> yazabilirsiniz:"
        )
        send_telegram_msg(msg, sender_id, inline_keyboard=get_control_keyboard())

    elif c in ["durum", "status", "rapor", "cmd_status"]:
        status_text = (
            f"☁️ <b>7/24 BULUT DURUM RAPORU</b>\n\n"
            f"• <b>Durum:</b> {'🟢 AKTİF' if bot_state['enabled'] else '🔴 SUSTURULDU'}\n"
            f"• <b>Alarm Eşiği:</b> ${bot_state['threshold']:,.0f}\n"
            f"• <b>İzlenen Kriptolar:</b> 15 Parite\n"
            f"• <b>Sunucu:</b> Render Cloud Engine"
        )
        send_telegram_msg(status_text, sender_id)

    elif c == "cmd_set_10k":
        bot_state["threshold"] = 10000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $10,000", sender_id)

    elif c == "cmd_set_50k":
        bot_state["threshold"] = 50000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $50,000", sender_id)

    elif c == "cmd_set_100k":
        bot_state["threshold"] = 100000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $100,000", sender_id)

    elif c == "cmd_set_250k":
        bot_state["threshold"] = 250000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $250,000", sender_id)

    elif c == "cmd_set_500k":
        bot_state["threshold"] = 500000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $500,000", sender_id)

    elif c == "cmd_set_1m":
        bot_state["threshold"] = 1000000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $1,000,000", sender_id)

    elif c.startswith("esik") or c.startswith("limit") or c.startswith("threshold") or parse_smart_amount(c):
        raw_val = c.replace("esik", "").replace("limit", "").replace("threshold", "").strip()
        parsed = parse_smart_amount(raw_val)
        if parsed and parsed >= 1000:
            bot_state["threshold"] = parsed
            send_telegram_msg(f"🎯 <b>Alarm eşiği ayarlandı:</b> ${parsed:,.0f}", sender_id)
        else:
            send_telegram_msg("⚠️ Geçersiz limit. Örn: <code>esik 75k</code>", sender_id)

# ----------------------------------------------------
# 5. BAĞIMSIZ TELEGRAM DİNLEYİCİ THREAD
# ----------------------------------------------------
def telegram_poller_loop():
    log("Telegram dinleyici başlatılıyor...")
    # Olası Webhook kilitlerini temizle
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=False", timeout=10)
        log("Telegram Webhook kilidi temizlendi.")
    except Exception as e:
        log(f"Webhook sıfırlama uyarısı: {e}")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={bot_state['last_update_id'] + 1}&timeout=10"
            res = requests.get(url, timeout=15).json()
            if res.get("ok") and res.get("result"):
                for update in res["result"]:
                    bot_state["last_update_id"] = update["update_id"]

                    if "callback_query" in update:
                        cb = update["callback_query"]
                        sender_id = str(cb["from"]["id"])
                        handle_telegram_command(cb["data"], sender_id)

                    elif "message" in update and "text" in update["message"]:
                        msg = update["message"]
                        sender_id = str(msg["from"]["id"])
                        handle_telegram_command(msg["text"], sender_id)
        except Exception as e:
            time.sleep(2)
        time.sleep(0.5)

# ----------------------------------------------------
# 6. BINANCE WEBSOCKET AKIŞI
# ----------------------------------------------------
async def binance_websocket_task():
    streams = "/".join([f"{coin}@aggTrade" for coin in COIN_LIST])
    ws_url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                log("Binance Canlı Akışına Başarıyla Bağlanıldı.")
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg).get("data")
                    if not data or "s" not in data:
                        continue

                    symbol = data["s"]
                    price = float(data["p"])
                    qty = float(data["q"])
                    total_usd = price * qty
                    is_buyer_maker = data["m"]
                    trade_type = "SELL" if is_buyer_maker else "BUY"

                    if bot_state["enabled"] and total_usd >= bot_state["threshold"]:
                        now = time.time()
                        if now - bot_state["last_alert_time"] >= 2:
                            bot_state["last_alert_time"] = now
                            icon = "🟢 ALIM" if trade_type == "BUY" else "🔴 SATIM"
                            alert_msg = (
                                f"🐋 <b>7/24 CLOUD BALİNA ALARMI!</b>\n\n"
                                f"<b>Parite:</b> {symbol}\n"
                                f"<b>Tür:</b> {icon}\n"
                                f"<b>Tutar:</b> ${total_usd:,.0f}\n"
                                f"<b>Fiyat:</b> ${price:,.2f}"
                            )
                            send_telegram_msg(alert_msg)
        except Exception as e:
            log(f"Binance WS Hatası: {e}. 3 sn sonra tekrar bağlanıyor...")
            await asyncio.sleep(3)

# ----------------------------------------------------
# ANA ÇALIŞTIRICI
# ----------------------------------------------------
if __name__ == "__main__":
    log("WhaleRadar Cloud Engine Başlatılıyor...")
    
    # 1. HTTP Server Thread Başlat
    http_t = threading.Thread(target=run_http_server, daemon=True)
    http_t.start()

    # 2. Telegram Poller Thread Başlat
    tg_t = threading.Thread(target=telegram_poller_loop, daemon=True)
    tg_t.start()

    # 3. Binance Asyncio Döngüsü
    asyncio.run(binance_websocket_task())