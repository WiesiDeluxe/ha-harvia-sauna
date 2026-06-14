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

# Cooldown end mode (v2.8.1): end the session either a hysteresis below the
# frozen target ("hysteresis", original behavior) or at a fixed reference
# temperature ("fixed_temp"). The fixed temperature also drives the
# Ambilight standard-restore, so session end and lights end at the same point.
CONF_COOLDOWN_END_MODE = "cooldown_end_mode"
COOLDOWN_END_HYSTERESIS = "hysteresis"
COOLDOWN_END_FIXED_TEMP = "fixed_temp"
DEFAULT_COOLDOWN_END_MODE = COOLDOWN_END_HYSTERESIS
CONF_COOLDOWN_END_TEMP = "cooldown_end_temp"
DEFAULT_COOLDOWN_END_TEMP = 60.0  # °C — below this the session ends

# Flicker guard: a BLE reference sensor (e.g. Shelly BLU H&T) can drop to
# "unavailable" briefly. To avoid ending the session on a single reading
# right after such a gap, the reference must stay below the threshold for
# this many consecutive evaluations before the session ends.
COOLDOWN_END_CONFIRM_COUNT = 3
CONF_EXT_SENSOR_FOR_MAX_TEMP = "use_ext_sensor_for_max_temp"
DEFAULT_EXT_SENSOR_FOR_MAX_TEMP = True

# Grace period: if the external sensor is unavailable longer than this,
# fall back to the internal Harvia sensor for cooldown decisions
COOLDOWN_EXT_SENSOR_GRACE_SEC = 600

# ── Ambilight & Ready (v2.7.0) ────────────────────────────────────
# Ambilight: temperature-driven light color in two independent zones
CONF_AMBI_ZONE1_LIGHTS = "ambi_zone1_lights"
CONF_AMBI_ZONE1_CURVE = "ambi_zone1_curve"
CONF_AMBI_ZONE1_OFFSET = "ambi_zone1_offset"
CONF_AMBI_ZONE2_LIGHTS = "ambi_zone2_lights"
CONF_AMBI_ZONE2_CURVE = "ambi_zone2_curve"
CONF_AMBI_ZONE2_OFFSET = "ambi_zone2_offset"
AMBI_CURVE_OFF = "off"
AMBI_CURVE_FULL = "full_spectrum"
AMBI_CURVE_WARM = "warm_only"
DEFAULT_AMBI_CURVE = AMBI_CURVE_OFF
DEFAULT_AMBI_OFFSET = 0.0

# Standard/everyday light value restored after the session ends
CONF_AMBI_STANDARD_KELVIN = "ambi_standard_kelvin"
DEFAULT_AMBI_STANDARD_KELVIN = 2000
CONF_AMBI_STANDARD_BRIGHTNESS = "ambi_standard_brightness"
DEFAULT_AMBI_STANDARD_BRIGHTNESS = 100  # percent

# Ambilight engine tuning
AMBI_MIN_TEMP_DELTA_C = 1.0      # recompute only on >= 1 °C change
AMBI_MIN_INTERVAL_SEC = 30.0     # ... or at most every 30 s
AMBI_TRANSITION_SEC = 2          # smooth fade
AMBI_CCT_SAT_THRESHOLD = 35.0    # below this saturation prefer CCT mode

# Ready detection
CONF_READY_MODE = "ready_mode"
READY_MODE_TARGET = "target"
READY_MODE_FIXED = "fixed"
DEFAULT_READY_MODE = READY_MODE_TARGET
CONF_READY_FIXED_TEMP = "ready_fixed_temp"
DEFAULT_READY_FIXED_TEMP = 60.0

EVENT_READY = f"{DOMAIN}_ready"

# Time-to-ready estimation
READY_TREND_MIN_C_PER_MIN = 0.05  # below this the ETA is unknown (guard)
REF_TREND_HISTORY_MAX = 10

# Combi safety: the MyHarvia app enforces targetTemp + targetRh <= 140,
# the API does not — we clamp to protect combi heaters
COMBI_TEMP_RH_LIMIT = 140

# ── Smart Preheat & Statistics (v2.8.0) ──────────────────────────
# Heating model: 5 °C buckets, exponentially weighted moving average
HEATING_BUCKET_SIZE_C = 5
HEATING_EWMA_ALPHA = 0.3           # weight of the newest observation
HEATING_RATE_MIN = 0.05            # °C/min sanity bounds for learning
HEATING_RATE_MAX = 5.0
HEATING_BUCKET_MIN_C = 10          # learn from this cabin temp upwards
HEATING_BUCKET_MAX_C = 110
HEATING_LEARN_CEILING_BELOW_TARGET_C = 3.0  # exclude thermostat zone
HEATING_EXTRAPOLATION_DECAY = 0.9  # rate multiplier per unknown bucket
HEATING_DIP_TOLERANCE_C = 0.5      # in-bucket dip > this discards bucket

# Seed profiles by heater stone-mass class (validated against measured
# Legend 10.8 kW / ~100 kg data: launch ~0.3, mid ~0.82, top ~0.7 °C/min)
HEATER_CLASS_HIGH_MASS = "high"     # Legend, Cilindro (~90–120 kg)
HEATER_CLASS_MEDIUM_MASS = "medium"  # Virta, Senator, Club, ... (~40–60 kg)
HEATER_CLASS_LOW_MASS = "low"       # KIP, Spirit (~20–30 kg)
HEATER_MODEL_CLASSES: dict[str, str] = {
    "legend": HEATER_CLASS_HIGH_MASS,
    "cilindro": HEATER_CLASS_HIGH_MASS,
    "kip": HEATER_CLASS_LOW_MASS,
    "spirit": HEATER_CLASS_LOW_MASS,
    # everything else (virta*, club, senator, forte, pro, other) -> medium
}
DEFAULT_HEATER_CLASS = HEATER_CLASS_MEDIUM_MASS

# Preheat scheduling
CONF_PREHEAT_BUFFER_MIN = "preheat_buffer_min"
DEFAULT_PREHEAT_BUFFER_MIN = 5
PREHEAT_RECOMPUTE_INTERVAL_MIN = 15
PREHEAT_START_VERIFY_DELAY_SEC = 60
PREHEAT_START_MAX_RETRIES = 3

SERVICE_READY_AT = "ready_at"
SERVICE_CANCEL_PREHEAT = "cancel_preheat"
EVENT_PREHEAT_FAILED = f"{DOMAIN}_preheat_failed"
EVENT_PREHEAT_STARTED = f"{DOMAIN}_preheat_started"

STORAGE_VERSION = 1
STORAGE_KEY_HEATING_MODEL = f"{DOMAIN}.heating_model"
STORAGE_KEY_PREHEAT = f"{DOMAIN}.preheat_schedule"

# Climate presets (3 fixed slots configured via options)
CONF_PRESET1_NAME = "preset1_name"
CONF_PRESET1_TEMP = "preset1_temp"
CONF_PRESET1_DURATION = "preset1_duration"
CONF_PRESET2_NAME = "preset2_name"
CONF_PRESET2_TEMP = "preset2_temp"
CONF_PRESET2_DURATION = "preset2_duration"
CONF_PRESET3_NAME = "preset3_name"
CONF_PRESET3_TEMP = "preset3_temp"
CONF_PRESET3_DURATION = "preset3_duration"
