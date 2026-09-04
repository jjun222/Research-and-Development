"""CareCall chest Pico 2 W configuration."""

# ---------------------------------------------------------------------------
# Jetson setup AP and device provisioning
# ---------------------------------------------------------------------------
# This is the setup AP already created on the Jetson.
SETUP_AP_SSID = "CareCall-Jetson-Setup"
SETUP_AP_PASSWORD = "12345678"
SETUP_AP_IP = "10.42.0.1"
SETUP_AP_PORT = 80

# On the Jetson, run:
#   sudo cat /etc/carecall/device-provision.token
# Paste that value below. Never paste the token into chat or a public repo.
DEVICE_PROVISION_TOKEN = "***가림***"

# The received router credentials are stored on the Pico flash.
WIFI_CREDENTIALS_FILE = "wifi_config.json"
WIFI_CREDENTIALS_BACKUP_FILE = "wifi_config.bak"

# The Pico retries forever, so Jetson/Pico power-on order does not matter.
WIFI_CONNECT_TIMEOUT_MS = 30000
SETUP_AP_CONNECT_TIMEOUT_MS = 30000
SETUP_AP_RETRY_MS = 10000
PROVISION_POLL_MS = 1000
PROVISION_HTTP_TIMEOUT_SECONDS = 10
PROVISION_MAX_RESPONSE_BYTES = 4096

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
DEVICE_OFFLINE_MS = 15000
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

