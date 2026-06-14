# Harvia Sauna Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/WiesiDeluxe/ha-harvia-sauna)](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Custom Home Assistant integration for **Harvia sauna heaters** with **Xenio WiFi** (CX110 / CX001WIFI) and **Fenix** (FX001XW / FX002XW) control panels, providing real-time monitoring and control through the Harvia cloud APIs.

## Features

- 🌡️ **Climate control** — thermostat with current/target temperature
- 🔌 **Dual controller support** — Xenio WiFi (myHarvia) and Fenix (harvia.io)
- ⚡ **Real-time updates** via WebSocket push — no polling delay
- 📊 **Session tracking** — duration, max temperature, daily count
- 🔋 **Energy monitoring** — power (W) and cumulative energy (kWh), persistent across restarts
- 💡 **Full device control** — power, light, fan, steam, aroma, dehumidifier
- 🎚️ **Configurable setpoints** — session time, target humidity, aroma level
- 🔧 **Custom service** `harvia_sauna.set_session` — configure sessions in one call
- 📡 **HA Events** — `session_start` / `session_end` for automation triggers
- 🔒 **Diagnostics** — anonymized debug export for troubleshooting
- 🌍 **19 languages** — EN, DE, FI, IT, FR, SV, ES, JA, ET, NL, NB, DA, PL, PT-BR, RU, ZH-Hans, KO, CS, HU

## Requirements

- A Harvia sauna heater with **Xenio WiFi** (CX110 / CX001WIFI) or **Fenix** (FX001XW / FX002XW) control panel
- An active **MyHarvia** or **MyHarvia 2** app account
- Internet connectivity (cloud API — no local control available)

## Installation

### HACS (recommended)

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/WiesiDeluxe/ha-harvia-sauna` as type **Integration**
3. Search for "Harvia Sauna" and install
4. Restart Home Assistant

### Manual

1. Download the [latest release](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases)
2. Copy `custom_components/harvia_sauna/` to your `config/custom_components/`
3. Restart Home Assistant

## Setup

1. **Settings** → **Devices & Services** → **Add Integration** → search "Harvia Sauna"
2. Select your **API Provider**:
   - **myHarvia (Xenio controller)** — for Xenio WiFi panels (CX110 / CX001WIFI)
   - **myHarvia 2 - harvia.io (Fenix controller)** — for Fenix panels (FX001XW / FX002XW)
3. Enter your credentials (same email/password as in the app)
4. Select heater model and power rating (auto-detection attempted)

To change model/power later: **⋮** → **Reconfigure**

## Entities

### Climate
| Entity | Description |
|--------|-------------|
| Thermostat | Set target temperature, operating mode |

### Switches
Power, Light, Fan, Steamer, Aroma, Auto Light, Auto Fan, Dehumidifier

### Sensors
| Entity | Description |
|--------|-------------|
| Temperature | Current cabin temperature |
| Humidity | Current humidity level |
| Target temperature | Configured target |
| Remaining time | Session countdown |
| Power (estimated) | Current power draw estimate (W) — see Energy Dashboard note |
| Energy (estimated) | Cumulative kWh estimate (persisted, Energy Dashboard compatible) |
| Last session duration | Duration of most recent session |
| Last session max temp | Peak temperature of most recent session |
| Sessions today | Daily session counter |
| Temperature trend | Heating rate in °C/min (disabled by default) |
| WiFi signal | RSSI (diagnostic) |
| Status codes | Device status (diagnostic) |
| Relay counters | Phase 1/2/3, heater, steam cycle counters (diagnostic) |

### Binary Sensors
Door, Heating active, Steam active

### Number Controls
Target humidity, Aroma level, Session time

## Options (v2.6.0+)

Open **Settings → Devices & Services → Harvia Sauna → Configure** to access:

**Light sync** — Link HA light entities (e.g. Hue strips) to the light button
on the Harvia panel, even if no light is physically wired to the power unit.
Modes: disabled, panel → HA only, or bidirectional (panel ↔ HA with loop
protection and any-on aggregation across multiple lights).

**Session end behavior** — By default a session ends when the heater turns
off. The *cooldown* mode keeps the session running until the temperature
drops below the target temperature (frozen at heater-off) minus a
configurable hysteresis — designed for stone-heavy heaters (e.g. Legend with
100 kg of stones) that keep the cabin sauna-hot long after power-off. An
optional external HA temperature sensor can be used as the reference (and
for the session max temperature) instead of the slower internal Harvia
sensor. A maximum cooldown duration acts as a safety net.

## Ambilight & Ready Detection (v2.7.0+)

**Ambilight** — temperature-driven light color in up to two independent
zones (e.g. ceiling strip = full spectrum from cool white to deep red,
bench strip = warm-only), each with an optional temperature offset. Only
the color is driven; brightness stays under your control. A manual color
change pauses Ambilight until the session ends or the integration's
**Ambilight switch** is toggled. When the session ends, the configured
everyday standard (color temperature + brightness) is restored.

**Ready detection** — a latched `Ready` binary sensor plus the
`harvia_sauna_ready` event, fired once per session when the reference
temperature reaches the threshold (target temperature, or a fixed value —
useful for stone-heavy heaters you enter before the target is reached).
Companion sensors: **Time to ready** (minutes, from the reference-sensor
heating trend) and **Ready at** (timestamp) for notifications like
"Sauna ready at 17:42".

**Combi safety** — `target temperature + target humidity` is clamped to
140 (the MyHarvia app enforces this limit, the raw API does not).

## Cooldown End Mode (v2.8.1+)

The cooldown phase can now end either by **hysteresis** below the frozen
target (original behavior) or at a **fixed reference temperature**
(`cooldown_end_mode` option). In fixed-temperature mode the session ends
when the reference sensor drops below the configured value — and this is
the same point at which Ambilight restores the everyday standard, so the
session and the lights end together.

A **flicker guard** requires several consecutive readings below the
threshold before ending, so a BLE reference sensor (e.g. Shelly BLU H&T)
briefly dropping to `unavailable` can no longer end the session early.

## Custom Service

```yaml
action: harvia_sauna.set_session
data:
  device_id: "your_device_id"
  target_temp: 80        # 40–110 °C
  duration: 60           # 1–720 minutes
  active: true           # start/stop
```

## Automation Events

```yaml
# Session started
trigger:
  - trigger: event
    event_type: harvia_sauna_session_start
# Event data: device_id, target_temp

# Session ended
trigger:
  - trigger: event
    event_type: harvia_sauna_session_end
# Event data: device_id, duration_min, max_temp
```

## Energy Dashboard

The energy sensor uses `state_class: total_increasing` and works with the HA Energy Dashboard.

> **⚠️ Xenio users:** Power and energy are **estimates**. The Xenio API does not expose the actual heater relay state — it only reports whether the session is active. So the power sensor shows the full rated power (e.g. 10800 W) for the whole session, even when the thermostat has cycled the element off (and the stones keep radiating heat). Real consumption is lower than reported. For accurate measurement, use an external energy meter (e.g. Shelly 3EM, CT clamp) on the heater circuit.
>
> **Fenix users:** The Fenix API provides a real measured `heaterPower` value, so the "Actual heater power" sensor reflects true consumption.

## Architecture

```
Xenio WiFi Panel ──MQTT/TLS──▶ AWS IoT Core (eu-west-1)
                                     ▲
This Integration ──Cognito──▶ AWS AppSync (GraphQL + WebSocket)

Fenix Panel ──MQTT/TLS──▶ AWS IoT Core (eu-central-1)
                                  ▲
This Integration ──REST──▶ harvia.io API (REST + GraphQL + WebSocket)
```

**IoT class:** `cloud_push` — real-time WebSocket subscriptions with REST polling fallback every 5 minutes. Both providers use the same coordinator, entities, and session tracking.

## Troubleshooting

**Download diagnostics:** Settings → Devices & Services → Harvia Sauna → ⋮ → Download diagnostics

| Issue | Solution |
|-------|----------|
| Cannot connect | Verify MyHarvia app credentials, check internet |
| Entities unavailable | Check Xenio WiFi panel LED, verify WiFi |
| Stale data | Check diagnostics for WebSocket status |

## Companion Card

[sauna-card](https://github.com/krissen/sauna-card) by [@krissen](https://github.com/krissen) is a custom Lovelace card built specifically for this integration. It shows and controls the sauna in a single card — current/target temperature with a stepper, start/stop a session, and toggles for power, light, fan and steamer — plus a live temperature graph while the sauna runs, and a companion badge. It auto-detects the device through this integration (no entity IDs to enter) and follows the HA locale.

Install via HACS as a custom repository — see the [sauna-card README](https://github.com/krissen/sauna-card) for details.

## License

MIT License. This project is not affiliated with Harvia Oyj.

---

<p align="center"><i>Scripted in Austria 🇦🇹 — Happy Schwitzing! 🧖‍♂️🔥</i></p>
