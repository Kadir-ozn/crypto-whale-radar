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
from datetime import datetime, timezone, timedelta
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

# 24 Saatlik İstatistik Hafızası
stats_lock = threading.Lock()
rolling_trades_24h = []

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ----------------------------------------------------
# 2. SQLITE VERİTABANI YÖNETİMİ
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
                daily_report INTEGER DEFAULT 1,
                report_time TEXT DEFAULT '09:00',
                created_at INTEGER
            )
        """)
        cols = [col[1] for col in c.execute("PRAGMA table_info(users)").fetchall()]
        if "theme" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'theme-navy'")
        if "voice_enabled" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN voice_enabled INTEGER DEFAULT 0")
        if "sound_enabled" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN sound_enabled INTEGER DEFAULT 0")
        if "side_filter" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN side_filter TEXT DEFAULT 'ALL'")
        if "daily_report" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN daily_report INTEGER DEFAULT 1")
        if "report_time" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN report_time TEXT DEFAULT '09:00'")
        conn.commit()
        conn.close()

def get_user_profile(chat_id):
    chat_id = str(chat_id).strip()
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT chat_id, first_name, threshold, enabled, tracked_coins, theme, voice_enabled, sound_enabled, side_filter, daily_report, report_time
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
                "side_filter": row[8] or "ALL",
                "daily_report": bool(row[9]),
                "report_time": row[10] or "09:00"
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
            INSERT OR REPLACE INTO users (chat_id, username, first_name, threshold, enabled, tracked_coins, theme, voice_enabled, sound_enabled, side_filter, daily_report, report_time, created_at)
            VALUES (?, ?, ?, 50000.0, 1, 'ALL', 'theme-navy', 0, 0, 'ALL', 1, '09:00', ?)
        """, (chat_id, username, first_name, now))
        conn.commit()
        conn.close()
    return get_user_profile(chat_id)

def update_user_settings(chat_id, settings_dict):
    chat_id = str(chat_id).strip()
    allowed_cols = ["threshold", "enabled", "tracked_coins", "theme", "voice_enabled", "sound_enabled", "side_filter", "daily_report", "report_time", "first_name"]
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for k, v in settings_dict.items():
            if k in allowed_cols:
                if k in ["enabled", "voice_enabled", "sound_enabled", "daily_report"]:
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

def get_due_daily_report_users(current_hhmm):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT chat_id, first_name FROM users WHERE daily_report = 1 AND report_time = ?", (current_hhmm,))
        rows = c.fetchall()
        conn.close()
        return [{"chat_id": r[0], "first_name": r[1]} for r in rows]

# ----------------------------------------------------
# 3. İSTATİSTİK VE GÜNLÜK RAPOR MOTORU
# ----------------------------------------------------
def record_trade_for_stats(trade_obj):
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (24 * 3600 * 1000)
    with stats_lock:
        rolling_trades_24h.append(trade_obj)
        while rolling_trades_24h and rolling_trades_24h[0]["timestamp"] < cutoff_ms:
            rolling_trades_24h.pop(0)

def generate_daily_report_text():
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (24 * 3600 * 1000)

    with stats_lock:
        valid_trades = [t for t in rolling_trades_24h if t["timestamp"] >= cutoff_ms]

    if not valid_trades:
        return "📊 <b>WhaleMetric 24s Raporu:</b>\n\nHenüz yeterli 24 saatlik balina verisi toplanmadı. Piyasa hareketleri izlenmeye devam ediyor..."

    total_volume = 0
    total_buy = 0
    total_sell = 0
    coin_data = {}
    max_trade = None

    for t in valid_trades:
        sym = t["symbol"].replace("USDT", "")
        usd = t["total_usd"]
        is_buy = (t["type"] == "BUY")

        total_volume += usd
        if is_buy:
            total_buy += usd
        else:
            total_sell += usd

        if sym not in coin_data:
            coin_data[sym] = {"buy": 0, "sell": 0, "total": 0}
        
        coin_data[sym]["total"] += usd
        if is_buy:
            coin_data[sym]["buy"] += usd
        else:
            coin_data[sym]["sell"] += usd

        if max_trade is None or usd > max_trade["total_usd"]:
            max_trade = t

    net_flow = total_buy - total_sell
    flow_icon = "🟢 Net Alım Baskısı" if net_flow >= 0 else "🔴 Net Satış Baskısı"

    sorted_by_net_buy = sorted(
        coin_data.items(),
        key=lambda x: (x[1]["buy"] - x[1]["sell"]),
        reverse=True
    )
    top_buys = [x for x in sorted_by_net_buy if (x[1]["buy"] - x[1]["sell"]) > 0][:3]

    sorted_by_net_sell = sorted(
        coin_data.items(),
        key=lambda x: (x[1]["sell"] - x[1]["buy"]),
        reverse=True
    )
    top_sells = [x for x in sorted_by_net_sell if (x[1]["sell"] - x[1]["buy"]) > 0][:3]

    tr_tz = timezone(timedelta(hours=3))
    date_str = datetime.now(tr_tz).strftime("%d.%m.%Y - %H:%M")

    msg = f"📊 <b>WHALEMETRIC 24S BALİNA BÜLTENİ</b>\n"
    msg += f"🗓 <code>{date_str} (TSİ)</code>\n\n"
    msg += f"🐋 <b>Toplam Balina Hacmi:</b> ${total_volume:,.0f}\n"
    msg += f"⚖️ <b>Piyasa Dengesi:</b> {flow_icon} (<b>{'+' if net_flow >= 0 else ''}${net_flow:,.0f}</b>)\n\n"

    msg += "🏆 <b>EN ÇOK TOPLANANLAR (Net Giriş):</b>\n"
    if top_buys:
        for i, (sym, d) in enumerate(top_buys, 1):
            net = d["buy"] - d["sell"]
            buy_pct = (d["buy"] / d["total"]) * 100 if d["total"] > 0 else 0
            msg += f"{i}. <b>{sym}</b>: +${net:,.0f} (🟢 %{buy_pct:.0f} Alıcı)\n"
    else:
        msg += "• Belirgin bir net alım kümelenmesi yok.\n"

    msg += "\n📉 <b>EN ÇOK SATILANLAR (Net Çıkış):</b>\n"
    if top_sells:
        for i, (sym, d) in enumerate(top_sells, 1):
            net = d["sell"] - d["buy"]
            sell_pct = (d["sell"] / d["total"]) * 100 if d["total"] > 0 else 0
            msg += f"{i}. <b>{sym}</b>: -${net:,.0f} (🔴 %{sell_pct:.0f} Satıcı)\n"
    else:
        msg += "• Belirgin bir net satış baskısı yok.\n"

    if max_trade:
        m_sym = max_trade["symbol"].replace("USDT", "")
        m_type = "🟢 ALIM" if max_trade["type"] == "BUY" else "🔴 SATIM"
        msg += f"\n💥 <b>GÜNÜN EN BÜYÜK İŞLEMİ:</b>\n"
        msg += f"• <b>{m_sym}</b>: ${max_trade['total_usd']:,.0f} ({m_type} @ ${max_trade['price']:,.2f})\n"

    msg += "\n🎯 <i>Detaylı canlı radar için terminalinizi kontrol edin.</i>"
    return msg

# Kişiye Özel Saatli Bülten Gönderici
def daily_digest_scheduler():
    tr_tz = timezone(timedelta(hours=3))
    last_sent_minute = None

    while True:
        try:
            now_tr = datetime.now(tr_tz)
            current_hhmm = now_tr.strftime("%H:%M")

            if current_hhmm != last_sent_minute:
                due_users = get_due_daily_report_users(current_hhmm)
                if due_users:
                    report_text = generate_daily_report_text()
                    log(f"📢 Saat {current_hhmm} bülteni {len(due_users)} kullanıcıya iletiliyor...")
                    for u in due_users:
                        send_msg(report_text, u["chat_id"])
                last_sent_minute = current_hhmm

        except Exception as e:
            log(f"Zamanlayıcı Hatası: {e}")

        time.sleep(25)

# ----------------------------------------------------
# 4. TELEGRAM MESAJ GÖNDERİCİ
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
# 5. TELEGRAM KOMUT YÖNETİCİSİ
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

    # Rapor Saati Ayarlama (Örn: "saat 08:30" veya "08:00")
    time_match = re.search(r'(?:saat\s*)?([01]?[0-9]|2[0-3])[:.]([0-5][0-9])', c)
    if time_match:
        hh = f"{int(time_match.group(1)):02d}"
        mm = f"{int(time_match.group(2)):02d}"
        new_time = f"{hh}:{mm}"
        update_user_settings(sender_id_str, {"report_time": new_time, "daily_report": True})
        send_msg(f"⏰ <b>Günlük Bülten Saatiniz Ayarlandı:</b> <code>{new_time} (TSİ)</code>\nHer gün bu saatte 24 saatlik balina özeti otomatik cebinize gelecek!", sender_id_str)
        return

    # Günlük Raporu Aç / Kapat
    if c in ["rapor ac", "rapor aç", "bulten ac", "bülten aç"]:
        update_user_settings(sender_id_str, {"daily_report": True})
        send_msg(f"✅ <b>Günlük bülten aktif edildi!</b>\nGönderim saati: <code>{user.get('report_time', '09:00')}</code>\nDeğiştirmek için: <code>saat 08:30</code>", sender_id_str)
        return

    if c in ["rapor kapat", "rapor iptal", "bulten kapat", "bülten kapat"]:
        update_user_settings(sender_id_str, {"daily_report": False})
        send_msg("🛑 <b>Otomatik günlük bülten kapatıldı.</b>\nİstediğiniz an <code>rapor</code> yazarak anlık özet alabilirsiniz.", sender_id_str)
        return

    # Anlık Rapor İsteme
    if c in ["rapor", "report", "bulten", "bülten", "ozet", "özet", "digest"]:
        send_msg("⏳ 24 saatlik balina verileri hesaplanıyor...", sender_id_str)
        report_text = generate_daily_report_text()
        send_msg(report_text, sender_id_str)
        return

    if c in ["menu", "menü", "panel", "durum", "status", "start"]:
        st = "🟢 AKTİF" if user["enabled"] else "🔴 KAPALI"
        rep_st = f"🟢 AÇIK ({user.get('report_time', '09:00')} TSİ)" if user.get("daily_report", True) else "🔴 KAPALI"
        send_msg(
            f"📊 <b>WHALEMETRIC KİŞİSEL PANELİNİZ</b>\n\n"
            f"• <b>Üye:</b> {user.get('first_name') or 'Trader'}\n"
            f"• <b>Canlı Radar:</b> {st}\n"
            f"• <b>Günlük Bülten Saati:</b> {rep_st}\n"
            f"• <b>Balina Eşiğiniz:</b> <code>${user['threshold']:,.0f}</code>\n"
            f"• <b>Kullanıcı ID:</b> <code>{sender_id_str}</code>\n\n"
            f"⚙️ <b>Hızlı Komutlar:</b>\n"
            f"• <code>saat 08:30</code> → Bülten saatini ayarlar\n"
            f"• <code>rapor</code> → Anlık 24s özet bülteni getirir\n"
            f"• <code>50k</code> → Balina eşiğinizi $50,000 yapar",
            sender_id_str
        )
        return

    if c in ["dur", "stop", "durdur", "kapat", "off"]:
        update_user_settings(sender_id_str, {"enabled": False})
        send_msg("🛑 <b>Canlı bildirimleriniz durduruldu!</b>\nTekrar açmak için: <code>baslat</code>", sender_id_str)
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

    send_msg("❓ <b>Komut anlaşılamadı.</b>\nÖrnekler: <code>saat 08:00</code>, <code>rapor</code>, <code>50k</code>, <code>durdur</code>, <code>baslat</code>", sender_id_str)

# ----------------------------------------------------
# 6. REST API (KİMLİK DOĞRULAMA & BULUT PROFİL)
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

            # 3. Kullanıcı Ayarlarını Kaydetme
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

                if "command" in data:
                    cmd = str(data.get("command", "")).strip()
                    handle_cmd(cmd, chat_id)
                
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
# 7. TELEGRAM POLLER
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
# 8. BYBIT WEBSOCKET
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
                            trade_time = int(trade.get("T", time.time() * 1000))
                        except (ValueError, TypeError):
                            continue

                        if total < 1000:
                            continue

                        side = str(trade.get("S", "")).upper()
                        trade_type = "BUY" if side == "BUY" else "SELL"

                        # 24 Saatlik Rapor İstatistiğine Kaydet
                        record_trade_for_stats({
                            "symbol": sym,
                            "price": p,
                            "quantity": v,
                            "total_usd": total,
                            "type": trade_type,
                            "timestamp": trade_time
                        })

                        # Anlık Kullanıcı Eşik Bildirimleri
                        active_users = get_all_active_users()
                        for u in active_users:
                            if total >= u["threshold"]:
                                icon = "🟢 ALIM" if trade_type == "BUY" else "🔴 SATIM"
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
    threading.Thread(target=daily_digest_scheduler, daemon=True).start()
    asyncio.run(bybit_ws())