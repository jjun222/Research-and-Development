import gc
import os
import socket
import time

import machine
import network
import ubinascii
import ujson as json
from machine import Pin
from simple import MQTTClient

# =========================================================
# Hardware
# =========================================================
# New relay circuit:
#   Pico GP16 HIGH -> C1815 ON -> Relay IN pulled LOW -> Relay ON -> Beacon ON
#   Pico GP16 LOW  -> C1815 OFF -> Relay OFF           -> Beacon OFF
#
# IMPORTANT hardware note:
#   Put a base resistor, typically 1 kΩ ~ 4.7 kΩ, between GP16 and C1815 Base.
#   Pico GND, relay GND, UBEC OUT-2 GND, and C1815 Emitter must share common GND.
# =========================================================
RELAY_CTRL_PIN = 16
RELAY_ACTIVE_HIGH = True

relay_ctrl = Pin(RELAY_CTRL_PIN, Pin.OUT, value=0)

def beacon_on():
    relay_ctrl.value(1 if RELAY_ACTIVE_HIGH else 0)

def beacon_off():
    relay_ctrl.value(0 if RELAY_ACTIVE_HIGH else 1)

try:
    ONBOARD_LED = Pin("LED", Pin.OUT)
except Exception:
    ONBOARD_LED = None

# =========================================================
# Device / MQTT configuration
# =========================================================
DEVICE_ID = "Beacon_1"
WIFI_SSID = ""
WIFI_PASSWORD = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"
BROKER = DEFAULT_BROKER_IP
PORT = 1883
KEEPALIVE = 60

TOPIC_CMD = f"beacon/{DEVICE_ID}"
TOPIC_STATUS = f"interfaceui/status/subscriber/{DEVICE_ID}"
TOPIC_HELLO = f"interfaceui/registry/hello/{DEVICE_ID}"
TOPIC_REQ = "interfaceui/registry/request"
TOPIC_LOG = f"interfaceui/logs/subscriber/{DEVICE_ID}"

# =========================================================
# Runtime / safety configuration
# =========================================================
WIFI_RETRY_MAX = 300
WIFI_RETRY_WAIT_MS = 500
MQTT_RECONNECT_MAX = 10
MAX_RECOVERY_FAILS = 8
COLD_BOOT_SETTLE_MS = 3_000
SOFT_BOOT_SETTLE_MS = 3_000
WIFI_POST_RESET_WAIT_MS = 1_500
POST_WIFI_CONNECT_SETTLE_MS = 1_000

WDT_TIMEOUT_MS = 8000
SOCKET_TIMEOUT_SEC = 3
PING_INTERVAL_MS = 30000
HELLO_INTERVAL_MS = 60000
STATUS_HEARTBEAT_MS = 60000
GC_INTERVAL_MS = 20000
FORCE_ROTATE_AFTER_PUBLISHES = 120
PUBLISH_FAIL_MAX = 5
NO_MQTT_ACTIVITY_MS = 180000

AP_SSID = f"{DEVICE_ID}_setup"
AP_PW = "123456789"

HTML_FORM = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>경광등 WiFi 설정</title></head>
<body>
<h2>Wi-Fi / MQTT 설정 (%s)</h2>
<form method="POST" action="/save">
SSID: <input name="ssid"><br>
PW: <input name="pw" type="password"><br>
Broker IP: <input name="broker" value="%s"><br>
<button type="submit">저장</button>
</form></body></html>
""" % (DEVICE_ID, DEFAULT_BROKER_IP)

HTML_SAVED = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
"""

# =========================================================
# Global state
# =========================================================
wlan = None
client = None
wdt = None
_effect_generation = 0
recovery_fail_count = 0
publish_success_count = 0
publish_fail_count = 0
last_mqtt_ok_ms = time.ticks_ms()

# =========================================================
# Basic helpers
# =========================================================
def now_ts():
    return int(time.time())

def note_mqtt_ok():
    global last_mqtt_ok_ms
    last_mqtt_ok_ms = time.ticks_ms()

def check_mqtt_inactivity(now_ms):
    if time.ticks_diff(now_ms, last_mqtt_ok_ms) >= NO_MQTT_ACTIVITY_MS:
        hard_recover("beacon mqtt inactivity timeout")

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

# =========================================================
# Wi-Fi / AP portal
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

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패")
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    time.sleep_ms(POST_WIFI_CONNECT_SETTLE_MS)
    blink_n(3)
    return True

def connect_wifi_from_config(force_reset=True):
    global BROKER
    config = load_wifi_config()
    if config:
        ssid = config.get("ssid")
        password = config.get("password")
        if ssid and password and try_connect_wifi(ssid, password, force_reset):
            BROKER = config.get("broker") or DEFAULT_BROKER_IP
            print("🌐 config로 Wi-Fi 연결 OK, broker =", BROKER)
            return True

    if WIFI_SSID and WIFI_PASSWORD and try_connect_wifi(WIFI_SSID, WIFI_PASSWORD, force_reset):
        BROKER = DEFAULT_BROKER_IP
        print("🌐 기본 설정으로 Wi-Fi 연결 OK, broker =", BROKER)
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
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
    wlan.active(False)

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
        "reset_cause": reset_cause_name(),
        "relay_pin": RELAY_CTRL_PIN,
        "relay_active_high": RELAY_ACTIVE_HIGH,
    }
    safe_publish(TOPIC_STATUS, payload, retain=True)

def publish_hello():
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
    ip = wlan.ifconfig()[0] if wlan.isconnected() else ""
    payload = {
        "id": DEVICE_ID,
        "ip": ip,
        "name": DEVICE_ID,
        "type": "subscriber",
        "ts": now_ts(),
        "relay_pin": RELAY_CTRL_PIN,
    }
    safe_publish(TOPIC_HELLO, payload, retain=True)
    log("info", "hello published", ip=ip)

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

def make_client():
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
    mac = wlan.config("mac")
    client_id = b"beacon-" + ubinascii.hexlify(mac)
    mqtt = MQTTClient(client_id, BROKER, port=PORT, keepalive=KEEPALIVE)
    will = json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber", "status": "offline", "ts": now_ts()})
    mqtt.set_last_will(TOPIC_STATUS, will, retain=True)
    return mqtt

def mqtt_connect_and_subscribe():
    global client, publish_success_count, publish_fail_count
    print("📡 MQTT 연결 시도 중... (broker =", BROKER, ")")
    close_mqtt_client()
    client = make_client()
    try:
        client.set_callback(handle_message)
        client.connect()
        apply_socket_timeout()
        client.subscribe(TOPIC_CMD, qos=1)
        client.subscribe(TOPIC_REQ, qos=1)
        publish_success_count = 0
        publish_fail_count = 0
        publish_status(True, "mqtt_connected")
        publish_hello()
        note_mqtt_ok()
        log("info", "mqtt connected")
        blink_n(5)
        return True
    except Exception as exc:
        print("MQTT connect err:", exc)
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
    beacon_off()
    set_led(False)
    time.sleep(2)
    machine.reset()

# =========================================================
# Effect helpers / command handler
# =========================================================
def new_effect_token():
    global _effect_generation
    _effect_generation = (_effect_generation + 1) & 0x7FFFFFFF
    return _effect_generation

def is_current_effect(token):
    return token == _effect_generation

def sleep_with_token(duration_ms, token):
    end = time.ticks_add(time.ticks_ms(), duration_ms)
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
        time.sleep(0.05)
    return True

def handle_message(topic_bytes, message_bytes):
    topic = topic_bytes.decode() if isinstance(topic_bytes, bytes) else str(topic_bytes)
    raw = message_bytes.decode() if isinstance(message_bytes, bytes) else str(message_bytes)
    note_mqtt_ok()
    print("🔔", topic, "→", raw)

    try:
        data = json.loads(raw) if raw and raw[0] in "[{" else {"text": raw}
        command = (data.get("command") or data.get("text", "")).strip()
    except Exception:
        log("error", "json parse failed", raw=raw)
        return

    if topic == TOPIC_REQ:
        publish_hello()
        return

    if command == "beacon_fire_alert":
        token = new_effect_token()
        duration_ms = int(data.get("duration_ms", 10000))
        on_ms = int(data.get("on_ms", 250))
        off_ms = int(data.get("off_ms", 250))
        blink_once(40, 40)
        end = time.ticks_add(time.ticks_ms(), duration_ms)
        state = False
        while time.ticks_diff(end, time.ticks_ms()) > 0 and is_current_effect(token):
            state = not state
            set_led(state)
            if state:
                beacon_on()
            else:
                beacon_off()
            if not sleep_with_token(on_ms if state else off_ms, token):
                break
        if is_current_effect(token):
            beacon_off()
            set_led(False)
        return

    if command == "beacon_stop":
        new_effect_token()
        beacon_off()
        set_led(False)
        return

    log("warn", "unknown cmd", cmd=command)

# =========================================================
# Main loop
# =========================================================
def main():
    global recovery_fail_count

    beacon_off()
    set_led(False)
    blink_n(2)
    print("====================================")
    print("🚀 BEACON BOOT START")
    print("🧾 reset cause =", reset_cause_name())
    print("🔌 relay control pin = GP%d" % RELAY_CTRL_PIN)
    print("====================================")
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
                close_mqtt_client()

            if client is None:
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("beacon mqtt disconnected")
                    time.sleep(2)
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
                hard_recover("beacon publish failures")

            maybe_rotate_mqtt()
            recovery_fail_count = 0
            time.sleep_ms(20)
        except Exception as exc:
            print("loop err:", exc)
            log("error", "mqtt loop error", error=str(exc))
            close_mqtt_client()
            recovery_fail_count += 1
            blink_once(200, 200)
            if recovery_fail_count >= MAX_RECOVERY_FAILS:
                hard_recover("beacon mqtt loop stuck")
            time.sleep(2)

main()

