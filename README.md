# 🐋 Crypto Whale Radar (Real-Time BTC Stream Dashboard)

A high-frequency real-time crypto analytics dashboard that tracks and visualizes large Bitcoin transactions ("whale trades") from the Binance public WebSocket stream with sub-millisecond latency.

![Project Status](https://img.shields.io/badge/Status-Active-emerald)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Architecture](https://img.shields.io/badge/Architecture-Event--Driven-orange)

---

## ⚡ System Architecture

```text
[ Binance Public WebSocket Stream ] 
                │ (Sub-millisecond trade stream)
                ▼
  [ Python Async Backend (main.py) ] ──► (Filter: Threshold >= $10k, $50k, $100k)
                │
                ▼ (Local WebSocket Server ws://localhost:8765)
  [ Frontend Client (index.html) ] ──► Dynamic Chart & Audio Ping & DOM Queue