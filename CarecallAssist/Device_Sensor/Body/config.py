"""CareCall chest Pico 2 W configuration."""

# ---------------------------------------------------------------------------
# Jetson setup AP and device provisioning
# ---------------------------------------------------------------------------
SETUP_AP_SSID = "CareCall-Jetson-Setup"
SETUP_AP_PASSWORD = " "
SETUP_AP_IP = "10.42.0.1"
SETUP_AP_PORT = 80

# IMPORTANT:
# Replace this placeholder with the REAL token already used by your Body Pico.
# Never commit the real token to a public repository.
DEVICE_PROVISION_TOKEN = " "

WIFI_CREDENTIALS_FILE = "wifi_config.json"
WIFI_CREDENTIALS_BACKUP_FILE = "wifi_config.bak"

WIFI_CONNECT_TIMEOUT_MS = 30000
SETUP_AP_CONNECT_TIMEOUT_MS = 30000
SETUP_AP_RETRY_MS = 10000
PROVISION_POLL_MS = 1000
PROVISION_HTTP_TIMEOUT_SECONDS = 3
PROVISION_MAX_RESPONSE_BYTES = 4096

# Network recovery settings.
WIFI_RECONNECT_DELAY_MS = 5000
WIFI_RESET_OFF_MS = 1000
WIFI_DISABLE_POWER_SAVE = True

# Hardware watchdog / blocking-network protection.
# Pico 2 W uses the RP2 port. 8000 ms is conservative and also fits RP2040 limits.
WATCHDOG_ENABLED = True
WATCHDOG_TIMEOUT_MS = 8000

# MQTT operations are bounded so a dead Jetson does not freeze the Body loop.
MQTT_CONNECT_TIMEOUT_SECONDS = 2
MQTT_SOCKET_TIMEOUT_SECONDS = 2
MQTT_RESOLVE_RETRY_MS = 15000

# ---------------------------------------------------------------------------
# Chest gateway protocol
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 1
GATEWAY_ID = "chest"

DISCOVERY_PORT = 5004
DATA_PORT = 5005

ALLOWED_DEVICES = (
    "right_arm",
    "left_arm",
    "right_leg",
    "left_leg",
)

BUFFER_LIMIT = 20
DEVICE_OFFLINE_MS = 30000
SUMMARY_INTERVAL_MS = 5000

# ---------------------------------------------------------------------------
# Jetson MQTT
# ---------------------------------------------------------------------------
JETSON_HOST = "jetson.local"
MQTT_PORT = 1883
MQTT_USER = None
MQTT_PASSWORD = None
MQTT_KEEPALIVE = 30
MQTT_RETRY_MS = 5000
MQTT_PING_MS = 15000
MQTT_FLUSH_PER_LOOP = 4


