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
Power, Light, Fan, Steamer, Aroma, Auto Light, Auto Fan, Dehumidifier, Device schedule armed *(Xenio, v2.9.0+)*

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

## Custom Services

### `set_session`

```yaml
action: harvia_sauna.set_session
data:
  device_id: "your_device_id"
  target_temp: 80        # 40–110 °C
  duration: 60           # 1–720 minutes
  active: true           # start/stop
```

### `set_schedule` / `clear_schedule` *(Xenio, v2.9.0+)*

Program the heater's **own** one-shot schedule. The plan is stored in the panel — it survives Home Assistant restarts and network outages, and the heater ignites by itself.

```yaml
action: harvia_sauna.set_schedule
data:
  device_id: "your_device_id"
  ready_at: "2026-09-01 18:00:00"   # when the sauna should be at temperature
  duration: 90                      # minutes AFTER ready_at, in 15-minute steps
  target_temp: 75                   # 40–110 °C
  enabled: true                     # optional, arm (true) or store disarmed (false)
```

`harvia_sauna.clear_schedule` (only `device_id`) removes the plan completely. See [Device schedule](#device-schedule-xenio-v290) for the measured behaviour and limitations.

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

## Device schedule (Xenio, v2.9.0+)

Everything below was measured on a Xenio CX110 before the feature was implemented (see issue #6).

- **Ignition** happens at `ready_at − heat-up time` (the *Vorwärmzeit* configured in the app), verified to the second. With a 60 min heat-up time and `ready_at` 18:00 the heater starts at 17:00.
- **Duration counts from `ready_at`**, not from ignition. `remainingTime` at ignition = heat-up time + duration.
- The sensor **Device schedule** shows the ready time only while the plan is armed and in the future. The switch **Device schedule armed** toggles byte 0 of the plan exactly like the app's *Aktivieren* toggle — the stored plan is kept.
- **A consumed schedule is not cleared by the heater.** After ignition the plan stays armed with a past ready time; the sensor reports it as *not planned* (`expired: true`).
- **Rest period:** the panel can silently refuse a scheduled start (the app shows *Ruhezeitraum*). This state is **not visible** in the cloud data, so the sensor may show a plan that will not fire. Observed once after a manual stop shortly before the scheduled ignition; a manual start always works.
- Duration is stored in 15-minute units, so 90 min works here although the session-duration number is floored to whole hours by the heater (see below).

## Xenio status codes (bit map)

The `Status codes` sensor value is a bit field. Since v2.8.7 it is decoded into attributes (`door_open`, `heat_demand`, `light`, …, `unknown_bits`, `raw_hex`). Verified across three devices by the community in issue #6:

| Bit | Meaning | Verified on |
|---|---|---|
| 1 | safety circuit / door contact open | 2 devices |
| 5 | target temperature reached (maintaining) | 2 devices |
| 8 | heating **demand** — stays set through thermostat pauses, *not* the element duty cycle | 3 devices |
| 11 | cabin light | 3 devices |
| 17 | session active (not present on every unit) | 2 devices |
| 18 | heating stopped / interrupted (also on a normal stop) | 2 devices |
| 2, 3, 12, 16, 19 | baseline / device-specific, meaning unknown | — |

The inherited "2nd decimal digit == 9" door rule was an artefact of bit 1 on some baselines; the integration uses the bit since v2.8.7. Captures of unexplained bits are welcome in issue #6.

## Measured device behaviour (Xenio)

- **Session duration is normalised to whole hours by the heater** (90 → 60, measured on two devices, firmware 2.3.4). Non-hour values also break the MyHarvia app's editor. The number entity therefore steps by 60 and preset durations are floored — use the device schedule for 15-minute granularity.
- **Switching the heater off at the panel does not update `active` in the cloud data.** The climate entity may keep showing *heat* until a command is sent from HA; `hvac_action` (from the status bits) shows *idle* immediately. A fix that derives the state from the bits is planned.
- The panel reports its state roughly every 14 minutes while idle; the integration polls every 5 minutes in addition to the push feed, so idle devices are not flagged stale.

## Controller support matrix

| | Xenio (myHarvia) | Fenix (harvia.io) |
|---|---|---|
| Monitoring & control | ✅ verified (CX110, CX170 reported) | ✅ verified (SW90S Combi) |
| Door sensor | ✅ status-code bit 1 | ⚠️ derived from `remoteAllowed` (safety-circuit proxy) |
| Device schedule | ✅ | ❌ not yet — scheduling fields unknown; a v2.8.8+ diagnostics export from a Fenix unit would help |
| Status-code bit map | ✅ | n/a (Fenix delivers named fields) |
| Real heater power | ❌ estimate | ✅ `heaterPower` telemetry |

Fenix note: remote control on MyHarvia 2 is a paid *Control* tier after a trial. If reading works but starting does not, check the licence in the app before filing a bug.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

The tests set the integration up against a fake cloud client and assert that every platform loads and every described entity is created for both providers — the failure modes that only show up at runtime. They run in CI on every push.

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
