"""Constants for the Harvia Sauna integration."""

from __future__ import annotations

DOMAIN = "harvia_sauna"
MANUFACTURER = "Harvia"

# MyHarvia Cloud
MYHARVIA_BASE_URL = "https://prod.myharvia-cloud.net"
MYHARVIA_REGION = "eu-west-1"

# API Endpoints
ENDPOINTS = ["users", "device", "events", "data"]

# WebSocket
WS_RECONNECT_INTERVAL = 1800  # 30 Minuten - periodischer Reconnect
WS_HEARTBEAT_TIMEOUT = 300  # 5 Minuten ohne Heartbeat = Reconnect
WS_MAX_RECONNECT_DELAY = 60  # Max Backoff bei Reconnect

# Coordinator
SCAN_INTERVAL_FALLBACK = 300  # 5 Minuten Fallback-Polling falls WebSocket ausfällt

# Config keys
CONF_HEATER_MODEL = "heater_model"
CONF_HEATER_POWER = "heater_power"

# Heater models compatible with MyHarvia / Xenio WiFi
HEATER_MODELS: dict[str, str] = {
    "kip": "Harvia KIP",
    "cilindro": "Harvia Cilindro",
    "spirit": "Harvia Spirit",
    "club": "Harvia Club",
    "virta": "Harvia Virta",
    "virta_combi": "Harvia Virta Combi",
    "virta_pro": "Harvia Virta Pro",
    "legend": "Harvia Legend",
    "senator": "Harvia Senator",
    "forte": "Harvia Forte",
    "pro": "Harvia Pro",
    "other": "Other / Unknown",
}

# Available heater power ratings (kW)
HEATER_POWER_OPTIONS: dict[str, str] = {
    "3.0": "3.0 kW",
    "4.5": "4.5 kW",
    "6.0": "6.0 kW",
    "6.8": "6.8 kW",
    "8.0": "8.0 kW",
    "9.0": "9.0 kW",
    "10.5": "10.5 kW",
    "10.8": "10.8 kW",
    "12.0": "12.0 kW",
    "15.0": "15.0 kW",
    "16.5": "16.5 kW",
    "17.0": "17.0 kW",
    "20.0": "20.0 kW",
}

# Heater
DEFAULT_HEATER_POWER_W = 10800  # Default Nennleistung in Watt (10.8 kW)
CONF_HEATER_POWER = "heater_power"
