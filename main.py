import asyncio
import json
import websockets

# Takip edilecek pariteler
TRACKED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
    "NEARUSDT", "PEPEUSDT", "SHIBUSDT", "ARBUSDT", "OPUSDT"
]

# Binance Combined Stream URL
stream_names = "/".join([f"{s.lower()}@aggTrade" for s in TRACKED_SYMBOLS])
BINANCE_WS_URL = f"wss://stream.binance.com:9443/stream?streams={stream_names}"

CONNECTED_CLIENTS = set()

async def register(websocket):
    CONNECTED_CLIENTS.add(websocket)
    print(f"[+] WhaleMetric: İstemci bağlandı. Toplam: {len(CONNECTED_CLIENTS)}")
    try:
        await websocket.wait_closed()
    finally:
        CONNECTED_CLIENTS.remove(websocket)
        print(f"[-] WhaleMetric: İstemci ayrıldı. Kalan: {len(CONNECTED_CLIENTS)}")

async def broadcast(message):
    if CONNECTED_CLIENTS:
        await asyncio.gather(
            *[client.send(message) for client in CONNECTED_CLIENTS],
            return_exceptions=True
        )

async def binance_whale_listener():
    while True:
        try:
            print("[*] WhaleMetric: Binance Combined akışına bağlanılıyor...")
            async with websockets.connect(BINANCE_WS_URL) as ws:
                print(f"[✓] WhaleMetric: {len(TRACKED_SYMBOLS)} kripto parite canlı dinleniyor!")
                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)
                    
                    payload = data.get("data", {})
                    symbol = payload.get("s")
                    
                    if not symbol or symbol not in TRACKED_SYMBOLS:
                        continue
                    
                    price = float(payload.get("p", 0))
                    qty = float(payload.get("q", 0))
                    total_usd = price * qty
                    
                    # Taban limit $1,000 (Kullanıcı arayüzde istediği gibi filtreleyebilsin diye)
                    if total_usd >= 1000:
                        is_buyer_maker = payload.get("m", False)
                        trade_type = "SELL" if is_buyer_maker else "BUY"
                        
                        whale_alert = {
                            "symbol": symbol,
                            "price": price,
                            "quantity": qty,
                            "total_usd": total_usd,
                            "type": trade_type,
                            "timestamp": payload.get("T")
                        }
                        await broadcast(json.dumps(whale_alert))
                        
        except Exception as e:
            print(f"[!] WhaleMetric Hata: {e}. 5 saniye sonra yeniden bağlanılıyor...")
            await asyncio.sleep(5)

async def main():
    async with websockets.serve(register, "localhost", 8765):
        print("[🚀] WhaleMetric WebSocket Sunucusu (ws://localhost:8765) hazır.")
        await binance_whale_listener()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] WhaleMetric durduruldu.")