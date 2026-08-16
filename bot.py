import os
import sys
import re
import json
import time
import random
import sqlite3
import asyncio
import threading
import queue
import requests
import websockets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ----------------------------------------------------
# 1. AYARLAR & SABİTLER
# ----------------------------------------------------
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_BOT_TOKEN = "8921763189:AAE3pCTrBLwUoKAqT25B8WqP6IKMTdCsQxU"
DB_PATH = "radar_users.db"

DEFAULT_COINS = [
    # Majörler
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    
    # Popüler L1 / L2 & Altyapı
    "AVAXUSDT", "ADAUSDT", "SUIUSDT", "NEARUSDT", "APTUSDT",
    "LINKUSDT", "DOTUSDT", "ARBUSDT", "OPUSDT", "MATICUSDT",
    "FTMUSDT", "INJUSDT", "SEIUSDT", "TIAUSDT", "TONUSDT",
    
    # Yapay Zeka (AI) & Veri
    "FETUSDT", "RENDERUSDT", "TAOUSDT", "WLDUSDT",
    
    # Meme Tokenlar
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"
]

telegram_outbox = queue.Queue()
db_lock = threading.Lock()
pending_auth_codes = {}
auth_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. GENİŞLETİLMİŞ SQLITE VERİTABANI YÖNETİMİ
# ----------------------------------------------------
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id TEXT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                threshold REAL DEFAULT 50000.0,
                enabled INTEGER DEFAULT 1,
                tracked_coins TEXT DEFAULT 'ALL',
                theme TEXT DEFAULT 'theme-navy',
                voice_enabled INTEGER DEFAULT 0,
                sound_enabled INTEGER DEFAULT 0,
                side_filter TEXT DEFAULT 'ALL',
                created_at INTEGER
            )
        """)
        # Eski tablolara eksik sütunları güvenle ekle
        cols = [col[1] for col in c.execute("PRAGMA table_info(users)").fetchall()]
        if "theme" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'theme-navy'")
        if "voice_enabled" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN voice_enabled INTEGER DEFAULT 0")
        if "sound_enabled" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN sound_enabled INTEGER DEFAULT 0")
        if "side_filter" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN side_filter TEXT DEFAULT 'ALL'")
        conn.commit()
        conn.close()

def get_user_profile(chat_id):
    chat_id = str(chat_id).strip()
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT chat_id, first_name, threshold, enabled, tracked_coins, theme, voice_enabled, sound_enabled, side_filter 
            FROM users WHERE chat_id = ?
        """, (chat_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                "chat_id": row[0],
                "first_name": row[1],
                "threshold": float(row[2]),
                "enabled": bool(row[3]),
                "tracked_coins": row[4] or "ALL",
                "theme": row[5] or "theme-navy",
                "voice_enabled": bool(row[6]),
                "sound_enabled": bool(row[7]),
                "side_filter": row[8] or "ALL"
            }
        return None

def get_or_create_verified_user(chat_id, first_name="", username=""):
    chat_id = str(chat_id).strip()
    user = get_user_profile(chat_id)
    if user:
        return user
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        now = int(time.time())
        c.execute("""
            INSERT OR REPLACE INTO users (chat_id, username, first_name, threshold, enabled, tracked_coins, theme, voice_enabled, sound_enabled, side_filter, created_at)
            VALUES (?, ?, ?, 50000.0, 1, 'ALL', 'theme-navy', 0, 0, 'ALL', ?)
        """, (chat_id, username, first_name, now))
        conn.commit()
        conn.close()
    return get_user_profile(chat_id)

def update_user_settings(chat_id, settings_dict):
    chat_id = str(chat_id).strip()
    allowed_cols = ["threshold", "enabled", "tracked_coins", "theme", "voice_enabled", "sound_enabled", "side_filter", "first_name"]
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for k, v in settings_dict.items():
            if k in allowed_cols:
                if k in ["enabled", "voice_enabled", "sound_enabled"]:
                    val = 1 if v else 0
                elif k == "threshold":
                    val = float(v)
                else:
                    val = str(v)
                c.execute(f"UPDATE users SET {k} = ? WHERE chat_id = ?", (val, chat_id))
        conn.commit()
        conn.close()

def get_all_active_users():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT chat_id, threshold, tracked_coins, side_filter FROM users WHERE enabled = 1")
        rows = c.fetchall()
        conn.close()
        return [{"chat_id": r[0], "threshold": float(r[1]), "tracked_coins": r[2], "side_filter": r[3]} for r in rows]

# ----------------------------------------------------
# 3. TELEGRAM MESAJ GÖNDERİCİ
# ----------------------------------------------------
def telegram_worker():
    session = requests.Session()
    while True:
        try:
            item = telegram_outbox.get()
            if item is None:
                break
            text, target_cid, kb = item
            if not target_cid:
                telegram_outbox.task_done()
                continue

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": str(target_cid),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            if kb:
                payload["reply_markup"] = {"inline_keyboard": kb}

            resp = session.post(url, json=payload, timeout=5)
            if resp.ok:
                log(f"📤 Bildirim İletildi: ID {target_cid}")
            else:
                log(f"❌ Telegram Hatası: {resp.text}")
            telegram_outbox.task_done()
        except Exception as e:
            log(f"Worker Hatası: {e}")
        time.sleep(0.04)

def send_msg(text, target_cid=None, kb=None):
    telegram_outbox.put((text, target_cid, kb))

# ----------------------------------------------------
# 4. TELEGRAM KOMUT YÖNETİCİSİ
# ----------------------------------------------------
def handle_cmd(text, sender_id, sender_name=""):
    if not sender_id:
        return
    
    sender_id_str = str(sender_id).strip()
    user = get_user_profile(sender_id_str)
    
    if not user:
        send_msg(
            f"🔒 <b>WhaleMetric Üye Alanı</b>\n\n"
            f"Merhaba <b>{sender_name or 'Trader'}</b>, Telegram bildirimleri ve kişisel radar ayarları sadece kayıtlı üyelere özeldir.\n\n"
            f"👉 Web sitemize gidin ve <b>'Telegram ile Bağlan'</b> butonuna basarak <code>{sender_id_str}</code> ID'niz ile giriş yapın.",
            sender_id_str
        )
        return

    raw = str(text).strip()
    c = raw.lower().replace("/", "").replace("$", "").replace("usd", "").replace("usdt", "").strip()

    if c in ["menu", "menü", "panel", "durum", "status", "start"]:
        st = "🟢 AKTİF" if user["enabled"] else "🔴 KAPALI"
        send_msg(
            f"📊 <b>WHALEMETRIC KİŞİSEL PANELİNİZ</b>\n\n"
            f"• <b>Üye:</b> {user.get('first_name') or 'Trader'}\n"
            f"• <b>Durum:</b> {st}\n"
            f"• <b>Özel Eşik Değeriniz:</b> <code>${user['threshold']:,.0f}</code>\n"
            f"• <b>Kullanıcı ID:</b> <code>{sender_id_str}</code>",
            sender_id_str
        )
        return

    if c in ["dur", "stop", "durdur", "kapat", "off"]:
        update_user_settings(sender_id_str, {"enabled": False})
        send_msg("🛑 <b>Bildirimleriniz durduruldu!</b>\nTekrar açmak için: <code>baslat</code>", sender_id_str)
        return

    if c in ["baslat", "start", "ac", "aç", "on"]:
        update_user_settings(sender_id_str, {"enabled": True})
        send_msg(f"▶️ <b>Radarınız Aktif!</b>\nEşiğiniz: <b>${user['threshold']:,.0f}</b>", sender_id_str)
        return

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

        update_user_settings(sender_id_str, {"threshold": final_val})
        send_msg(f"🎯 <b>Size Özel Eşik Güncellendi:</b> ${final_val:,.0f}", sender_id_str)
        return

    send_msg("❓ <b>Komut anlaşılamadı.</b>\nÖrnekler: <code>10k</code>, <code>50k</code>, <code>durdur</code>, <code>baslat</code>", sender_id_str)

# ----------------------------------------------------
# 5. REST API (KİMLİK DOĞRULAMA & BULUT PROFİL)
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
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        chat_id = params.get("chat_id", [None])[0]

        if chat_id:
            user = get_user_profile(chat_id)
            if user:
                self._set_headers(200)
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "authenticated": True,
                    "user": user,
                    "threshold": user["threshold"],
                    "enabled": user["enabled"]
                }).encode("utf-8"))
                return
        
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "status": "ok",
            "authenticated": False,
            "threshold": 50000.0,
            "enabled": True
        }).encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body.decode("utf-8"))
            now = time.time()

            # 1. OTP Kod Gönderme
            if self.path == "/api/auth/send-code":
                chat_id = str(data.get("id", "")).strip()
                name = str(data.get("first_name", "")).strip() or "Trader"

                if not chat_id:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"ok": False, "msg": "Chat ID gerekli"}).encode("utf-8"))
                    return

                code = f"{random.randint(100000, 999999)}"
                with auth_lock:
                    pending_auth_codes[chat_id] = {
                        "code": code,
                        "expires_at": now + 180,
                        "name": name
                    }

                otp_msg = (
                    f"🔐 <b>WhaleMetric Giriş Doğrulama Kodu</b>\n\n"
                    f"Kodunuz: <code>{code}</code>\n\n"
                    f"⏱ Bu kod <b>3 dakika</b> boyunca geçerlidir. Kimseyle paylaşmayın."
                )
                send_msg(otp_msg, chat_id)

                self._set_headers(200)
                self.wfile.write(json.dumps({"ok": True, "msg": "Kod gönderildi"}).encode("utf-8"))
                return

            # 2. OTP Kodu Doğrulama & Giriş
            elif self.path == "/api/auth/verify-code":
                chat_id = str(data.get("id", "")).strip()
                code = str(data.get("code", "")).strip()

                with auth_lock:
                    record = pending_auth_codes.get(chat_id)

                    if not record:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"ok": False, "msg": "Önce kod talep edin!"}).encode("utf-8"))
                        return

                    if now > record["expires_at"]:
                        del pending_auth_codes[chat_id]
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"ok": False, "msg": "Kodun süresi doldu."}).encode("utf-8"))
                        return

                    if record["code"] != code:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"ok": False, "msg": "Hatalı doğrulama kodu!"}).encode("utf-8"))
                        return

                    name = record["name"]
                    del pending_auth_codes[chat_id]

                user = get_or_create_verified_user(chat_id, first_name=name)
                send_msg("✅ <b>WhaleMetric Web Terminaline başarıyla giriş yapıldı!</b>", chat_id)

                self._set_headers(200)
                self.wfile.write(json.dumps({"ok": True, "user": user}).encode("utf-8"))
                return

            # 3. Kullanıcı Ayarlarını Kaydetme (Eşik, Tema, Ses vb.)
            elif self.path in ["/api/command", "/api/user/save-settings"]:
                chat_id = str(data.get("chat_id", "")).strip()
                
                if not chat_id:
                    self._set_headers(403)
                    self.wfile.write(json.dumps({"ok": False, "msg": "Yetkisiz istek."}).encode("utf-8"))
                    return

                user = get_user_profile(chat_id)
                if not user:
                    self._set_headers(403)
                    self.wfile.write(json.dumps({"ok": False, "msg": "Kullanıcı bulunamadı."}).encode("utf-8"))
                    return

                # Eşik komutu geldiyse
                if "command" in data:
                    cmd = str(data.get("command", "")).strip()
                    handle_cmd(cmd, chat_id)
                
                # Toplu ayar objesi geldiyse (Tema, Ses, vb.)
                if "settings" in data and isinstance(data["settings"], dict):
                    update_user_settings(chat_id, data["settings"])

                updated_user = get_user_profile(chat_id)
                self._set_headers(200)
                self.wfile.write(json.dumps({
                    "ok": True,
                    "user": updated_user,
                    "threshold": updated_user["threshold"],
                    "enabled": updated_user["enabled"]
                }).encode("utf-8"))
                return

        except Exception as e:
            log(f"API Hatası: {e}")

        self._set_headers(400)
        self.wfile.write(json.dumps({"ok": False}).encode("utf-8"))

    def log_message(self, format, *args):
        return

def run_http():
    server = HTTPServer(("0.0.0.0", PORT), WebApiHandler)
    log(f"🚀 HTTP API Sunucusu port {PORT} üzerinde çalışıyor.")
    server.serve_forever()

# ----------------------------------------------------
# 6. TELEGRAM POLLER
# ----------------------------------------------------
def telegram_poller():
    session = requests.Session()
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 5}
            resp = session.get(url, params=params, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                time.sleep(2)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    msg = upd["message"]
                    chat_id = msg.get("chat", {}).get("id")
                    name = msg.get("chat", {}).get("first_name", "")
                    text = msg.get("text", "")
                    handle_cmd(text, chat_id, name)
        except Exception:
            time.sleep(2)
        time.sleep(0.2)

# ----------------------------------------------------
# 7. BYBIT WEBSOCKET
# ----------------------------------------------------
async def bybit_ws():
    ws_url = "wss://stream.bybit.com/v5/public/spot"
    while True:
        try:
            log("🔗 Bybit WebSocket'e bağlanılıyor...")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                log("✅ Bybit WebSocket Bağlantısı Başarılı!")
                for coin in DEFAULT_COINS:
                    await ws.send(json.dumps({"op": "subscribe", "args": [f"publicTrade.{coin}"]}))
                    await asyncio.sleep(0.02)

                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)
                    if "topic" not in data:
                        continue

                    sym = data.get("topic", "").replace("publicTrade.", "").upper()
                    trades = data.get("data", [])
                    if not isinstance(trades, list):
                        continue

                    for trade in trades:
                        try:
                            p = float(trade.get("p", 0))
                            v = float(trade.get("v", 0))
                            total = p * v
                        except (ValueError, TypeError):
                            continue

                        if total < 1000:
                            continue

                        active_users = get_all_active_users()
                        for u in active_users:
                            if total >= u["threshold"]:
                                side = str(trade.get("S", "")).upper()
                                icon = "🟢 ALIM" if side == "BUY" else "🔴 SATIM"
                                text = (
                                    f"⚡ <b>BALİNA HAREKETİ</b>\n\n"
                                    f"<b>Parite:</b> {sym}\n"
                                    f"<b>İşlem:</b> {icon}\n"
                                    f"<b>Tutar:</b> ${total:,.0f}\n"
                                    f"<b>Fiyat:</b> ${p:,.2f}"
                                )
                                send_msg(text, u["chat_id"])

        except Exception as e:
            log(f"⚠️ WS Hatası: {e}. 2 sn sonra yeniden bağlanılıyor...")
            await asyncio.sleep(2)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_http, daemon=True).start()
    threading.Thread(target=telegram_worker, daemon=True).start()
    threading.Thread(target=telegram_poller, daemon=True).start()
    asyncio.run(bybit_ws())