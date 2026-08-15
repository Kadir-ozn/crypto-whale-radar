import os
import json
import time
import asyncio
import requests
import websockets
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Render ücretsiz web servisi kontrolü için mini sunucu
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"WhaleRadar 7/24 Cloud Bot Aktif!")

def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# Ortam Değişkenleri
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8623857901:AAH2mMnEC4qMjG3fdpimhZyA4bFDgTqvugM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Bot Durum Hafızası
bot_state = {
    "enabled": True,
    "threshold": 250000,
    "side_filter": "ALL",
    "last_update_id": 0,
    "last_alert_time": 0
}

COIN_LIST = [
    "btcusdt", "ethusdt", "solusdt", "bnbusdt", "xrpusdt",
    "avaxusdt", "linkusdt", "dogeusdt", "adausdt", "suiusdt",
    "nearusdt", "arbusdt", "opusdt", "pepeusdt", "shibusdt"
]

def send_telegram_msg(text, inline_keyboard=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

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
    except:
        return None

def handle_telegram_command(cmd_text):
    global bot_state
    c = cmd_text.lower().strip()
    if c.startswith("/"): c = c[1:].strip()

    if c in ["dur", "stop", "durdur", "kapat", "cmd_stop"]:
        bot_state["enabled"] = False
        send_telegram_msg("🛑 <b>WhaleRadar Bulut Servisi Susturuldu!</b>\nTekrar açmak için <code>baslat</code> yazın.")
        
    elif c in ["baslat", "start", "ac", "calistir", "cmd_start"]:
        bot_state["enabled"] = True
        send_telegram_msg(f"▶️ <b>WhaleRadar 7/24 Bulut Aktif!</b>\nAlarm Eşiği: <b>${bot_state['threshold']:,.0f}</b>")
        
    elif c in ["sifirla", "reset", "cmd_reset"]:
        bot_state["enabled"] = True
        bot_state["threshold"] = 250000
        bot_state["side_filter"] = "ALL"
        send_telegram_msg("🔄 <b>WhaleRadar Fabrika Ayarlarına Sıfırlandı!</b>\n\n• Durum: 🟢 Aktif\n• Alarm Limiti: $250,000\n• Filtre: Alış + Satış")

    elif c in ["menu", "kumanda", "yardim", "help"]:
        kb = [
            [{"text": "🛑 Durdur", "callback_data": "cmd_stop"}, {"text": "▶️ Başlat", "callback_data": "cmd_start"}],
            [{"text": "🎯 $100k", "callback_data": "cmd_100k"}, {"text": "🎯 $250k", "callback_data": "cmd_250k"}, {"text": "🎯 $500k", "callback_data": "cmd_500k"}],
            [{"text": "📊 Durum", "callback_data": "cmd_status"}, {"text": "🔄 Sıfırla", "callback_data": "cmd_reset"}]
        ]
        send_telegram_msg("🎮 <b>WHALERADAR TELEFON KUMANDASI</b>", kb)

    elif c in ["durum", "status", "rapor", "cmd_status"]:
        status_text = (
            f"☁️ <b>7/24 BULUT DURUM RAPORU</b>\n\n"
            f"• <b>Durum:</b> {'🟢 AKTİF (7/24 ÇALIŞIYOR)' if bot_state['enabled'] else '🔴 SUSTURULDU'}\n"
            f"• <b>Alarm Eşiği:</b> ${bot_state['threshold']:,.0f}\n"
            f"• <b>İzlenen Pariteler:</b> 15 Büyük Kripto\n"
            f"• <b>Sunucu:</b> Render Cloud Engine"
        )
        send_telegram_msg(status_text)

    elif c == "cmd_100k":
        bot_state["threshold"] = 100000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $100,000")

    elif c == "cmd_250k":
        bot_state["threshold"] = 250000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $250,000")

    elif c == "cmd_500k":
        bot_state["threshold"] = 500000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $500,000")

    elif c.startswith("esik") or c.startswith("limit") or c.startswith("threshold"):
        raw_val = c.replace("esik", "").replace("limit", "").replace("threshold", "").strip()
        parsed = parse_smart_amount(raw_val)
        if parsed and parsed >= 1000:
            bot_state["threshold"] = parsed
            send_telegram_msg(f"🎯 <b>Alarm eşiği:</b> ${parsed:,.0f}")
        else:
            send_telegram_msg("⚠️ Geçersiz limit formatı. Örn: <code>esik 100k</code>")

async def telegram_poller_task():
    global bot_state
    while True:
        try:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                url = f"https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates?offset={bot_state['last_update_id'] + 1}&timeout=5"
                res = requests.get(url, timeout=10).json()
                if res.get("ok") and res.get("result"):
                    for update in res["result"]:
                        bot_state["last_update_id"] = update["update_id"]
                        if "callback_query" in update:
                            if str(update["callback_query"]["from"]["id"]) == str(TELEGRAM_CHAT_ID):
                                handle_telegram_command(update["callback_query"]["data"])
                        elif "message" in update and "text" in update["message"]:
                            if str(update["message"]["from"]["id"]) == str(TELEGRAM_CHAT_ID):
                                handle_telegram_command(update["message"]["text"])
        except:
            pass
        await asyncio.sleep(2)

async def binance_websocket_task():
    global bot_state
    streams = "/".join([f"{coin}@aggTrade" for coin in COIN_LIST])
    ws_url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg).get("data")
                    if not data or "s" not in data: continue

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
                            alert_msg = f"🐋 <b>7/24 CLOUD BALİNA ALARMI!</b>\n\n<b>Parite:</b> ${symbol}\n<b>Tür:</b> {icon}\n<b>Tutar:</b> ${total_usd:,.0f}\n<b>Fiyat:</b> ${price:,.2f}"
                            send_telegram_msg(alert_msg)
        except:
            await asyncio.sleep(3)

async def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    send_telegram_msg("🚀 <b>WhaleRadar 7/24 Bulut Servisi Başlatıldı!</b>\nBilgisayar kapalıyken de çalışır.")
    await asyncio.gather(telegram_poller_task(), binance_websocket_task())

if __name__ == "__main__":
    asyncio.run(main())