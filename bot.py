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
# 1. AYARLAR & YAPILANDIRMA
# ----------------------------------------------------
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8623857901:AAH2mMnEC4qMjG3fdpimhZyA4bFDgTqvugM").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

CONFIG_FILE = "bot_state_config.json"

DEFAULT_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "SUIUSDT",
    "NEARUSDT", "ARBUSDT", "OPUSDT", "PEPEUSDT", "SHIBUSDT"
]

def load_saved_state():
    default_state = {
        "enabled": True,
        "threshold": 100000.0,
        "tracked_coins": list(DEFAULT_COINS),
        "chat_id": TELEGRAM_CHAT_ID,
        "last_update_id": 0
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_state.update(data)
                default_state["threshold"] = float(default_state.get("threshold", 100000.0))
        except Exception:
            pass
    return default_state

def save_current_state():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "enabled": bot_state["enabled"],
                "threshold": float(bot_state["threshold"]),
                "tracked_coins": bot_state["tracked_coins"],
                "chat_id": bot_state.get("chat_id", "")
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

bot_state = load_saved_state()
CURRENT_THRESHOLD = float(bot_state["threshold"])

if bot_state.get("chat_id"):
    TELEGRAM_CHAT_ID = str(bot_state["chat_id"])

tracked_coins_set = set(bot_state["tracked_coins"])
ws_subscribe_queue = []
telegram_outbox = queue.Queue()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. RENDER SAĞLIK KONTROLÜ
# ----------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"WhaleMetric Turbo Engine OK")

    def log_message(self, format, *args):
        return

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    server.serve_forever()

# ----------------------------------------------------
# 3. TURBO TELEGRAM MESAJ GÖNDERİCİ (SIFIR GECİKME)
# ----------------------------------------------------
def telegram_worker():
    session = requests.Session()
    # TCP soketini açık tutarak el sıkışma süresini sıfıra indirir
    adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)

    while True:
        try:
            # get(block=True) ile mesaj geldiği mikrosaniyede uyanır
            item = telegram_outbox.get(block=True)
            if item is None:
                break
            text, target_cid, kb = item
            cid = target_cid or bot_state.get("chat_id") or TELEGRAM_CHAT_ID
            if TELEGRAM_BOT_TOKEN and cid:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": str(cid), "text": text, "parse_mode": "HTML"}
                if kb:
                    payload["reply_markup"] = {"inline_keyboard": kb}
                session.post(url, json=payload, timeout=4)
            telegram_outbox.task_done()
        except Exception as e:
            pass

def send_telegram_msg(text, target_chat_id=None, inline_keyboard=None):
    telegram_outbox.put_nowait((text, target_chat_id, inline_keyboard))

def clear_outbox():
    while not telegram_outbox.empty():
        try:
            telegram_outbox.get_nowait()
            telegram_outbox.task_done()
        except Exception:
            break

# ----------------------------------------------------
# 4. EŞİK & PARİTE AYRIŞTIRICI
# ----------------------------------------------------
def extract_threshold_value(text):
    t = text.lower().replace(",", "").replace("$", "").replace("esik", "").replace("limit", "").strip()
    match = re.search(r'^([0-9]+(?:\.[0-9]+)?)\s*([km])?$', t)
    if match:
        val = float(match.group(1))
        unit = match.group(2)
        if unit == 'k':
            return val * 1000.0
        elif unit == 'm':
            return val * 1000000.0
        else:
            return val * 1000.0 if val < 500 else val
    return None

def normalize_coin_symbol(sym):
    s = sym.strip().upper()
    return s if s.endswith("USDT") else s + "USDT"

def format_coin_list(coins):
    return ", ".join([c.replace("USDT", "") for c in coins])

def get_control_keyboard():
    return [
        [{"text": "🛑 Durdur", "callback_data": "cmd_stop"}, {"text": "▶️ Başlat", "callback_data": "cmd_start"}],
        [{"text": "🎯 $2k", "callback_data": "cmd_set_2k"}, {"text": "🎯 $10k", "callback_data": "cmd_set_10k"}, {"text": "🎯 $50k", "callback_data": "cmd_set_50k"}],
        [{"text": "🎯 $100k", "callback_data": "cmd_set_100k"}, {"text": "🎯 $250k", "callback_data": "cmd_set_250k"}, {"text": "🎯 $1M", "callback_data": "cmd_set_1m"}],
        [{"text": "🪙 Coin Kumandası", "callback_data": "cmd_coin_menu"}, {"text": "📊 Durum", "callback_data": "cmd_status"}],
        [{"text": "🔄 Fabrika Ayarları ($100k)", "callback_data": "cmd_reset"}]
    ]

def get_coin_keyboard():
    return [
        [{"text": "🟡 BTC", "callback_data": "cmd_only_btc"}, {"text": "🔷 ETH", "callback_data": "cmd_only_eth"}, {"text": "🟣 SOL", "callback_data": "cmd_only_sol"}],
        [{"text": "🔥 Top 3 (BTC+ETH+SOL)", "callback_data": "cmd_top3"}, {"text": "🌐 Tüm Liste", "callback_data": "cmd_all_coins"}],
        [{"text": "🔙 Ana Menü", "callback_data": "cmd_main_menu"}]
    ]

# ----------------------------------------------------
# 5. KOMUT MOTORU
# ----------------------------------------------------
def handle_telegram_command(cmd_text, sender_id):
    global bot_state, TELEGRAM_CHAT_ID, tracked_coins_set, ws_subscribe_queue, CURRENT_THRESHOLD
    TELEGRAM_CHAT_ID = str(sender_id)
    bot_state["chat_id"] = str(sender_id)
    raw = cmd_text.strip()
    c = raw.lower()
    if c.startswith("/"): c = c[1:].strip()

    # Eşik Girişi
    possible_thresh = extract_threshold_value(c)
    if possible_thresh is not None and possible_thresh >= 500:
        CURRENT_THRESHOLD = float(possible_thresh)
        bot_state["threshold"] = CURRENT_THRESHOLD
        clear_outbox()
        save_current_state()
        send_telegram_msg(f"⚡ <b>Hız: TURBO AKTİF</b>\n🎯 <b>Yeni Eşik:</b> ${CURRENT_THRESHOLD:,.0f}", sender_id)
        return

    if c.startswith("cmd_set_"):
        val_map = {"2k": 2000.0, "10k": 10000.0, "50k": 50000.0, "100k": 100000.0, "250k": 250000.0, "500k": 500000.0, "1m": 1000000.0}
        key = c.replace("cmd_set_", "")
        if key in val_map:
            CURRENT_THRESHOLD = float(val_map[key])
            bot_state["threshold"] = CURRENT_THRESHOLD
            clear_outbox()
            save_current_state()
            send_telegram_msg(f"🎯 <b>Alarm eşiği:</b> ${CURRENT_THRESHOLD:,.0f}", sender_id)
            return

    if c in ["dur", "stop", "durdur", "kapat", "cmd_stop"]:
        bot_state["enabled"] = False
        clear_outbox()
        save_current_state()
        send_telegram_msg("🛑 <b>WhaleMetric Susturuldu!</b>", sender_id)

    elif c in ["baslat", "start", "ac", "calistir", "cmd_start"]:
        bot_state["enabled"] = True
        save_current_state()
        send_telegram_msg(f"▶️ <b>WhaleMetric Turbo Aktif!</b>\n\n• <b>Eşik:</b> ${CURRENT_THRESHOLD:,.0f}\n• <b>İzlenen:</b> {format_coin_list(bot_state['tracked_coins'])}", sender_id)

    elif c in ["sifirla", "reset", "cmd_reset"]:
        bot_state["enabled"] = True
        CURRENT_THRESHOLD = 100000.0
        bot_state["threshold"] = 100000.0
        bot_state["tracked_coins"] = list(DEFAULT_COINS)
        tracked_coins_set = set(DEFAULT_COINS)
        clear_outbox()
        save_current_state()
        send_telegram_msg("🔄 <b>Ayarlar Sıfırlandı!</b>\n• Eşik: $100,000", sender_id)

    elif c in ["menu", "kumanda", "yardim", "help", "cmd_main_menu"]:
        send_telegram_msg(
            f"🎮 <b>WHALEMETRIC TURBO PANEL</b>\n\n• <b>Eşik:</b> <code>${CURRENT_THRESHOLD:,.0f}</code>\n• <b>Durum:</b> {'🟢 Aktif' if bot_state['enabled'] else '🔴 Kapalı'}\n• <b>Coinler:</b> <code>{format_coin_list(bot_state['tracked_coins'])}</code>",
            sender_id,
            inline_keyboard=get_control_keyboard()
        )

    elif c in ["coin", "coinler", "parite", "cmd_coin_menu"]:
        send_telegram_msg(f"🪙 <b>COİN YÖNETİMİ</b>\n<code>{format_coin_list(bot_state['tracked_coins'])}</code>", sender_id, inline_keyboard=get_coin_keyboard())

    elif c == "cmd_only_btc":
        bot_state["tracked_coins"] = ["BTCUSDT"]
        tracked_coins_set = {"BTCUSDT"}
        save_current_state()
        send_telegram_msg("🎯 Sadece <b>BTCUSDT</b> izleniyor!", sender_id)

    elif c == "cmd_only_eth":
        bot_state["tracked_coins"] = ["ETHUSDT"]
        tracked_coins_set = {"ETHUSDT"}
        save_current_state()
        send_telegram_msg("🎯 Sadece <b>ETHUSDT</b> izleniyor!", sender_id)

    elif c == "cmd_only_sol":
        bot_state["tracked_coins"] = ["SOLUSDT"]
        tracked_coins_set = {"SOLUSDT"}
        save_current_state()
        send_telegram_msg("🎯 Sadece <b>SOLUSDT</b> izleniyor!", sender_id)

    elif c == "cmd_top3":
        bot_state["tracked_coins"] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        tracked_coins_set = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
        save_current_state()
        send_telegram_msg("🔥 <b>Top 3:</b> BTC, ETH, SOL izleniyor!", sender_id)

    elif c in ["cmd_all_coins", "tumu", "hepsi", "all"]:
        bot_state["tracked_coins"] = list(DEFAULT_COINS)
        tracked_coins_set = set(DEFAULT_COINS)
        save_current_state()
        send_telegram_msg("🌐 15 kripto paranın tümü izleniyor!", sender_id)

    elif c in ["durum", "status", "rapor", "cmd_status"]:
        send_telegram_msg(
            f"⚡ <b>TURBO BULUT RAPORU</b>\n\n• <b>Mod:</b> Sıfır Gecikme (Real-Time)\n• <b>Aktif Eşik:</b> <b>${CURRENT_THRESHOLD:,.0f}</b>\n• <b>İzlenen:</b> <code>{format_coin_list(bot_state['tracked_coins'])}</code>",
            sender_id
        )

    elif c.startswith("ekle ") or c.startswith("add "):
        tokens = re.sub(r'^(ekle|add)\s+', '', c).replace(",", " ").split()
        for t in tokens:
            sym = normalize_coin_symbol(t)
            if sym not in tracked_coins_set:
                bot_state["tracked_coins"].append(sym)
                tracked_coins_set.add(sym)
                ws_subscribe_queue.append(sym)
        save_current_state()
        send_telegram_msg(f"✅ <b>Eklendi:</b> <code>{format_coin_list(bot_state['tracked_coins'])}</code>", sender_id)

    elif c.startswith("cikar ") or c.startswith("sil "):
        tokens = re.sub(r'^(cikar|sil)\s+', '', c).replace(",", " ").split()
        for t in tokens:
            sym = normalize_coin_symbol(t)
            if sym in tracked_coins_set:
                bot_state["tracked_coins"].remove(sym)
                tracked_coins_set.remove(sym)
        if not bot_state["tracked_coins"]:
            bot_state["tracked_coins"] = ["BTCUSDT"]
            tracked_coins_set = {"BTCUSDT"}
        save_current_state()
        send_telegram_msg(f"🗑️ <b>Kalan:</b> <code>{format_coin_list(bot_state['tracked_coins'])}</code>", sender_id)

    elif any(k in c for k in ["sadece ", "only "]) or (c.replace(" ", "").isalpha() and len(c) <= 20):
        tokens = re.sub(r'^(sadece|only)\s+', '', c).replace(",", " ").split()
        new_list = [normalize_coin_symbol(t) for t in tokens if t]
        if new_list:
            bot_state["tracked_coins"] = list(dict.fromkeys(new_list))
            tracked_coins_set = set(bot_state["tracked_coins"])
            for sym in new_list:
                ws_subscribe_queue.append(sym)
            save_current_state()
            send_telegram_msg(f"🎯 <b>Takip Listesi:</b> <code>{format_coin_list(bot_state['tracked_coins'])}</code>", sender_id)

# ----------------------------------------------------
# 6. TELEGRAM DİNLEYİCİ
# ----------------------------------------------------
def telegram_poller_loop():
    session = requests.Session()
    try:
        session.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=False", timeout=4)
    except Exception:
        pass

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={bot_state['last_update_id'] + 1}&timeout=5"
            res = session.get(url, timeout=8).json()
            if res.get("ok") and res.get("result"):
                for update in res["result"]:
                    bot_state["last_update_id"] = update["update_id"]
                    if "callback_query" in update:
                        handle_telegram_command(update["callback_query"]["data"], str(update["callback_query"]["from"]["id"]))
                    elif "message" in update and "text" in update["message"]:
                        handle_telegram_command(update["message"]["text"], str(update["message"]["from"]["id"]))
        except Exception:
            time.sleep(0.5)
        time.sleep(0.1)

# ----------------------------------------------------
# 7. BYBIT CANLI SIFIR GECİKME WEBSOCKET
# ----------------------------------------------------
async def crypto_websocket_task():
    global ws_subscribe_queue, CURRENT_THRESHOLD
    ws_url = "wss://stream.bybit.com/v5/public/spot"

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=15, ping_timeout=15, max_size=10_000_000) as ws:
                log("Bybit Turbo Stream Aktif.")
                
                all_subs = list(set(DEFAULT_COINS + bot_state["tracked_coins"]))
                chunk_size = 5
                for i in range(0, len(all_subs), chunk_size):
                    chunk = all_subs[i:i + chunk_size]
                    sub_args = [f"publicTrade.{coin}" for coin in chunk]
                    await ws.send(json.dumps({"op": "subscribe", "args": sub_args}))

                while True:
                    if ws_subscribe_queue:
                        new_coins = list(ws_subscribe_queue)
                        ws_subscribe_queue.clear()
                        sub_args = [f"publicTrade.{c}" for c in new_coins]
                        await ws.send(json.dumps({"op": "subscribe", "args": sub_args}))

                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=20)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"op": "ping"}))
                        continue

                    data = json.loads(msg)
                    trades = data.get("data")
                    if not trades or not isinstance(trades, list):
                        continue

                    active_limit = CURRENT_THRESHOLD

                    # SIFIR GECİKMELİ İŞLEM MOTORU
                    for trade in trades:
                        sym = trade.get("s", "").upper()
                        if sym not in tracked_coins_set:
                            continue

                        price = float(trade.get("p", 0))
                        qty = float(trade.get("v", 0))
                        total_usd = price * qty

                        # Eşik kontrolü (Gecikmesiz, beklemesiz anında bildirim)
                        if bot_state["enabled"] and total_usd >= active_limit:
                            is_buy = trade.get("S", "").upper() == "BUY"
                            icon = "🟢 ALIM" if is_buy else "🔴 SATIM"
                            thresh_display = f"${int(active_limit/1000)}k" if active_limit >= 1000 else f"${int(active_limit)}"
                            alert_msg = (
                                f"⚡ <b>BALİNA ALARMI (>{thresh_display})</b>\n\n"
                                f"<b>Parite:</b> {sym}\n"
                                f"<b>Tür:</b> {icon}\n"
                                f"<b>Tutar:</b> ${total_usd:,.0f}\n"
                                f"<b>Fiyat:</b> ${price:,.2f}"
                            )
                            send_telegram_msg(alert_msg)
        except Exception as e:
            log(f"WS Hatası: {e}. Yeniden bağlanılıyor...")
            await asyncio.sleep(1)

# ----------------------------------------------------
# ÇALIŞTIRICI
# ----------------------------------------------------
if __name__ == "__main__":
    log(f"WhaleMetric Turbo Engine Başlatıldı. Eşik: ${CURRENT_THRESHOLD:,.0f}")
    
    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=telegram_worker, daemon=True).start()
    threading.Thread(target=telegram_poller_loop, daemon=True).start()

    asyncio.run(crypto_websocket_task())