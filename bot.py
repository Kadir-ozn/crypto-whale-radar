import os
import json
import time
import asyncio
import requests
import websockets

# Ortam Değişkenleri (Render'dan veya yerelden okunur)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8623857901:AAH2mMnEC4qMjG3fdpimhZyA4bFDgTqvugM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Bot Durum Hafızası
bot_state = {
    "enabled": True,
    "threshold": 250000,       # Varsayılan $250,000
    "side_filter": "ALL",      # ALL, BUY_ONLY, SELL_ONLY
    "last_update_id": 0,
    "last_alert_time": 0
}

COIN_LIST = [
    "btcusdt", "ethusdt", "solusdt", "bnbusdt", "xrpusdt",
    "avaxusdt", "linkusdt", "dogeusdt", "adausdt", "suiusdt",
    "nearusdt", "arbusdt", "opusdt", "pepeusdt", "shibusdt"
]

def send_telegram_msg(text, inline_keyboard=None):
    """Telegram'a mesaj gönderir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
    
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[TG Hatası] {e}")

def parse_smart_amount(text):
    """'100k', '1.5m', '50000' gibi stringleri sayıya çevirir."""
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
    """Telefondan gelen komutları işler."""
    global bot_state
    c = cmd_text.lower().strip()
    if c.startswith("/"):
        c = c[1:].strip()

    if c in ["dur", "stop", "durdur", "kapat", "cmd_stop"]:
        bot_state["enabled"] = False
        send_telegram_msg("🛑 <b>WhaleRadar Bulut Servisi Susturuldu!</b>\nBildirimler durduruldu. Başlatmak için <code>baslat</code> yazın.")

    elif c in ["baslat", "start", "ac", "calistir", "cmd_start"]:
        bot_state["enabled"] = True
        send_telegram_msg(f"▶️ <b>WhaleRadar 7/24 Bulut Aktif!</b>\nAlarm Eşiği: <b>${bot_state['threshold']:,.0f}</b>\nBilgisayarın kapalı olsa bile balinalar buraya gelecek.")

    elif c in ["menu", "kumanda", "yardim", "help"]:
        kb = [
            [{"text": "🛑 Durdur", "callback_data": "cmd_stop"}, {"text": "▶️ Başlat", "callback_data": "cmd_start"}],
            [{"text": "🎯 $100k", "callback_data": "cmd_100k"}, {"text": "🎯 $250k", "callback_data": "cmd_250k"}, {"text": "🎯 $500k", "callback_data": "cmd_500k"}],
            [{"text": "📊 Durum Raporu", "callback_data": "cmd_status"}]
        ]
        send_telegram_msg("🎮 <b>WHALERADAR 7/24 BULUT KUMANDASI</b>\nSeçim yapabilir veya doğrudan mesaj yazabilirsiniz:", kb)

    elif c in ["durum", "status", "rapor", "cmd_status"]:
        status_text = (
            f"☁️ <b>7/24 BULUT RADAR DURUMU</b>\n\n"
            f"• <b>Durum:</b> {'🟢 AKTİF (7/24 ÇALIŞIYOR)' if bot_state['enabled'] else '🔴 SUSTURULDU'}\n"
            f"• <b>Alarm Limiti:</b> ${bot_state['threshold']:,.0f}\n"
            f"• <b>İzlenen Pariteler:</b> 15 Büyük Kripto\n"
            f"• <b>Sunucu:</b> Render Cloud Engine"
        )
        send_telegram_msg(status_text)

    elif c == "cmd_100k":
        bot_state["threshold"] = 100000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $100,000 olarak ayarlandı.")

    elif c == "cmd_250k":
        bot_state["threshold"] = 250000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $250,000 olarak ayarlandı.")

    elif c == "cmd_500k":
        bot_state["threshold"] = 500000
        send_telegram_msg("🎯 <b>Alarm eşiği:</b> $500,000 olarak ayarlandı.")

    elif c.startswith("esik") or c.startswith("limit") or c.startswith("threshold"):
        raw_val = c.replace("esik", "").replace("limit", "").replace("threshold", "").strip()
        parsed = parse_smart_amount(raw_val)
        if parsed and parsed >= 1000:
            bot_state["threshold"] = parsed
            send_telegram_msg(f"🎯 <b>Alarm eşiği:</b> ${parsed:,.0f} olarak güncellendi.")
        else:
            send_telegram_msg("⚠️ Geçersiz limit formatı. Örn: <code>esik 100k</code>")

async def telegram_poller_task():
    """Telegram'dan gelen komutları ve buton tıklamalarını 7/24 dinler."""
    global bot_state
    print("[Telegram Poller] Başlatıldı...")
    while True:
        try:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={bot_state['last_update_id'] + 1}&timeout=5"
                res = requests.get(url, timeout=10).json()
                if res.get("ok") and res.get("result"):
                    for update in res["result"]:
                        bot_state["last_update_id"] = update["update_id"]
                        
                        # Buton Tıklaması
                        if "callback_query" in update:
                            cb_data = update["callback_query"]["data"]
                            from_id = str(update["callback_query"]["from"]["id"])
                            if from_id == str(TELEGRAM_CHAT_ID):
                                handle_telegram_command(cb_data)

                        # Metin Komutu
                        elif "message" in update and "text" in update["message"]:
                            msg_text = update["message"]["text"]
                            from_id = str(update["message"]["from"]["id"])
                            if from_id == str(TELEGRAM_CHAT_ID):
                                handle_telegram_command(msg_text)
        except Exception as e:
            pass
        await asyncio.sleep(2)

async def binance_websocket_task():
    """Binance WebSocket akışını dinler ve balinaları Telegram'a fırlatır."""
    global bot_state
    streams = "/".join([f"{coin}@aggTrade" for coin in COIN_LIST])
    ws_url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    while True:
        try:
            print("[Binance WS] Bağlanıyor...")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print("[Binance WS] 7/24 Canlı Akış Bağlandı!")
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

                    # Eşik Kontrolü & Master Switch
                    if bot_state["enabled"] and total_usd >= bot_state["threshold"]:
                        now = time.time()
                        # Spam önleyici: 2 saniye aralık
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
            print(f"[Binance WS Hatası] {e}. 3 sn sonra yeniden bağlanıyor...")
            await asyncio.sleep(3)

async def main():
    # Başlangıç bildirimi
    send_telegram_msg("🚀 <b>WhaleRadar 7/24 Bulut Servisi Başlatıldı!</b>\nBilgisayarın kapalıyken bile arka planda çalışmaya devam edecek.")
    await asyncio.gather(
        telegram_poller_task(),
        binance_websocket_task()
    )

if __name__ == "__main__":
    asyncio.run(main())