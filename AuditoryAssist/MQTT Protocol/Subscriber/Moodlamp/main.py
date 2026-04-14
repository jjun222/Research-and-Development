
import gc
import os
import socket
import time

import machine
import network
import neopixel
import ujson as json
import ubinascii
from umqtt.simple import MQTTClient

# =========================================================
# Device / MQTT configuration
# =========================================================
DEVICE_ID = "Neopixel_1"
NEO_PIN = 28
NUM_LED = 12

WIFI_SSID = ""
WIFI_PASSWORD = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

MQTT_BROKER = DEFAULT_BROKER_IP
MQTT_PORT = 1883
KEEPALIVE = 60

TOPIC_CMD_THIS = f"neopixel/{DEVICE_ID}"
TOPIC_CMD_ALL = "neopixel/ALL"
TOPIC_STATUS = f"interfaceui/status/subscriber/{DEVICE_ID}"
TOPIC_HELLO = f"interfaceui/registry/hello/{DEVICE_ID}"
TOPIC_REQ = "interfaceui/registry/request"
TOPIC_LOG = f"interfaceui/logs/subscriber/{DEVICE_ID}"

DEFAULT_MOOD_RGB = (250, 248, 104)
DEFAULT_BRIGHTNESS = 255

# =========================================================
# Runtime / safety configuration
# =========================================================
AP_SSID = f"{DEVICE_ID}_setup"
AP_PW = "123456789"

# Cold boot stabilization
WIFI_RETRY_MAX = 30
WIFI_RETRY_WAIT_MS = 500
COLD_BOOT_SETTLE_MS = 12_000
SOFT_BOOT_SETTLE_MS = 3_000
WIFI_POST_RESET_WAIT_MS = 1_500
POST_WIFI_CONNECT_SETTLE_MS = 1_000

WDT_TIMEOUT_MS = 8000
SOCKET_TIMEOUT_SEC = 3
PING_INTERVAL_MS = 30000
HELLO_INTERVAL_MS = 60000
STATUS_HEARTBEAT_MS = 60000
GC_INTERVAL_MS = 20000
MQTT_RECONNECT_MAX = 10
MAX_RECOVERY_FAILS = 8
FORCE_ROTATE_AFTER_PUBLISHES = 120
MAIN_LOOP_SLEEP_MS = 20
PUBLISH_FAIL_MAX = 5
NO_MSG_REFRESH_MS = 180000
NO_MQTT_ACTIVITY_MS = 180000

HTML_FORM = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>WiFi 설정</title></head>
<body>
<h2>Wi-Fi / MQTT 설정</h2>
<form method="POST" action="/save">
SSID: <input name="ssid"><br>
PW: <input name="pw" type="password"><br>
Broker IP: <input name="broker" value="%s"><br>
<button type="submit">저장</button>
</form></body></html>
""" % DEFAULT_BROKER_IP

HTML_SAVED = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
"""

# =========================================================
# Hardware / global state
# =========================================================
try:
    ONBOARD_LED = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    ONBOARD_LED = None

np = neopixel.NeoPixel(machine.Pin(NEO_PIN), NUM_LED)

mood_rgb = DEFAULT_MOOD_RGB
mood_brightness = DEFAULT_BRIGHTNESS

wlan = None
client = None
wdt = None

_effect_generation = 0
recovery_fail_count = 0
publish_success_count = 0
publish_fail_count = 0
last_command_ms = time.ticks_ms()
last_mqtt_ok_ms = time.ticks_ms()

NAMED_COLORS = {
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "purple": (128, 0, 128),
    "brown": (165, 42, 42),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}

# =========================================================
# Basic helpers
# =========================================================
def now_ts():
    return int(time.time())

def get_ip():
    try:
        if wlan is not None and wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return ""

def reset_cause_name():
    cause = machine.reset_cause()
    mapping = {}
    for name in ("PWRON_RESET", "HARD_RESET", "WDT_RESET", "DEEPSLEEP_RESET", "SOFT_RESET"):
        if hasattr(machine, name):
            mapping[getattr(machine, name)] = name
    return mapping.get(cause, str(cause))

def get_boot_settle_ms():
    cause = reset_cause_name()
    if cause in ("PWRON_RESET", "HARD_RESET", "WDT_RESET"):
        return COLD_BOOT_SETTLE_MS
    return SOFT_BOOT_SETTLE_MS

def pre_boot_stabilize():
    wait_ms = get_boot_settle_ms()
    if wait_ms <= 0:
        return
    print("⏳ 초기 전원 안정화 대기:", wait_ms // 1000, "초")
    end_ms = time.ticks_add(time.ticks_ms(), wait_ms)
    last_log = -1
    while time.ticks_diff(end_ms, time.ticks_ms()) > 0:
        remain_sec = max(0, time.ticks_diff(end_ms, time.ticks_ms()) // 1000)
        if remain_sec != last_log:
            print("⏳ 부팅 안정화 중... 남은 시간:", remain_sec, "초")
            last_log = remain_sec
        time.sleep_ms(250)

def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value

def hex_to_rgb(value):
    value = value.strip().lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

def apply_brightness(rgb, brightness):
    r, g, b = rgb
    return (r * brightness // 255, g * brightness // 255, b * brightness // 255)

def set_led(on):
    if ONBOARD_LED is not None:
        ONBOARD_LED.value(1 if on else 0)

def blink_once(on_ms=80, off_ms=80):
    set_led(True)
    time.sleep_ms(on_ms)
    set_led(False)
    time.sleep_ms(off_ms)

def blink_n(count, on_ms=80, off_ms=80):
    for _ in range(count):
        blink_once(on_ms, off_ms)

def set_all(color):
    for i in range(NUM_LED):
        np[i] = color
    np.write()

def restore_mood():
    set_all(apply_brightness(mood_rgb, mood_brightness))

def start_watchdog():
    global wdt
    try:
        wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
        print("🛡️ WDT 시작:", WDT_TIMEOUT_MS, "ms")
    except Exception as exc:
        wdt = None
        print("⚠️ WDT 시작 실패:", exc)

def feed_wdt():
    try:
        if wdt is not None:
            wdt.feed()
    except Exception:
        pass

def maybe_gc(last_gc_ms):
    now = time.ticks_ms()
    if time.ticks_diff(now, last_gc_ms) >= GC_INTERVAL_MS:
        gc.collect()
        return now
    return last_gc_ms

def note_mqtt_ok():
    global last_mqtt_ok_ms
    last_mqtt_ok_ms = time.ticks_ms()

def check_mqtt_inactivity(now_ms):
    if time.ticks_diff(now_ms, last_mqtt_ok_ms) >= NO_MQTT_ACTIVITY_MS:
        hard_recover("neopixel mqtt inactivity timeout")

# =========================================================
# Wi-Fi configuration / AP portal
# =========================================================
def load_wifi_config():
    if CONFIG_PATH not in os.listdir():
        return None
    try:
        with open(CONFIG_PATH, "r") as file:
            return json.loads(file.read())
    except Exception as exc:
        print("⚠️ config load 실패:", exc)
        return None

def save_wifi_config(ssid, password, broker_ip=None):
    config = {"ssid": ssid, "password": password}
    if broker_ip:
        config["broker"] = broker_ip
    with open(CONFIG_PATH, "w") as file:
        file.write(json.dumps(config))
    print("✅ Wi-Fi 설정 저장 완료:", config)

def radio_reset():
    global wlan
    try:
        network.WLAN(network.AP_IF).active(False)
    except Exception:
        pass

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    try:
        wlan.disconnect()
    except Exception:
        pass

    try:
        wlan.active(False)
    except Exception:
        pass
    time.sleep_ms(300)

    try:
        wlan.active(True)
    except Exception:
        pass
    time.sleep_ms(WIFI_POST_RESET_WAIT_MS)

def try_connect_wifi(ssid, password, force_reset=True):
    global wlan
    if not ssid or not password:
        return False

    if force_reset:
        radio_reset()
    elif wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

    if wlan.isconnected():
        print("✅ 이미 Wi-Fi 연결 상태:", wlan.ifconfig())
        return True

    print("📡 Wi-Fi 연결 시도:", ssid)
    try:
        wlan.connect(ssid, password)
    except Exception as exc:
        print("❌ wlan.connect 실패:", exc)
        return False

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
        feed_wdt()
        attempt += 1
        if attempt == 1 or (attempt % 5 == 0):
            print("📶 Wi-Fi 연결 대기 중...", attempt, "/", WIFI_RETRY_MAX)
        time.sleep_ms(WIFI_RETRY_WAIT_MS)

    if wlan.isconnected():
        print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
        time.sleep_ms(POST_WIFI_CONNECT_SETTLE_MS)
        blink_n(3)
        return True

    print("❌ Wi-Fi 연결 실패")
    return False

def connect_wifi_from_config(force_reset=True):
    global MQTT_BROKER

    config = load_wifi_config()
    if config:
        ssid = config.get("ssid")
        password = config.get("password")
        if ssid and password and try_connect_wifi(ssid, password, force_reset):
            MQTT_BROKER = config.get("broker") or DEFAULT_BROKER_IP
            print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
            return True

    if WIFI_SSID and WIFI_PASSWORD and try_connect_wifi(WIFI_SSID, WIFI_PASSWORD, force_reset):
        MQTT_BROKER = DEFAULT_BROKER_IP
        print("🌐 기본 설정으로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
        return True

    return False

def wifi_ensure():
    global wlan
    if wlan is None or (not wlan.isconnected()):
        return connect_wifi_from_config(force_reset=True)
    return True

def url_decode(value):
    result = ""
    i = 0
    while i < len(value):
        char = value[i]
        if char == "+":
            result += " "
        elif char == "%" and i + 2 < len(value):
            try:
                result += chr(int(value[i + 1:i + 3], 16))
                i += 2
            except Exception:
                result += char
        else:
            result += char
        i += 1
    return result

def parse_form(body):
    result = {}
    for part in body.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key] = url_decode(value)
    return result

def start_config_portal():
    network.WLAN(network.STA_IF).active(False)
    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PW)
    ap.active(True)
    print("📶 AP 모드 시작:", ap.ifconfig())

    address = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    server = socket.socket()
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    server.bind(address)
    server.listen(1)

    while True:
        connection, _ = server.accept()
        request = connection.recv(1024)
        try:
            request_str = request.decode()
        except Exception:
            request_str = ""

        if "POST /save" in request_str:
            body = request_str.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in request_str else ""
            form = parse_form(body)
            ssid = form.get("ssid", "").strip()
            password = form.get("pw", "").strip()
            broker = form.get("broker", "").strip()
            if ssid and password:
                save_wifi_config(ssid, password, broker or None)
                connection.send(HTML_SAVED.encode())
                connection.close()
                time.sleep(3)
                machine.reset()
            else:
                connection.send(HTML_FORM.encode())
                connection.close()
        else:
            connection.send(HTML_FORM.encode())
            connection.close()

def startup_wifi_or_portal():
    if connect_wifi_from_config(force_reset=True):
        return True
    start_config_portal()
    return False

# =========================================================
# MQTT helpers
# =========================================================
def close_mqtt_client():
    global client
    try:
        if client is not None:
            try:
                if hasattr(client, "sock") and client.sock is not None:
                    try:
                        client.sock.close()
                    except Exception:
                        pass
                    client.sock = None
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
    except Exception:
        pass
    client = None
    gc.collect()

def apply_socket_timeout():
    global client
    try:
        if client is not None and hasattr(client, "sock") and client.sock is not None:
            client.sock.settimeout(SOCKET_TIMEOUT_SEC)
            print("⏱️ MQTT socket timeout =", SOCKET_TIMEOUT_SEC, "sec")
    except Exception as exc:
        print("⚠️ socket timeout 설정 실패:", exc)

def safe_publish(topic, payload, retain=False):
    global publish_success_count, publish_fail_count
    if client is None:
        return False
    try:
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        client.publish(topic, payload, retain=retain)
        publish_success_count += 1
        publish_fail_count = 0
        note_mqtt_ok()
        return True
    except Exception as exc:
        publish_fail_count += 1
        print("⚠️ publish 실패:", exc)
        return False

def publish_status(online=True, reason="heartbeat"):
    payload = {
        "id": DEVICE_ID,
        "name": DEVICE_ID,
        "type": "subscriber",
        "status": "online" if online else "offline",
        "reason": reason,
        "ts": now_ts(),
        "ip": get_ip(),
        "reset_cause": reset_cause_name(),
    }
    safe_publish(TOPIC_STATUS, payload, retain=True)

def publish_hello():
    payload = {
        "id": DEVICE_ID,
        "ip": get_ip(),
        "name": DEVICE_ID,
        "type": "subscriber",
        "ts": now_ts(),
    }
    safe_publish(TOPIC_HELLO, payload, retain=True)
    log("info", "hello published", ip=get_ip())

def log(level, message, **extra):
    try:
        print(f"[{DEVICE_ID}][{level}] {message} {extra if extra else ''}")
    except Exception:
        pass

    try:
        record = {"id": DEVICE_ID, "type": "subscriber", "level": level, "msg": message, "ts": now_ts()}
        if extra:
            record.update(extra)
        safe_publish(TOPIC_LOG, record, retain=False)
    except Exception:
        pass

def make_client():
    client_id = b"pico-" + ubinascii.hexlify(machine.unique_id())
    mqtt = MQTTClient(client_id, MQTT_BROKER, port=MQTT_PORT, keepalive=KEEPALIVE)
    will = json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber", "status": "offline", "ts": now_ts()})
    mqtt.set_last_will(TOPIC_STATUS, will, retain=True)
    return mqtt

def mqtt_connect_and_subscribe():
    global client, publish_success_count, publish_fail_count, last_command_ms
    print("📡 MQTT 연결 시도 중... (broker =", MQTT_BROKER, ")")
    close_mqtt_client()
    client = make_client()

    try:
        client.set_callback(handle_message)
        client.connect()
        apply_socket_timeout()
        client.subscribe(TOPIC_CMD_THIS, qos=1)
        client.subscribe(TOPIC_CMD_ALL, qos=1)
        client.subscribe(TOPIC_REQ, qos=1)
        publish_success_count = 0
        publish_fail_count = 0
        publish_status(True, "mqtt_connected")
        publish_hello()
        last_command_ms = time.ticks_ms()
        note_mqtt_ok()
        log("info", "mqtt connected")
        return True
    except Exception as exc:
        print("❌ MQTT 연결 실패:", exc)
        close_mqtt_client()
        return False

def mqtt_reconnect_with_backoff():
    backoff = 0.5
    for _ in range(MQTT_RECONNECT_MAX):
        feed_wdt()
        if not wifi_ensure():
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            continue
        if mqtt_connect_and_subscribe():
            return True
        time.sleep(backoff)
        backoff = min(backoff * 2, 5)
    return False

def maybe_rotate_mqtt():
    global publish_success_count
    if publish_success_count >= FORCE_ROTATE_AFTER_PUBLISHES:
        print("♻️ 정기 MQTT 회전 실행 (publish count =", publish_success_count, ")")
        if mqtt_reconnect_with_backoff():
            publish_success_count = 0

def hard_recover(reason="unknown"):
    print("♻️ 하드 복구 실행:", reason)
    blink_n(4, 120, 120)
    close_mqtt_client()
    try:
        if wlan is not None:
            wlan.active(False)
            time.sleep(1)
            wlan.active(True)
            time.sleep(1)
    except Exception:
        pass
    time.sleep(2)
    machine.reset()

# =========================================================
# Effect helpers
# =========================================================
def new_effect_token():
    global _effect_generation
    _effect_generation = (_effect_generation + 1) & 0x7FFFFFFF
    return _effect_generation

def is_current_effect(token):
    return token == _effect_generation

def sleep_with_token(seconds, token, poll=0.05):
    end = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        feed_wdt()
        try:
            if client is not None:
                client.check_msg()
                note_mqtt_ok()
        except Exception:
            pass
        if not is_current_effect(token):
            return False
        time.sleep(poll)
    return is_current_effect(token)

def handle_hex_flash(data):
    token = new_effect_token()
    try:
        rgb = hex_to_rgb((data.get("color") or "#FFFFFF").strip())
    except Exception:
        rgb = (255, 255, 255)

    set_all(apply_brightness(rgb, mood_brightness))
    duration_sec = int(data.get("duration_ms", 5000)) / 1000.0
    if sleep_with_token(duration_sec, token):
        restore_mood()

def handle_named_blink(cmd):
    token = new_effect_token()
    name = cmd.replace("_blink_3s", "")
    blink_color = apply_brightness(NAMED_COLORS.get(name, NAMED_COLORS["white"]), mood_brightness)
    base_color = apply_brightness(mood_rgb, mood_brightness)
    end = time.ticks_add(time.ticks_ms(), 3000)
    on = False
    while time.ticks_diff(end, time.ticks_ms()) > 0 and is_current_effect(token):
        set_all(blink_color if on else base_color)
        on = not on
        sleep_with_token(0.25, token)
    if is_current_effect(token):
        restore_mood()

def handle_red_blink(duration_ms=10000):
    token = new_effect_token()
    red_color = apply_brightness(NAMED_COLORS["red"], mood_brightness)
    base_color = apply_brightness(mood_rgb, mood_brightness)
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    on = False
    while time.ticks_diff(end, time.ticks_ms()) > 0 and is_current_effect(token):
        set_all(red_color if on else base_color)
        on = not on
        sleep_with_token(0.25, token)
    if is_current_effect(token):
        restore_mood()

# =========================================================
# Message handling
# =========================================================
def handle_message(topic_bytes, message_bytes):
    global mood_rgb, mood_brightness, last_command_ms

    topic = topic_bytes.decode() if isinstance(topic_bytes, bytes) else str(topic_bytes)
    raw = message_bytes.decode() if isinstance(message_bytes, bytes) else str(message_bytes)
    last_command_ms = time.ticks_ms()
    note_mqtt_ok()
    log("debug", "cmd recv", topic=topic, raw=raw[:120])

    try:
        data = json.loads(raw) if raw and raw[0] in "[{" else {"text": raw}
        cmd = (data.get("command") or data.get("text", "")).strip()
        sensor_id = data.get("sensor_id")

        if topic == TOPIC_REQ:
            publish_hello()
            return

        if cmd in ("fire_warning", "yellow_flash"):
            cmd = "hex_flash"
            if sensor_id == "gas_sensor_pico":
                data["color"] = data.get("color", "#8300FD")
            else:
                data["color"] = data.get("color", "#FD6A00")
            data["duration_ms"] = data.get("duration_ms", 5000)

        if cmd == "purple_blink_3s" and sensor_id == "water_level_1":
            cmd = "hex_flash"
            data["color"] = "#0045FD"
            data["duration_ms"] = 5000

        if cmd == "brown_blink_3s" and sensor_id == "doorbell_1":
            cmd = "hex_flash"
            data["color"] = "#00FD05"
            data["duration_ms"] = 5000

        if cmd == "set_mood":
            new_effect_token()
            mood_rgb = hex_to_rgb((data.get("color") or "#FFFFFF").strip())
            mood_brightness = clamp(int(data.get("brightness", DEFAULT_BRIGHTNESS)), 0, 255)
            restore_mood()
            return

        if cmd == "hex_flash":
            handle_hex_flash(data)
            return

        if cmd.endswith("_blink_3s"):
            handle_named_blink(cmd)
            return

        if cmd in ("fire_confirmed", "red_blink"):
            handle_red_blink(10000)
            return

        if cmd in ("off", "black"):
            new_effect_token()
            set_all((0, 0, 0))
            return

        log("warn", "unknown cmd", cmd=cmd)
    except Exception as exc:
        log("error", "handle_message error", error=str(exc))

# =========================================================
# Main loop
# =========================================================
def main():
    global recovery_fail_count, last_command_ms

    gc.collect()
    set_led(False)
    restore_mood()
    blink_n(2, 100, 100)
    pre_boot_stabilize()

    startup_wifi_or_portal()
    while not mqtt_connect_and_subscribe():
        time.sleep(3)

    start_watchdog()

    last_ping_ms = time.ticks_ms()
    last_hello_ms = time.ticks_ms()
    last_status_ms = time.ticks_ms()
    last_gc_ms = time.ticks_ms()

    while True:
        try:
            feed_wdt()
            last_gc_ms = maybe_gc(last_gc_ms)
            now_ms = time.ticks_ms()
            check_mqtt_inactivity(now_ms)

            if not wifi_ensure():
                recovery_fail_count += 1
                if recovery_fail_count >= MAX_RECOVERY_FAILS:
                    hard_recover("neopixel wifi disconnected")
                time.sleep_ms(1000)
                continue

            if client is None:
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("neopixel mqtt disconnected")
                    time.sleep_ms(1000)
                    continue
                recovery_fail_count = 0
                last_ping_ms = time.ticks_ms()
                last_hello_ms = time.ticks_ms()
                last_status_ms = time.ticks_ms()

            client.check_msg()
            note_mqtt_ok()

            if time.ticks_diff(now_ms, last_ping_ms) >= PING_INTERVAL_MS:
                try:
                    client.ping()
                    note_mqtt_ok()
                    last_ping_ms = now_ms
                except Exception:
                    close_mqtt_client()

            if time.ticks_diff(now_ms, last_hello_ms) >= HELLO_INTERVAL_MS:
                publish_hello()
                last_hello_ms = now_ms

            if time.ticks_diff(now_ms, last_status_ms) >= STATUS_HEARTBEAT_MS:
                publish_status(True, "heartbeat")
                last_status_ms = now_ms

            if publish_fail_count >= PUBLISH_FAIL_MAX:
                hard_recover("neopixel publish failures")

            if time.ticks_diff(now_ms, last_command_ms) >= NO_MSG_REFRESH_MS:
                publish_hello()
                last_command_ms = now_ms

            maybe_rotate_mqtt()
            recovery_fail_count = 0
            time.sleep_ms(MAIN_LOOP_SLEEP_MS)
        except Exception as exc:
            print("❌ MQTT 오류:", exc)
            log("error", "mqtt loop error", error=str(exc))
            close_mqtt_client()
            recovery_fail_count += 1
            if recovery_fail_count >= MAX_RECOVERY_FAILS:
                hard_recover("neopixel mqtt loop stuck")
            time.sleep_ms(2000)

main()
