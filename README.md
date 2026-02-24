# Harvia Sauna Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/WiesiDeluxe/ha-harvia-sauna?style=flat-square)](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Unofficial Home Assistant integration for **Harvia Sauna** heaters with WiFi control (Xenio WiFi / CX110 / CX001WIFI), using the same cloud API as the MyHarvia app.

## Features

- 🌡️ **Climate control** – Thermostat with current/target temperature and HVAC modes
- 💡 **Switches** – Power, Light, Fan, Steamer, Aroma, Auto-Light, Auto-Fan, Dehumidifier
- 📊 **Sensors** – Temperature, Humidity, WiFi RSSI, Remaining time, Heat-up time, Relay counters
- 🚪 **Binary sensors** – Door sensor, Heater active, Steamer active
- 🎚️ **Number controls** – Target humidity, Aroma level, Session duration
- ⚡ **Real-time updates** via WebSocket push (no polling delay)
- 🔧 **Proper device registry** integration with device grouping
- 🔄 **Re-authentication** support via config flow

## Compatibility

Tested with:
- Harvia Legend Home XW (PO110XW) with CX110 control panel
- Harvia Xenio WiFi (CX001WIFI)
- Harvia Cilindro PC90XE

Should work with any sauna compatible with the **MyHarvia** app.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click the **three dots** (⋮) in the top right → **Custom repositories**
4. Add this repository URL and select **Integration** as the category
5. Search for **Harvia Sauna** and click **Install**
6. Restart Home Assistant

### Manual

1. Download the latest release from the [Releases](https://github.com/WiesiDeluxe/ha-harvia-sauna/releases) page
2. Copy the `custom_components/harvia_sauna` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for **Harvia Sauna**
4. Enter your MyHarvia app credentials (email + password)

## Entities

| Platform | Entity | Description |
|----------|--------|-------------|
| Climate | Thermostat | Temperature control with HEAT/OFF modes |
| Switch | Power | Turn heater on/off |
| Switch | Light | Sauna light on/off |
| Switch | Fan | Ventilation fan on/off |
| Switch | Steamer | Steam generator on/off |
| Switch | Aroma | Aroma dispenser on/off |
| Switch | Auto Light | Automatic light control |
| Switch | Auto Fan | Automatic fan control |
| Switch | Dehumidifier | Dehumidifier on/off |
| Sensor | Temperature | Current cabin temperature |
| Sensor | Humidity | Current humidity |
| Sensor | Target Temperature | Set target temperature |
| Sensor | Remaining Time | Minutes remaining in session |
| Sensor | Heat-up Time | Estimated heat-up time |
| Sensor | WiFi Signal | Controller WiFi RSSI (diagnostic) |
| Sensor | Status Codes | Raw status codes (diagnostic) |
| Sensor | Aroma Level | Current aroma intensity |
| Sensor | Relay Counters | Phase 1/2/3 relay cycles (diagnostic) |
| Sensor | Heater/Steamer Cycles | Lifetime cycle counters (diagnostic) |
| Binary Sensor | Door | Sauna door open/closed |
| Binary Sensor | Heater Active | Heating element actively on |
| Binary Sensor | Steamer Active | Steam generator actively running |
| Number | Target Humidity | Set target humidity (0-100%) |
| Number | Aroma Level | Set aroma intensity (0-100%) |
| Number | Session Duration | Set max session time |

> **Note:** Not all entities may be relevant for your sauna model. You can disable unused entities in the Home Assistant UI.

## Architecture

This integration uses a **push-based** architecture:

- **WebSocket connections** to MyHarvia AppSync for real-time state and telemetry updates
- **Fallback polling** every 5 minutes in case WebSocket connections drop
- **DataUpdateCoordinator** pattern for efficient entity updates
- **Proper lifecycle management** – clean shutdown of all WebSocket connections

## Migrating from v0.x (RubenHarms version)

1. Remove the old integration: **Settings** → **Devices & Services** → **Harvia Sauna** → **Delete**
2. Delete the old files from `custom_components/harvia_sauna/`
3. Install this version via HACS or manually
4. Restart Home Assistant
5. Add the integration again with your MyHarvia credentials

> **Note:** Entity IDs will change. Update your automations and dashboards accordingly.

## Troubleshooting

### Authentication errors
- Make sure you use the same credentials as in the MyHarvia app
- The integration uses AWS Cognito authentication – verify your email/password are correct

### Connection issues
- Check your internet connection
- The MyHarvia cloud service must be reachable
- Check Home Assistant logs for detailed error messages

### Missing entities
- Not all Harvia models support all features (e.g., steamer, aroma)
- Unsupported entities will show as "unavailable" and can be disabled

## Credits

- **API reverse engineering** by [Ruben Harms](https://github.com/RubenHarms) (original [ha-harvia-xenio-wifi](https://github.com/RubenHarms/ha-harvia-xenio-wifi))
- **v2 rewrite** with modern Home Assistant architecture patterns, proper lifecycle management, and extended entity support

## License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by Harvia. Use at your own risk.
