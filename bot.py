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
# 1. AYARLAR & DURUM HAFIZASI
# ----------------------------------------------------
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8623857901:AAH2mMnEC4qMjG3fdpimhZyA4bFDgTqvugM").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ALL_SUPPORTED_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "SUIUSDT",
    "NEARUSDT", "ARBUSDT", "OPUSDT", "PEPEUSDT", "SHIBUSDT"
]

bot_state = {
    "enabled": True,
    "threshold": 100000,
    "tracked_coins": [c.upper() for c in ALL_SUPPORTED_COINS],
    "last_update_id": 0,
    "last_alert_time": 0
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. RENDER SAĞLIK KONTROLÜ (HTTP LIVE)
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
            {"text": "🪙 Coin Filtreleri", "callback_data": "cmd_coin_menu"},
            {"text": "📊 Durum", "callback_data": "cmd_status"}
        ],
        [
            {"text": "🔄 Fabrika Ayarları", "callback_data": "cmd_reset"}
        ]
    ]

def get_coin_keyboard():
    return [
        [
            {"text": "🟡 Sadece BTC", "callback_data": "cmd_only_btc"},
            {"text": "🔷 Sadece ETH", "callback_data": "cmd_only_eth"},
            {"text": "🟣 Sadece SOL", "callback_data": "cmd_only_sol"}
        ],
        [
            {"text": "🌐 Tüm 15 Coin (Hepsi)", "callback_data": "cmd_all_coins"}
        ],
        [
            {"text": "🔙 Ana Kumandaya Dön", "callback_data": "cmd_main_menu"}
        ]
    ]

def format_coin_list(coins):
    return ", ".join([c.replace("USDT", "") for c in coins])

# ----------------------------------------------------
# 4. TELEGRAM KOMUT MOTORU
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
        coins_str = format_coin_list(bot_state["tracked_coins"])
        send_telegram_msg(f"▶️ <b>WhaleRadar 7/24 Bulut Aktif!</b>\n\n• <b>Alarm Eşiği:</b> ${bot_state['threshold']:,.0f}\n• <b>İzlenenler:</b> {coins_str}", sender_id)

    elif c in ["sifirla", "reset", "cmd_reset"]:
        bot_state["enabled"] = True
        bot_state["threshold"] = 100000
        bot_state["tracked_coins"] = [c.upper() for c in ALL_SUPPORTED_COINS]
        send_telegram_msg("🔄 <b>WhaleRadar Fabrika Ayarlarına Sıfırlandı!</b>\n\n• Durum: 🟢 Aktif\n• Alarm Limiti: <b>$100,000</b>\n• İzlenen: <b>15 Büyük Kripto</b>", sender_id)

    elif c in ["menu", "kumanda", "yardim", "help", "cmd_main_menu"]:
        coins_str = format_coin_list(bot_state["tracked_coins"])
        msg = (
            f"🎮 <b>WHALERADAR KONTROL MERKEZİ</b>\n\n"
            f"• <b>Aktif Eşik:</b> <code>${bot_state['threshold']:,.0f}</code>\n"
            f"• <b>Durum:</b> {'🟢 Aktif' if bot_state['enabled'] else '🔴 Susturuldu'}\n"
            f"• <b>İzlenen Coinler:</b> <code>{coins_str}</code>\n\n"
            f"Aşağıdan hızlı eşik/coin seçebilir veya serbest komut yazabilirsiniz:"
        )
        send_telegram_msg(msg, sender_id, inline_keyboard=get_control_keyboard())

    elif c in ["coin", "coinler", "parite", "cmd_coin_menu"]:
        coins_str = format_coin_list(bot_state["tracked_coins"])
        msg = (
            f"🪙 <b>KRİPTO PARİTE FİLTRESİ</b>\n\n"
            f"• <b>Şu An İzlenenler:</b>\n<code>{coins_str}</code>\n\n"
            f"• Tek coine odaklan: <code>btc</code>, <code>sol</code>, <code>eth</code>\n"
            f"• Özel liste: <code>ekle avax link doge</code>\n"
            f"• Listeden sil: <code>cikar pepe shib</code>\n"
            f"• Hepsini aç: <code>tumu</code>"
        )
        send_telegram_msg(msg, sender_id, inline_keyboard=get_coin_keyboard())

    elif c == "cmd_only_btc" or c in ["btc", "sadece btc"]:
        bot_state["tracked_coins"] = ["BTCUSDT"]
        send_telegram_msg("🎯 <b>Odak Modu:</b> Sadece <b>BTCUSDT</b> balinaları izleniyor!", sender_id)

    elif c == "cmd_only_eth" or c in ["eth", "sadece eth"]:
        bot_state["tracked_coins"] = ["ETHUSDT"]
        send_telegram_msg("🎯 <b>Odak Modu:</b> Sadece <b>ETHUSDT</b> balinaları izleniyor!", sender_id)

    elif c == "cmd_only_sol" or c in ["sol", "sadece sol"]:
        bot_state["tracked_coins"] = ["SOLUSDT"]
        send_telegram_msg("🎯 <b>Odak Modu:</b> Sadece <b>SOLUSDT</b> balinaları izleniyor!", sender_id)

    elif c in ["cmd_all_coins", "tumu", "hepsi", "all", "tum coinler"]:
        bot_state["tracked_coins"] = [c.upper() for c in ALL_SUPPORTED_COINS]
        send_telegram_msg("🌐 <b>Tüm Piyasa Modu:</b> 15 kripto paranın tümü izleniyor!", sender_id)

    elif c in ["durum", "status", "rapor", "cmd_status"]:
        coins_str = format_coin_list(bot_state["tracked_coins"])
        status_text = (
            f"☁️ <b>7/24 BULUT DURUM RAPORU</b>\n\n"
            f"• <b>Durum:</b> {'🟢 AKTİF (7/24)' if bot_state['enabled'] else '🔴 SUSTURULDU'}\n"
            f"• <b>Alarm Eşiği:</b> <b>${bot_state['threshold']:,.0f}</b>\n"
            f"• <b>İzlenen Pariteler ({len(bot_state['tracked_coins'])} adet):</b>\n<code>{coins_str}</code>\n"
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
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $1,000,000 (Mega Balina)", sender_id)

    elif c.startswith("ekle ") or c.startswith("add "):
        parts = c.replace("ekle", "").replace("add", "").replace(",", " ").split()
        added = []
        for p in parts:
            sym = p.strip().upper()
            if not sym.endswith("USDT"): sym += "USDT"
            if sym not in bot_state["tracked_coins"]:
                bot_state["tracked_coins"].append(sym)
                added.append(sym.replace("USDT", ""))
        if added:
            send_telegram_msg(f"✅ <b>Listeye Eklendi:</b> {', '.join(added)}\n\nGüncel: <code>{format_coin_list(bot_state['tracked_coins'])}</code>", sender_id)
        else:
            send_telegram_msg("ℹ️ Belirttiğiniz coinler zaten listenizde mevcut.", sender_id)

    elif c.startswith("cikar ") or c.startswith("sil ") or c.startswith("remove "):
        parts = c.replace("cikar", "").replace("sil", "").replace("remove", "").replace(",", " ").split()
        removed = []
        for p in parts:
            sym = p.strip().upper()
            if not sym.endswith("USDT"): sym += "USDT"
            if sym in bot_state["tracked_coins"]:
                bot_state["tracked_coins"].remove(sym)
                removed.append(sym.replace("USDT", ""))
        if removed:
            if not bot_state["tracked_coins"]:
                bot_state["tracked_coins"] = ["BTCUSDT"]
            send_telegram_msg(f"🗑️ <b>Listeden Çıkarıldı:</b> {', '.join(removed)}\n\nKalan: <code>{format_coin_list(bot_state['tracked_coins'])}</code>", sender_id)
        else:
            send_telegram_msg("ℹ️ Belirttiğiniz coinler zaten listenizde yoktu.", sender_id)

    elif c.startswith("esik") or c.startswith("limit") or c.startswith("threshold") or parse_smart_amount(c):
        raw_val = c.replace("esik", "").replace("limit", "").replace("threshold", "").strip()
        parsed = parse_smart_amount(raw_val)
        if parsed and parsed >= 1000:
            bot_state["threshold"] = parsed
            send_telegram_msg(f"🎯 <b>Alarm eşiği ayarlandı:</b> ${parsed:,.0f}", sender_id)
        else:
            send_telegram_msg("⚠️ Geçersiz limit formatı.", sender_id)

# ----------------------------------------------------
# 5. TELEGRAM POLLER
# ----------------------------------------------------
def telegram_poller_loop():
    log("Telegram dinleyici başlatılıyor...")
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=False", timeout=10)
    except Exception:
        pass

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
        except Exception:
            time.sleep(2)
        time.sleep(0.5)

# ----------------------------------------------------
# 6. BYBIT CANLI BALİNA AKIŞI (US İP ENGELSİZ)
# ----------------------------------------------------
async def crypto_websocket_task():
    # Bybit Spot Public WebSocket (US IP engeli yoktur)
    ws_url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                log("Bybit Küresel Canlı Akışına Başarıyla Bağlanıldı!")
                
                # Tüm paritelerin trade akışına abone ol
                sub_params = [f"publicTrade.{coin}" for coin in ALL_SUPPORTED_COINS]
                sub_msg = {"op": "subscribe", "args": sub_params}
                await ws.send(json.dumps(sub_msg))

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    
                    if "data" not in data or not isinstance(data["data"], list):
                        continue

                    for trade in data["data"]:
                        symbol = trade.get("s", "").upper()
                        if symbol not in bot_state["tracked_coins"]:
                            continue

                        price = float(trade.get("p", 0))
                        qty = float(trade.get("v", 0))
                        total_usd = price * qty
                        side = trade.get("S", "").upper() # Buy / Sell
                        trade_type = "BUY" if side == "BUY" else "SELL"

                        if bot_state["enabled"] and total_usd >= bot_state["threshold"]:
                            now = time.time()
                            if now - bot_state["last_alert_time"] >= 2:
                                bot_state["last_alert_time"] = now
                                icon = "🟢 ALIM" if trade_type == "BUY" else "🔴 SATIM"
                                alert_msg = (
                                    f"🐋 <b>7/24 BALİNA ALARMI!</b>\n\n"
                                    f"<b>Parite:</b> {symbol}\n"
                                    f"<b>Tür:</b> {icon}\n"
                                    f"<b>Tutar:</b> ${total_usd:,.0f}\n"
                                    f"<b>Fiyat:</b> ${price:,.2f}"
                                )
                                send_telegram_msg(alert_msg)
        except Exception as e:
            log(f"WS Akış Hatası: {e}. 3 sn sonra tekrar bağlanıyor...")
            await asyncio.sleep(3)

# ----------------------------------------------------
# ANA ÇALIŞTIRICI
# ----------------------------------------------------
if __name__ == "__main__":
    log("WhaleRadar Cloud Engine Başlatılıyor...")
    
    http_t = threading.Thread(target=run_http_server, daemon=True)
    http_t.start()

    tg_t = threading.Thread(target=telegram_poller_loop, daemon=True)
    tg_t.start()

    asyncio.run(crypto_websocket_task())