# Harvia Sauna Integration

Control your Harvia sauna heater directly from Home Assistant — real-time WebSocket updates, session tracking, energy monitoring, and more.

## Supported Controllers

- **Xenio WiFi** (CX110 / CX001WIFI) — via myHarvia cloud
- **Fenix** (FX001XW / FX002XW) — via harvia.io API *(experimental)*

## Highlights

🌡️ Climate thermostat with target temperature control
⚡ Real-time WebSocket push — no polling delay
📊 Session tracking — duration, max temperature, daily count
🔋 Energy monitoring — estimated kWh on Xenio, real measurement on Fenix
💡 Full control — power, light, fan, steam, aroma, dehumidifier
🔧 Custom service `harvia_sauna.set_session`
📡 HA events for session start/end automations
🌍 19 languages

## Setup

1. Add integration → search "Harvia Sauna"
2. Select controller type (Xenio or Fenix)
3. Enter MyHarvia credentials
4. Choose heater model & power

---

*Scripted in Austria 🇦🇹 — Happy Schwitzing! 🧖‍♂️🔥*
