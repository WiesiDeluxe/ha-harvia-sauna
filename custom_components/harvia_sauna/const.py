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

# Session tracking
SESSION_MIN_DURATION_SEC = 300  # 5 Minuten Mindestdauer damit ein An/Aus als Session zählt

# Config keys
CONF_HEATER_MODEL = "heater_model"
CONF_HEATER_POWER = "heater_power"

CONF_API_PROVIDER = "api_provider"

# HA Events
EVENT_SESSION_START = f"{DOMAIN}_session_start"
EVENT_SESSION_END = f"{DOMAIN}_session_end"

# Services
SERVICE_SET_SESSION = "set_session"

# API Providers
API_PROVIDER_MYHARVIA = "myharvia_graphql"
API_PROVIDER_HARVIAIO = "harviaio_rest_graphql"

API_PROVIDERS: dict[str, str] = {
    API_PROVIDER_MYHARVIA: "myHarvia (Xenio controller)",
    API_PROVIDER_HARVIAIO: "myHarvia 2 - harvia.io (Fenix controller)",
}

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

# ── Options (v2.6.0) ──────────────────────────────────────────────
# Light sync: mirror the panel light button to HA light entities
CONF_LINKED_LIGHTS = "linked_lights"
CONF_LIGHT_SYNC_MODE = "light_sync_mode"
LIGHT_SYNC_OFF = "off"
LIGHT_SYNC_PANEL_TO_HA = "panel_to_ha"
LIGHT_SYNC_BIDIRECTIONAL = "bidirectional"
DEFAULT_LIGHT_SYNC_MODE = LIGHT_SYNC_BIDIRECTIONAL
LIGHT_SYNC_DEBOUNCE_SEC = 1.0  # HA→Panel debounce

# Session end mode: classic (heater off) or cooldown (temp below target)
CONF_SESSION_END_MODE = "session_end_mode"
SESSION_END_HEATER_OFF = "heater_off"
SESSION_END_COOLDOWN = "cooldown"
DEFAULT_SESSION_END_MODE = SESSION_END_HEATER_OFF

CONF_COOLDOWN_TEMP_SENSOR = "cooldown_temp_sensor"
CONF_COOLDOWN_HYSTERESIS = "cooldown_hysteresis"
DEFAULT_COOLDOWN_HYSTERESIS = 2.0  # °C below frozen target temp
CONF_COOLDOWN_MAX_MINUTES = "cooldown_max_minutes"
DEFAULT_COOLDOWN_MAX_MINUTES = 180
CONF_EXT_SENSOR_FOR_MAX_TEMP = "use_ext_sensor_for_max_temp"
DEFAULT_EXT_SENSOR_FOR_MAX_TEMP = True

# Grace period: if the external sensor is unavailable longer than this,
# fall back to the internal Harvia sensor for cooldown decisions
COOLDOWN_EXT_SENSOR_GRACE_SEC = 600
