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
# 1. AYARLAR
# ----------------------------------------------------
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8921763189:AAHX71Lw61KhUugdaa3tMSyTSbEHwB6Vj68").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

DEFAULT_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "ADAUSDT", "SUIUSDT",
    "NEARUSDT", "ARBUSDT", "OPUSDT", "PEPEUSDT", "SHIBUSDT"
]

CURRENT_ENABLED = True
CURRENT_THRESHOLD = 50000.0  # Test için 50k başlattık
tracked_coins_set = set(DEFAULT_COINS)

telegram_outbox = queue.Queue()
state_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. HTTP SERVER (Render Keep-Alive)
# ----------------------------------------------------
class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"WhaleMetric Running")
    def log_message(self, format, *args):
        return

def run_http():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheck)
        server.serve_forever()
    except Exception as e:
        log(f"HTTP Server Hatası: {e}")

# ----------------------------------------------------
# 3. TELEGRAM GÖNDERİCİ MOTORU
# ----------------------------------------------------
def telegram_worker():
    session = requests.Session()
    while True:
        try:
            item = telegram_outbox.get()
            if item is None:
                break
            text, target_cid, kb = item
            cid = target_cid or TELEGRAM_CHAT_ID
            if TELEGRAM_BOT_TOKEN and cid:
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
                if not resp.ok:
                    log(f"Telegram Gönderme Hatası: {resp.text}")
            telegram_outbox.task_done()
        except Exception as e:
            log(f"Worker Hatası: {e}")
        time.sleep(0.05)

def send_msg(text, target_cid=None, kb=None):
    telegram_outbox.put((text, target_cid, kb))

# ----------------------------------------------------
# 4. KOMUT AYRIŞTIRICI
# ----------------------------------------------------
# Bilinen kripto para listesi (Geçersiz kelimelerin coin sanılmasını engeller)
VALID_CRYPTO_LIST = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "LINK", "DOGE",
    "ADA", "SUI", "NEAR", "ARB", "OP", "PEPE", "SHIB", "TON",
    "DOT", "MATIC", "POL", "APT", "INJ", "TIA", "RENDER", "FET"
}

def handle_cmd(text, sender_id):
    global CURRENT_ENABLED, CURRENT_THRESHOLD, tracked_coins_set, TELEGRAM_CHAT_ID
    
    TELEGRAM_CHAT_ID = str(sender_id)
    raw = text.strip()
    c = raw.lower().replace("/", "").strip()

    log(f"İŞLENEN KOMUT: '{raw}' (Kullanıcı: {sender_id})")

    # 1. Menü Komutları (meu, mnu, menu gibi yazımları yakalar)
    if c in ["menu", "menü", "meu", "mnu", "kumanda", "panel", "yardim", "help"]:
        st = "🟢 AKTİF" if CURRENT_ENABLED else "🔴 KAPALI"
        send_msg(
            f"🎮 <b>WHALEMETRIC KONTROL PANELİ</b>\n\n"
            f"• <b>Durum:</b> {st}\n"
            f"• <b>Eşik:</b> <code>${CURRENT_THRESHOLD:,.0f}</code>\n"
            f"• <b>İzlenen:</b> <code>{', '.join([x.replace('USDT','') for x in tracked_coins_set])}</code>",
            sender_id
        )
        return

    # 2. Durdur
    if c in ["dur", "stop", "durdur", "kapat", "sus"]:
        CURRENT_ENABLED = False
        send_msg("🛑 <b>WhaleMetric Durduruldu!</b>\nBildirimler kapalı. Başlatmak için: <code>baslat</code>", sender_id)
        return

    # 3. Başlat
    if c in ["baslat", "start", "ac", "calistir", "devam"]:
        CURRENT_ENABLED = True
        send_msg(f"▶️ <b>WhaleMetric Aktif!</b>\n\n• Eşik: <b>${CURRENT_THRESHOLD:,.0f}</b>\n• İzlenen: <code>{len(tracked_coins_set)} Coin</code>", sender_id)
        return

    # 4. Eşik Değiştirme (2k, 10k, 50k, 100k, eşik 50k vb.)
    clean_thresh = c.replace("esik", "").replace("eşik", "").replace("limit", "").replace("$", "").strip()
    match = re.search(r'^([0-9]+(?:\.[0-9]+)?)\s*([km])?$', clean_thresh)
    if match:
        val = float(match.group(1))
        unit = match.group(2)
        if unit == 'k': val *= 1000
        elif unit == 'm': val *= 1000000
        elif val < 500: val *= 1000
        CURRENT_THRESHOLD = float(val)
        send_msg(f"🎯 <b>Alarm Eşiği Güncellendi:</b> ${CURRENT_THRESHOLD:,.0f}", sender_id)
        return

    # 5. Durum
    if c in ["durum", "status", "bilgi", "rapor"]:
        st = "🟢 AKTİF" if CURRENT_ENABLED else "🔴 KAPALI"
        send_msg(f"📊 <b>WhaleMetric Durumu</b>\n\n• Durum: {st}\n• Eşik: <b>${CURRENT_THRESHOLD:,.0f}</b>\n• İzlenen: <code>{', '.join([x.replace('USDT','') for x in tracked_coins_set])}</code>", sender_id)
        return

    # 6. Sadece Geçerli Coin Listesi Girildiyse (örn: btc eth sol)
    tokens = [t.upper().replace("USDT", "") for t in raw.replace(",", " ").split() if t]
    if tokens and all(t in VALID_CRYPTO_LIST for t in tokens):
        new_set = {f"{t}USDT" for t in tokens}
        with state_lock:
            tracked_coins_set = new_set
        send_msg(f"🎯 <b>Takip Listesi Güncellendi:</b>\n<code>{', '.join(tokens)}</code>", sender_id)
        return

    # 7. Tanınmayan Giriş
    send_msg("❓ <b>Komut anlaşılamadı.</b>\nÖrnekler:\n• <code>menu</code> (Kontrol paneli)\n• <code>50k</code> (Eşik belirler)\n• <code>btc eth sol</code> (Coin seçer)\n• <code>durdur</code> / <code>baslat</code>", sender_id)
# ----------------------------------------------------
# 5. TELEGRAM DİNLEYİCİ
# ----------------------------------------------------
def telegram_poller():
    log("Telegram dinleyici başlatılıyor...")
    session = requests.Session()
    
    # Webhook'u sıfırla
    try:
        session.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    offset = 0
    log("Telegram dinleyici aktif! Bota Telegram'dan mesaj yazabilirsiniz.")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
            r = session.get(url, timeout=15)
            data = r.json()

            if data.get("ok") and data.get("result"):
                for upd in data["result"]:
                    offset = upd["update_id"] + 1
                    if "message" in upd and "text" in upd["message"]:
                        sender_id = str(upd["message"]["from"]["id"])
                        msg_text = upd["message"]["text"]
                        log(f"📩 Telegram Mesajı Geldi: '{msg_text}'")
                        handle_cmd(msg_text, sender_id)
                    elif "callback_query" in upd:
                        sender_id = str(upd["callback_query"]["from"]["id"])
                        cb_data = upd["callback_query"]["data"]
                        handle_cmd(cb_data, sender_id)
        except Exception as e:
            log(f"Telegram Hatası: {e}")
            time.sleep(1)
        time.sleep(0.2)

# ----------------------------------------------------
# 6. BYBIT SPOT WEBSOCKET
# ----------------------------------------------------
async def bybit_ws():
    ws_url = "wss://stream.bybit.com/v5/public/spot"
    log("Bybit WebSocket motoru başlatılıyor...")

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                log("✅ Bybit WebSocket Bağlantısı Başarılı!")
                
                # Tüm coinlere abone ol
                sub_list = [f"publicTrade.{c}" for c in DEFAULT_COINS]
                await ws.send(json.dumps({"op": "subscribe", "args": sub_list}))

                while True:
                    msg = await ws.recv()
                    
                    if not CURRENT_ENABLED:
                        continue

                    data = json.loads(msg)
                    trades = data.get("data", [])
                    if not isinstance(trades, list):
                        continue

                    for trade in trades:
                        sym = trade.get("s", "").upper()
                        with state_lock:
                            if sym not in tracked_coins_set:
                                continue

                        p = float(trade.get("p", 0))
                        v = float(trade.get("v", 0))
                        total = p * v

                        if total >= CURRENT_THRESHOLD:
                            side = trade.get("S", "").upper()
                            icon = "🟢 ALIM" if side == "BUY" else "🔴 SATIM"
                            text = (
                                f"⚡ <b>BALİNA HAREKETİ</b>\n\n"
                                f"<b>Parite:</b> {sym}\n"
                                f"<b>İşlem:</b> {icon}\n"
                                f"<b>Tutar:</b> ${total:,.0f}\n"
                                f"<b>Fiyat:</b> ${p:,.2f}"
                            )
                            log(f"🚨 Balina Yakalandı: {sym} | ${total:,.0f}")
                            send_msg(text)
        except Exception as e:
            log(f"WebSocket Hatası ({e}). 2 sn sonra yeniden bağlanılıyor...")
            await asyncio.sleep(2)

# ----------------------------------------------------
# BAŞLATMA
# ----------------------------------------------------
if __name__ == "__main__":
    log("==========================================")
    log("WhaleMetric Bot Başlatılıyor...")
    log("==========================================")

    threading.Thread(target=run_http, daemon=True).start()
    threading.Thread(target=telegram_worker, daemon=True).start()
    threading.Thread(target=telegram_poller, daemon=True).start()

    asyncio.run(bybit_ws())