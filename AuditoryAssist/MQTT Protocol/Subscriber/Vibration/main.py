import gc
import os
import socket
import time

import machine
import network
import ubinascii
import ujson
from micropython import const
from machine import Pin

try:
    from robust import MQTTClient
except Exception:
    from simple import MQTTClient

# =========================================================
# Device / MQTT configuration
# =========================================================
DEVICE_ID = "Vibrator_1"
CLIENT_ID = b"PICO_" + ubinascii.hexlify(machine.unique_id())

WIFI_SSID = ""
WIFI_PASSWORD = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = ""
MQTT_BROKER = ""

PORT = const(1883)
KEEPALIVE = const(30)
DISCOVERY_PORT = const(30303)
DISCOVERY_TIMEOUT_MS = const(2000)

TOPIC_CMD_THIS = b"vibrator/%s" % DEVICE_ID.encode()
TOPIC_CMD_ALL = b"vibrator/broadcast"
TOPIC_STATUS = f"interfaceui/status/subscriber/{DEVICE_ID}"
TOPIC_HELLO = f"interfaceui/registry/hello/{DEVICE_ID}"
TOPIC_REQ = "interfaceui/registry/request"
TOPIC_LOG = f"interfaceui/logs/subscriber/{DEVICE_ID}"

# =========================================================
# Runtime / safety configuration
# =========================================================
WIFI_RETRY_MAX = const(300)
WIFI_RETRY_WAIT_MS = const(500)
COLD_BOOT_SETTLE_MS = const(3000)
SOFT_BOOT_SETTLE_MS = const(3000)
WIFI_POST_RESET_WAIT_MS = const(1500)
POST_WIFI_CONNECT_SETTLE_MS = const(1000)

MAIN_LOOP_SLEEP_MS = const(5)
RECONNECT_DELAY_MS = const(2000)
MAX_RECOVERY_FAILS = const(8)
MQTT_RECONNECT_MAX = const(10)
GC_INTERVAL_MS = const(20000)
WDT_TIMEOUT_MS = const(8000)
PING_INTERVAL_MS = const(15000)
SOCKET_TIMEOUT_SEC = const(3)
STATUS_HEARTBEAT_MS = const(60000)
FORCE_ROTATE_AFTER_PUBLISHES = const(120)
PUBLISH_FAIL_MAX = const(5)
NO_MQTT_ACTIVITY_MS = const(180000)

AP_SSID = "vibrator_setup"
AP_PW = "123456789"

HTML_FORM = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>WiFi 설정</title></head>
<body><h2>Wi-Fi / MQTT 설정</h2>
<form method="POST" action="/save">
SSID: <input name="ssid"><br>
PW: <input name="pw" type="password"><br>
Broker IP: <input name="broker" value="%s"><br>
<small>비워두면 같은 네트워크에서 MQTT Broker를 자동 검색합니다.</small><br>
<button type="submit">저장</button>
</form></body></html>
""" % DEFAULT_BROKER_IP

HTML_SAVED = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>설정이 저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
"""

# =========================================================
# Hardware / global state
# =========================================================
try:
    LED = Pin("LED", Pin.OUT)
except Exception:
    LED = None

IN1 = Pin(15, Pin.OUT)
IN2 = Pin(14, Pin.OUT)
IN3 = Pin(13, Pin.OUT)
IN4 = Pin(12, Pin.OUT)

_pattern_state = {
    "active": False,
    "end_ms": 0,
    "on_ms": 300,
    "off_ms": 300,
    "next_toggle_ms": 0,
    "vibrating": False,
    "intensity": 0.8,
}

wlan = None
client = None
wdt = None
recovery_fail_count = 0
publish_success_count = 0
publish_fail_count = 0
last_mqtt_ok_ms = time.ticks_ms()

# =========================================================
# Basic helpers
# =========================================================
def now_ts():
    return int(time.time())

def ticks_ms():
    return time.ticks_ms()

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

def note_mqtt_ok():
    global last_mqtt_ok_ms
    last_mqtt_ok_ms = ticks_ms()

def check_mqtt_inactivity(now_ms):
    if time.ticks_diff(now_ms, last_mqtt_ok_ms) >= NO_MQTT_ACTIVITY_MS:
        hard_recover("vibrator mqtt inactivity timeout")

def led_off():
    if LED:
        LED.value(0)

def led_blink(count=1, on_ms=120, off_ms=120):
    if not LED:
        return
    for _ in range(count):
        LED.value(1)
        time.sleep_ms(on_ms)
        LED.value(0)
        time.sleep_ms(off_ms)

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
    now = ticks_ms()
    if time.ticks_diff(now, last_gc_ms) >= GC_INTERVAL_MS:
        gc.collect()
        return now
    return last_gc_ms

# =========================================================
# Motor helpers
# =========================================================
def set_motor_forward():
    IN1.value(0)
    IN2.value(1)
    IN3.value(1)
    IN4.value(0)

def set_power_ratio(ratio):
    if ratio <= 0:
        duty = 0
    elif ratio >= 1.0:
        duty = 65535
    else:
        duty = int(65535 * ratio)

def stop_all():
    set_power_ratio(0.0)
    IN1.value(0)
    IN2.value(0)
    IN3.value(0)
    IN4.value(0)

def start_fire_alert(duration_ms=10000, on_ms=400, off_ms=200, intensity=0.85):
    set_motor_forward()
    now = ticks_ms()
    _pattern_state.update(
        {
            "active": True,
            "end_ms": time.ticks_add(now, int(duration_ms)),
            "on_ms": int(on_ms),
            "off_ms": int(off_ms),
            "next_toggle_ms": now,
            "vibrating": False,
            "intensity": max(0.0, min(1.0, float(intensity))),
        }
    )

def stop_pattern():
    _pattern_state["active"] = False
    stop_all()

def pattern_tick():
    if not _pattern_state["active"]:
        return

    now = ticks_ms()
    if time.ticks_diff(_pattern_state["end_ms"], now) <= 0:
        stop_pattern()
        return

    if time.ticks_diff(now, _pattern_state["next_toggle_ms"]) >= 0:
        if _pattern_state["vibrating"]:
            set_power_ratio(0.0)
            _pattern_state["vibrating"] = False
            _pattern_state["next_toggle_ms"] = time.ticks_add(now, _pattern_state["off_ms"])
        else:
            set_motor_forward()
            set_power_ratio(_pattern_state["intensity"])
            _pattern_state["vibrating"] = True
            _pattern_state["next_toggle_ms"] = time.ticks_add(now, _pattern_state["on_ms"])

# =========================================================
# Wi-Fi configuration / AP portal
# =========================================================
def load_wifi_config():
    if CONFIG_PATH not in os.listdir():
        return None
    try:
        with open(CONFIG_PATH, "r") as file:
            return ujson.loads(file.read())
    except Exception as exc:
        print("⚠️ config load 실패:", exc)
        return None

def save_wifi_config(ssid, password, broker_ip=None):
    config = {"ssid": ssid, "password": password}
    if broker_ip:
        config["broker"] = broker_ip
    with open(CONFIG_PATH, "w") as file:
        file.write(ujson.dumps(config))
    print("✅ Wi-Fi 설정 저장 완료:", config)

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

def wifi_is_connected():
    return wlan is not None and wlan.active() and wlan.isconnected()

def try_connect_wifi(ssid, password, force_reset=True):
    global wlan
    if not ssid or not password:
        return False

    if force_reset:
        radio_reset()
    elif wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

    if wifi_is_connected():
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

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패")
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    time.sleep_ms(POST_WIFI_CONNECT_SETTLE_MS)
    led_blink(3, 80, 80)
    return True

def _candidate_broadcast_addresses():
    addrs = ["255.255.255.255"]
    try:
        if wifi_is_connected():
            ip = wlan.ifconfig()[0]
            parts = ip.split(".")
            if len(parts) == 4:
                addrs.append("%s.%s.%s.255" % (parts[0], parts[1], parts[2]))
    except Exception:
        pass
    return addrs

def discover_mqtt_broker(timeout_ms=DISCOVERY_TIMEOUT_MS):
    """
    UDP Discovery로 MQTT Broker IP를 찾는다.
    판단서버가 UDP 30303에서 MQTT_DISCOVER 요청에 응답해야 한다.
    실패하면 None을 반환하며, AP 설정 모드의 수동 Broker 입력을 fallback으로 사용한다.
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(timeout_ms / 1000)
        except Exception:
            s.settimeout(2)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass

        for bcast in _candidate_broadcast_addresses():
            try:
                s.sendto(b"MQTT_DISCOVER", (bcast, DISCOVERY_PORT))
            except Exception as exc:
                print("⚠️ discovery broadcast 실패:", bcast, exc)

        data, addr = s.recvfrom(512)
        info = ujson.loads(data.decode())

        ip = (info.get("ip") or "").strip()
        port = int(info.get("port", PORT))

        if ip:
            print("✅ MQTT Broker discovery 성공:", ip, port, "from", addr)
            return ip

    except Exception as exc:
        print("⚠️ MQTT Broker discovery 실패:", exc)

    try:
        if s is not None:
            s.close()
    except Exception:
        pass

    return None

def resolve_broker_from_config(config=None):
    """
    Broker 결정 순서:
    1. wifi_config.json의 broker
    2. UDP Discovery
    3. DEFAULT_BROKER_IP, 현재는 빈 값
    """
    broker = ""

    try:
        if config:
            broker = (config.get("broker") or "").strip()
    except Exception:
        broker = ""

    if broker:
        print("📌 config broker 사용:", broker)
        return broker

    discovered = discover_mqtt_broker()
    if discovered:
        return discovered

    if DEFAULT_BROKER_IP:
        print("📌 DEFAULT_BROKER_IP 사용:", DEFAULT_BROKER_IP)
        return DEFAULT_BROKER_IP

    print("⚠️ MQTT Broker를 찾지 못했습니다.")
    return ""

def ensure_mqtt_broker():
    global MQTT_BROKER

    if MQTT_BROKER:
        return True

    MQTT_BROKER = resolve_broker_from_config(load_wifi_config())
    return bool(MQTT_BROKER)


def rediscover_mqtt_broker():
    """
    저장된 broker가 오래되었거나 MQTT 연결에 실패했을 때,
    config broker를 다시 고집하지 않고 UDP Discovery로 새 Broker를 찾는다.
    """
    global MQTT_BROKER

    discovered = discover_mqtt_broker()
    if discovered:
        MQTT_BROKER = discovered
        print("🔎 MQTT Broker 재검색 성공:", MQTT_BROKER)
        return True

    MQTT_BROKER = ""
    print("⚠️ MQTT Broker 재검색 실패")
    return False

def connect_wifi_from_config(force_reset=True):
    global MQTT_BROKER

    config = load_wifi_config()
    if config:
        ssid = config.get("ssid")
        password = config.get("password")
        if ssid and password and try_connect_wifi(ssid, password, force_reset):
            MQTT_BROKER = resolve_broker_from_config(config)
            if MQTT_BROKER:
                print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
            else:
                print("⚠️ Wi-Fi 연결은 성공했지만 MQTT Broker를 찾지 못했습니다.")
            return True

    if WIFI_SSID and WIFI_PASSWORD:
        if try_connect_wifi(WIFI_SSID, WIFI_PASSWORD, force_reset):
            MQTT_BROKER = resolve_broker_from_config(None)
            if MQTT_BROKER:
                print("🌐 기본 설정으로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
            else:
                print("⚠️ 기본 Wi-Fi 연결은 성공했지만 MQTT Broker를 찾지 못했습니다.")
            return True

    return False

def wifi_ensure():
    if wifi_is_connected():
        return True
    return connect_wifi_from_config(True)

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
    if connect_wifi_from_config(True):
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
    except Exception:
        pass

def safe_publish(topic, payload, retain=False):
    global publish_success_count, publish_fail_count
    if client is None:
        return False
    try:
        if isinstance(payload, dict):
            payload = ujson.dumps(payload)
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
    }
    safe_publish(TOPIC_STATUS, payload, retain=True)

def publish_hello():
    payload = {
        "id": DEVICE_ID,
        "ip": wlan.ifconfig()[0] if wifi_is_connected() else "",
        "name": DEVICE_ID,
        "type": "subscriber",
        "ts": now_ts(),
    }
    safe_publish(TOPIC_HELLO, payload, retain=True)
    log("info", "hello published", ip=payload["ip"])

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

def mqtt_callback(topic, message):
    led_blink(1, 40, 40)
    note_mqtt_ok()
    try:
        raw = message.decode() if isinstance(message, (bytes, bytearray)) else str(message)
        payload = ujson.loads(raw) if raw and raw[0] in "[{" else {}
    except Exception as exc:
        print("JSON parse error:", exc, message)
        return

    command = str(payload.get("command", "")).strip()
    if command == "vibrate_fire_alert":
        start_fire_alert(
            int(payload.get("duration_ms", 10000)),
            int(payload.get("on_ms", 400)),
            int(payload.get("off_ms", 200)),
            float(payload.get("intensity", 0.85)),
        )
    elif command == "vibrate_once":
        duration = int(payload.get("ms", 800))
        intensity = float(payload.get("intensity", 0.8))
        start_fire_alert(duration, duration, 9999999, intensity)
    elif command == "vibrate_stop":
        stop_pattern()
    else:
        print("[VIB] unknown cmd:", command, payload)

def _mqtt_connect_once():
    global client, publish_success_count, publish_fail_count

    close_mqtt_client()

    mqtt = MQTTClient(CLIENT_ID, MQTT_BROKER, port=PORT, keepalive=KEEPALIVE)
    mqtt.set_callback(mqtt_callback)
    mqtt.connect()

    client = mqtt
    apply_socket_timeout()

    mqtt.subscribe(TOPIC_CMD_THIS, qos=1)
    mqtt.subscribe(TOPIC_CMD_ALL, qos=1)
    mqtt.subscribe(TOPIC_REQ.encode(), qos=1)

    publish_success_count = 0
    publish_fail_count = 0

    publish_status(True, "mqtt_connected")
    publish_hello()
    note_mqtt_ok()

    return mqtt


def mqtt_connect_and_sub():
    global MQTT_BROKER

    if not ensure_mqtt_broker():
        print("❌ MQTT 연결 실패: Broker IP 없음")
        close_mqtt_client()
        return None

    # 1차: config 또는 기존 discovery로 찾은 Broker에 연결
    # 2차: 실패 시 UDP Discovery를 다시 수행해 새 Broker로 재시도
    for attempt in range(2):
        try:
            print("📡 MQTT 연결 시도:", MQTT_BROKER, "try=", attempt + 1)
            return _mqtt_connect_once()

        except Exception as exc:
            print("MQTT connect err:", exc)
            close_mqtt_client()

            if attempt == 0:
                if rediscover_mqtt_broker():
                    continue

            MQTT_BROKER = ""
            return None

    return None

def mqtt_reconnect_with_backoff():
    backoff = 0.5
    for _ in range(MQTT_RECONNECT_MAX):
        feed_wdt()
        close_mqtt_client()

        if not wifi_ensure():
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            continue

        try:
            mqtt = mqtt_connect_and_sub()
            if mqtt is not None:
                return True
        except Exception as exc:
            print("MQTT reconnect err:", exc)
            close_mqtt_client()

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
    led_blink(4, 120, 120)
    close_mqtt_client()
    stop_pattern()
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
# Main loop
# =========================================================
def main():
    global recovery_fail_count

    stop_all()
    led_off()
    led_blink(2, 100, 100)
    pre_boot_stabilize()
    startup_wifi_or_portal()

    while True:
        try:
            if not wifi_ensure():
                recovery_fail_count += 1
                if recovery_fail_count >= MAX_RECOVERY_FAILS:
                    hard_recover("vibrator wifi disconnected")
                time.sleep_ms(RECONNECT_DELAY_MS)
                continue

            if client is None:
                print("📡 MQTT 연결 시도:", MQTT_BROKER)
                mqtt = mqtt_connect_and_sub()

                if mqtt is None:
                    print("❌ MQTT 초기 연결 실패 → 재시도")
                    close_mqtt_client()
                    time.sleep_ms(RECONNECT_DELAY_MS)
                    continue

                print("✅ MQTT 연결 완료. broker =", MQTT_BROKER)
                led_blink(5, 80, 80)
                recovery_fail_count = 0
                last_ping_ms = ticks_ms()
                last_status_ms = ticks_ms()
                last_gc_ms = ticks_ms()
                break
        except Exception as exc:
            print("초기 연결 err:", exc)
            close_mqtt_client()
            time.sleep_ms(RECONNECT_DELAY_MS)

    start_watchdog()

    while True:
        try:
            feed_wdt()
            last_gc_ms = maybe_gc(last_gc_ms)
            now_ms = ticks_ms()
            check_mqtt_inactivity(now_ms)

            if not wifi_ensure():
                close_mqtt_client()

            if client is None:
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("vibrator mqtt disconnected")
                    time.sleep_ms(RECONNECT_DELAY_MS)
                    continue
                recovery_fail_count = 0
                last_ping_ms = ticks_ms()
                last_status_ms = ticks_ms()

            try:
                client.check_msg()
                note_mqtt_ok()
            except Exception as exc:
                print("check_msg err:", exc)
                close_mqtt_client()

            pattern_tick()

            if time.ticks_diff(now_ms, last_ping_ms) >= PING_INTERVAL_MS:
                try:
                    client.ping()
                    note_mqtt_ok()
                    last_ping_ms = now_ms
                except Exception:
                    close_mqtt_client()

            if time.ticks_diff(now_ms, last_status_ms) >= STATUS_HEARTBEAT_MS:
                publish_status(True, "heartbeat")
                publish_hello()
                last_status_ms = now_ms

            if publish_fail_count >= PUBLISH_FAIL_MAX:
                hard_recover("vibrator publish failures")

            maybe_rotate_mqtt()
            recovery_fail_count = 0
            time.sleep_ms(MAIN_LOOP_SLEEP_MS)
        except Exception as exc:
            print("MQTT loop err:", exc)
            close_mqtt_client()
            stop_pattern()
            led_blink(1, 200, 200)
            recovery_fail_count += 1
            if recovery_fail_count >= MAX_RECOVERY_FAILS:
                hard_recover("vibrator mqtt loop stuck")
            time.sleep_ms(RECONNECT_DELAY_MS)

try:
    main()
except KeyboardInterrupt:
    stop_all()
    led_off()
