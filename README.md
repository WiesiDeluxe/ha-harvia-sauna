# Harvia Sauna Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/WiesiDeluxe/ha-harvia-sauna)](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Custom Home Assistant integration for **Harvia sauna heaters** with Xenio WiFi control panels (CX110 / CX001WIFI), providing real-time monitoring and control through the MyHarvia cloud API.

## Features

- 🌡️ **Climate control** — thermostat with current/target temperature
- 🔌 **Dual API provider support** — MyHarvia GraphQL (legacy) and Harvia REST/GraphQL API
- ⚡ **Real-time updates** via WebSocket push — no polling delay
- 📊 **Session tracking** — duration, max temperature, daily count
- 🔋 **Energy monitoring** — power (W) and cumulative energy (kWh), persistent across restarts
- 💡 **Full device control** — power, light, fan, steam, aroma, dehumidifier
- 🎚️ **Configurable setpoints** — session time, target humidity, aroma level
- 🔧 **Custom service** `harvia_sauna.set_session` — configure sessions in one call
- 📡 **HA Events** — `session_start` / `session_end` for automation triggers
- 🔒 **Diagnostics** — anonymized debug export for troubleshooting
- 🌍 **9 languages** — EN, DE, FI, IT, FR, SV, ES, JA, ET

## Requirements

- Harvia sauna heater with **Xenio WiFi** control panel
- Active **MyHarvia** app account
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
2. Choose API provider (`MyHarvia Cloud` or `Harvia API`)
3. Enter your Harvia credentials (email + password)
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
| Power | Current power draw (W) |
| Energy consumption | Cumulative kWh (persisted, Energy Dashboard compatible) |
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

The energy sensor uses `state_class: total_increasing` and works with the HA Energy Dashboard. Energy is calculated from heater relay state × configured power rating and persists across restarts.

## Architecture

```
Xenio WiFi Panel ──MQTT/TLS──▶ AWS IoT Core
                                     ▲
This Integration ──Cognito──▶ AWS AppSync (GraphQL + WebSocket)
```

**IoT class:** `cloud_push` — 4 WebSocket connections for real-time state + telemetry, REST polling fallback every 5 minutes.

## Troubleshooting

**Download diagnostics:** Settings → Devices & Services → Harvia Sauna → ⋮ → Download diagnostics

| Issue | Solution |
|-------|----------|
| Cannot connect | Verify MyHarvia app credentials, check internet |
| Entities unavailable | Check Xenio WiFi panel LED, verify WiFi |
| Stale data | Check diagnostics for WebSocket status |

## License

MIT License. This project is not affiliated with Harvia Oyj.
