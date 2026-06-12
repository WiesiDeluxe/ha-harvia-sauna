# Harvia Sauna Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/WiesiDeluxe/ha-harvia-sauna)](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Custom Home Assistant integration for **Harvia sauna heaters** with **Xenio WiFi** (CX110 / CX001WIFI) and **Fenix** (FX001XW / FX002XW) control panels, providing real-time monitoring and control through the Harvia cloud APIs.

## Features

- ð¡ï¸ **Climate control** â thermostat with current/target temperature
- ð **Dual controller support** â Xenio WiFi (myHarvia) and Fenix (harvia.io)
- â¡ **Real-time updates** via WebSocket push â no polling delay
- ð **Session tracking** â duration, max temperature, daily count
- ð **Energy monitoring** â power (W) and cumulative energy (kWh), persistent across restarts
- ð¡ **Full device control** â power, light, fan, steam, aroma, dehumidifier
- ðï¸ **Configurable setpoints** â session time, target humidity, aroma level
- ð§ **Custom service** `harvia_sauna.set_session` â configure sessions in one call
- ð¡ **HA Events** â `session_start` / `session_end` for automation triggers
- ð **Diagnostics** â anonymized debug export for troubleshooting
- ð **19 languages** â EN, DE, FI, IT, FR, SV, ES, JA, ET, NL, NB, DA, PL, PT-BR, RU, ZH-Hans, KO, CS, HU

## Requirements

- A Harvia sauna heater with **Xenio WiFi** (CX110 / CX001WIFI) or **Fenix** (FX001XW / FX002XW) control panel
- An active **MyHarvia** or **MyHarvia 2** app account
- Internet connectivity (cloud API â no local control available)

## Installation

### HACS (recommended)

1. Open HACS â **Integrations** â â® â **Custom repositories**
2. Add `https://github.com/WiesiDeluxe/ha-harvia-sauna` as type **Integration**
3. Search for "Harvia Sauna" and install
4. Restart Home Assistant

### Manual

1. Download the [latest release](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases)
2. Copy `custom_components/harvia_sauna/` to your `config/custom_components/`
3. Restart Home Assistant

## Setup

1. **Settings** â **Devices & Services** â **Add Integration** â search "Harvia Sauna"
2. Select your **API Provider**:
   - **myHarvia (Xenio controller)** â for Xenio WiFi panels (CX110 / CX001WIFI)
   - **myHarvia 2 - harvia.io (Fenix controller)** â for Fenix panels (FX001XW / FX002XW)
3. Enter your credentials (same email/password as in the app)
4. Select heater model and power rating (auto-detection attempted)

To change model/power later: **â®** â **Reconfigure**

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
| Temperature trend | Heating rate in Â°C/min (disabled by default) |
| WiFi signal | RSSI (diagnostic) |
| Status codes | Device status (diagnostic) |
| Relay counters | Phase 1/2/3, heater, steam cycle counters (diagnostic) |

### Binary Sensors
Door, Heating active, Steam active

### Number Controls
Target humidity, Aroma level, Session time

## Custom Service

```yaml
action: harvia_sauna.set_session
data:
  device_id: "your_device_id"
  target_temp: 80        # 40â110 Â°C
  duration: 60           # 1â720 minutes
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
Xenio WiFi Panel ââMQTT/TLSâââ¶ AWS IoT Core (eu-west-1)
                                     â²
This Integration ââCognitoâââ¶ AWS AppSync (GraphQL + WebSocket)

Fenix Panel ââMQTT/TLSâââ¶ AWS IoT Core (eu-central-1)
                                  â²
This Integration ââRESTâââ¶ harvia.io API (REST + GraphQL + WebSocket)
```

**IoT class:** `cloud_push` â real-time WebSocket subscriptions with REST polling fallback every 5 minutes. Both providers use the same coordinator, entities, and session tracking.

## Troubleshooting

**Download diagnostics:** Settings â Devices & Services â Harvia Sauna â â® â Download diagnostics

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

<p align="center"><i>Scripted in Austria ð¦ð¹ â Happy Schwitzing! ð§ââï¸ð¥</i></p>
