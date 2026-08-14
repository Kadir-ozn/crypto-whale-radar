import asyncio
import websockets
import json
from datetime import datetime

# 🎯 Balina Eşik Değeri (Test için $10.000, istediğin zaman artırabilirsin)
WHALE_THRESHOLD_USD = 10000.00

# 📡 Bize bağlanan web tarayıcılarını (Frontend istemcilerini) tutacağımız havuz (Set)
CONNECTED_CLIENTS = set()

# 1. Frontend (Tarayıcı) ile bağlantı kuran sunucu fonksiyonu
async def client_handler(websocket):
    # Yeni bir tarayıcı/sekme açıldığında onu listeye ekle
    CONNECTED_CLIENTS.add(websocket)
    print(f"🔗 [FRONTEND] Yeni bir tarayıcı bağlandı! Toplam izleyici: {len(CONNECTED_CLIENTS)}")
    
    try:
        # Bağlantı açık kaldığı sürece bekle
        await websocket.wait_closed()
    finally:
        # Sekme kapatıldığında listeden çıkar
        CONNECTED_CLIENTS.remove(websocket)
        print(f"❌ [FRONTEND] Bir tarayıcı ayrıldı. Kalan izleyici: {len(CONNECTED_CLIENTS)}")

# 2. Bağlı olan tüm tarayıcılara veriyi aynı anda fırlatan yayıncı fonksiyon
async def broadcast_whale(whale_data):
    if CONNECTED_CLIENTS:
        # JSON formatına çevirip tüm açık tarayıcılara aynı anda gönderiyoruz
        message = json.dumps(whale_data)
        await asyncio.gather(*[client.send(message) for client in CONNECTED_CLIENTS])

# 3. Binance'i dinleyen ve filtreleyen ana motor
async def binance_listener():
    stream_url = "wss://stream.binance.com:9443/ws/btcusdt@trade"

    async with websockets.connect(stream_url) as ws:
        print("🟢 Binance Canlı Veri Akışı Dinleniyor...")
        
        while True:
            raw_data = await ws.recv()
            trade = json.loads(raw_data)

            price = round(float(trade['p']), 2)
            quantity = float(trade['q'])
            trade_value = round(price * quantity, 2)

            # Filtre: Sadece balina işlemlerini yakala
            if trade_value >= WHALE_THRESHOLD_USD:
                trade_type = "SELL" if trade['m'] else "BUY"
                trade_time = datetime.fromtimestamp(trade['T'] / 1000.0).strftime('%H:%M:%S')

                # Frontend'e göndereceğimiz temiz veri paketi
                whale_payload = {
                    "time": trade_time,
                    "type": trade_type,
                    "price": price,
                    "quantity": quantity,
                    "value": trade_value
                }

                # Terminale de basıyoruz
                emoji = "🔴" if trade_type == "SELL" else "🟢"
                print(f"🚨 {emoji} {trade_type} | ${trade_value:,.2f} | {quantity} BTC @ ${price:,}")

                # 🚀 Tüm bağlı tarayıcılara fırlat!
                await broadcast_whale(whale_payload)

# 4. Ana Fonksiyon: Hem sunucuyu hem Binance dinleyicisini aynı anda başlatır
async def main():
    # Kendi WebSocket sunucumuzu 8765 portunda açıyoruz
    server = await websockets.serve(client_handler, "localhost", 8765)
    print("=" * 60)
    print("🚀 BACKEND WEBSOCKET SUNUCUSU BAŞLATILDI: ws://localhost:8765")
    print("=" * 60)

    # İki görevi aynı anda eşzamanlı (asenkron) çalıştır
    await asyncio.gather(
        server.wait_closed(),
        binance_listener()
    )

if __name__ == "__main__":
    asyncio.run(main())