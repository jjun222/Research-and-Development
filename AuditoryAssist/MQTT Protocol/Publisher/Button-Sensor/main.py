import gc
import machine
import network
import os
import socket
import time
import ujson

try:
    from simple import MQTTClient
except Exception:
    from umqtt.simple import MQTTClient

# =========================================================
# Doorbell Button Sensor - Product Final
#
# Hardware:
#   GPIO1 ---- Button ---- GND
#   Internal PULL_UP is used.
#
# Normal state:
#   - doorbell/sensor is NOT published.
#   - status heartbeat is published every 60 seconds.
#   - MQTT ping check is performed every 30 seconds.
#   - MQTT connection is refreshed every 5 minutes to avoid stale socket.
#
# Button pressed:
#   - polling debounce confirms the press.
#   - doorbell/sensor publishes button_pressed.
#   - priority_class remains routine.
#
# Important:
#   - No publish-silence reset.
#   - No IRQ.
#   - Button press does not intentionally start MQTT reconnect.
#   - If publish fails, event is queued and automatic reconnect loop handles recovery.
# =========================================================

# =========================
# Hardware
# =========================
BUTTON_PIN = 1

# Pull-up input: not pressed = 1, pressed = 0
button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

# =========================
# Button polling
# =========================
BUTTON_POLL_MS = 20
BUTTON_STABLE_MS = 80
BUTTON_EVENT_GAP_MS = 500
MAX_PENDING_BUTTON_EVENTS = 5

# =========================
# Wi-Fi / MQTT
# =========================
WIFI_SSID = ""
WIFI_PASSWORD = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = ""
DISCOVERY_PORT = 30303
DISCOVERY_TIMEOUT_MS = 2000

MQTT_BROKER = ""
MQTT_TOPIC = "doorbell/sensor"
MQTT_CLIENT_ID = "doorbell_1"
STATUS_TOPIC = "interfaceui/status/publisher/" + MQTT_CLIENT_ID

KEEPALIVE_SEC = 60
PING_INTERVAL_MS = 30_000
STATUS_HEARTBEAT_MS = 60_000
MQTT_FORCE_RECONNECT_MS = 300_000  # 5 minutes

GC_INTERVAL_MS = 20_000

# 30 checks * 0.5 sec = about 15 seconds before AP mode
WIFI_RETRY_MAX = 300
WIFI_RETRY_WAIT_MS = 500

MQTT_RECONNECT_MAX = 10
MAX_RECOVERY_FAILS = 5

WDT_TIMEOUT_MS = 8_000
SOCKET_TIMEOUT_SEC = 3

# Cold boot stabilization for UPS / power-only boot
COLD_BOOT_SETTLE_MS = 3_000
SOFT_BOOT_SETTLE_MS = 3_000
WIFI_POST_RESET_WAIT_MS = 1_500
POST_WIFI_CONNECT_SETTLE_MS = 1_000

DEBUG_PUBLISH = True
PUBLISH_FAIL_RESET_THRESHOLD = 5

AP_SSID = "doorbell_setup"
AP_PW = "123456789"

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
  <small>비워두면 같은 네트워크에서 MQTT Broker를 자동 검색합니다.</small><br>
<button type="submit">저장</button>
</form>
</body></html>
""" % DEFAULT_BROKER_IP

HTML_SAVED = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
"""

# =========================
# Global state
# =========================
wlan = None
client = None
wdt = None

recovery_fail_count = 0
publish_success_count = 0
consecutive_publish_failures = 0
pending_button_events = 0

BOOT_TICKS_MS = time.ticks_ms()


# =========================================================
# Basic helpers
# =========================================================
def now_str():
    return "%04d-%02d-%02d %02d:%02d:%02d" % time.localtime()[:6]


def uptime_ms():
    return time.ticks_diff(time.ticks_ms(), BOOT_TICKS_MS)


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
    last_log_sec = -1

    while time.ticks_diff(end_ms, time.ticks_ms()) > 0:
        remain_ms = time.ticks_diff(end_ms, time.ticks_ms())
        remain_sec = max(0, remain_ms // 1000)
        if remain_sec != last_log_sec:
            print("⏳ 부팅 안정화 중... 남은 시간:", remain_sec, "초")
            last_log_sec = remain_sec
        time.sleep_ms(250)


def set_led(v):
    if onboard_led is not None:
        onboard_led.value(1 if v else 0)


def blink_once(on_ms=80, off_ms=80):
    set_led(True)
    time.sleep_ms(on_ms)
    set_led(False)
    time.sleep_ms(off_ms)


def blink_n(n, on_ms=80, off_ms=80):
    for _ in range(n):
        blink_once(on_ms, off_ms)


def start_watchdog():
    global wdt
    try:
        wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
        print("🛡️ WDT 시작:", WDT_TIMEOUT_MS, "ms")
    except Exception as e:
        wdt = None
        print("⚠️ WDT 시작 실패:", e)


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


def hard_recover(reason="unknown"):
    print("♻️ 하드 복구 실행:", reason)
    blink_n(4, 120, 120)
    close_mqtt_client()
    reset_wifi_interface()
    time.sleep(2)
    machine.reset()


def record_publish_success():
    global consecutive_publish_failures
    consecutive_publish_failures = 0


def record_publish_failure():
    global consecutive_publish_failures
    consecutive_publish_failures += 1
    print("⚠️ publish 실패 누적:", consecutive_publish_failures)

    # Doorbell is an event-based device.
    # Do not reboot only because a publish failed.
    # Close MQTT socket and let the normal reconnect loop recover.
    if consecutive_publish_failures >= PUBLISH_FAIL_RESET_THRESHOLD:
        print("⚠️ publish 실패 누적 초과 → MQTT 연결만 초기화")
        close_mqtt_client()
        consecutive_publish_failures = 0


# =========================================================
# Wi-Fi config / AP portal
# =========================================================
def load_wifi_config():
    if CONFIG_PATH not in os.listdir():
        return None
    try:
        with open(CONFIG_PATH, "r") as f:
            return ujson.loads(f.read())
    except Exception as e:
        print("⚠️ config load 실패:", e)
        return None


def save_wifi_config(ssid, pw, broker_ip=None):
    cfg = {"ssid": ssid, "password": pw}
    if broker_ip:
        cfg["broker"] = broker_ip
    with open(CONFIG_PATH, "w") as f:
        f.write(ujson.dumps(cfg))
    print("✅ Wi-Fi 설정 저장 완료:", cfg)


def url_decode(s):
    out = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == "+":
            out += " "
        elif c == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i + 1:i + 3], 16))
                i += 2
            except Exception:
                out += c
        else:
            out += c
        i += 1
    return out


def parse_form(body):
    out = {}
    for p in body.split("&"):
        if "=" in p:
            k, v = p.split("=", 1)
            out[k] = url_decode(v)
    return out


def reset_wifi_interface():
    global wlan
    try:
        if wlan is None:
            wlan = network.WLAN(network.STA_IF)
        wlan.active(False)
        time.sleep_ms(300)
        wlan.active(True)
        time.sleep_ms(WIFI_POST_RESET_WAIT_MS)
    except Exception as e:
        print("⚠️ Wi-Fi 인터페이스 리셋 실패:", e)


def try_connect_wifi(ssid, pw):
    global wlan
    if not ssid or not pw:
        return False

    network.WLAN(network.AP_IF).active(False)

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    try:
        wlan.disconnect()
    except Exception:
        pass

    reset_wifi_interface()
    print("📡 Wi-Fi 연결 시도:", ssid)

    try:
        wlan.connect(ssid, pw)
    except Exception as e:
        print("❌ wlan.connect 실패:", e)
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




def discover_mqtt_broker(timeout_ms=DISCOVERY_TIMEOUT_MS):
    """
    UDP Discovery로 MQTT Broker IP를 찾는다.
    판단서버가 UDP 30303에서 MQTT_DISCOVER 요청에 응답해야 한다.

    실패해도 예외를 밖으로 던지지 않고 None을 반환한다.
    학교 Wi-Fi에서 UDP broadcast가 막힐 수 있으므로,
    실패 시 AP 설정 모드에서 Broker IP 수동 입력을 사용할 수 있다.
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout_ms / 1000)

        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass

        s.sendto(b"MQTT_DISCOVER", ("255.255.255.255", DISCOVERY_PORT))
        data, addr = s.recvfrom(512)

        try:
            s.close()
        except Exception:
            pass

        info = ujson.loads(data.decode())
        ip = info.get("ip") or ""
        port = int(info.get("port", 1883))

        if ip:
            print("✅ MQTT Broker discovery 성공:", ip, port, "from", addr)
            return ip

    except Exception as e:
        print("⚠️ MQTT Broker discovery 실패:", e)

    try:
        if s is not None:
            s.close()
    except Exception:
        pass

    return None


def resolve_broker_from_config(cfg=None):
    """
    Broker 결정 순서:
    1. wifi_config.json의 broker
    2. UDP Discovery
    3. DEFAULT_BROKER_IP, 현재는 빈 값
    """
    broker = ""

    try:
        if cfg:
            broker = (cfg.get("broker") or "").strip()
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
    """
    MQTT 연결 직전에 Broker가 비어 있으면 config/discovery를 다시 수행한다.
    """
    global MQTT_BROKER

    if MQTT_BROKER:
        return True

    MQTT_BROKER = resolve_broker_from_config(load_wifi_config())
    return bool(MQTT_BROKER)


def rediscover_mqtt_broker():
    """
    저장된 broker가 오래된 값일 수 있을 때 config broker를 무시하고 discovery를 재시도한다.
    """
    global MQTT_BROKER

    discovered = discover_mqtt_broker()
    if discovered:
        MQTT_BROKER = discovered
        return True

    return False

def connect_wifi_from_config():
    global MQTT_BROKER

    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid")
        pw = cfg.get("password")
        if ssid and pw and try_connect_wifi(ssid, pw):
            MQTT_BROKER = resolve_broker_from_config(cfg)

            if MQTT_BROKER:
                print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
            else:
                print("⚠️ Wi-Fi 연결은 성공했지만 MQTT Broker를 아직 찾지 못했습니다.")
                print("   → MQTT 연결 루프에서 discovery를 계속 재시도합니다.")

            # 기존 기능 보존:
            # Wi-Fi가 연결되면 AP 모드로 강제 진입하지 않고,
            # 이후 MQTT 연결 루프가 Broker 검색/재연결을 계속 수행한다.
            return True

    if WIFI_SSID and WIFI_PASSWORD and try_connect_wifi(WIFI_SSID, WIFI_PASSWORD):
        MQTT_BROKER = resolve_broker_from_config(None)

        if MQTT_BROKER:
            print("🌐 기본 설정으로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
        else:
            print("⚠️ 기본 Wi-Fi 연결은 성공했지만 MQTT Broker를 아직 찾지 못했습니다.")
            print("   → MQTT 연결 루프에서 discovery를 계속 재시도합니다.")

        return True

    return False


def wifi_ensure():
    if wlan is None or (not wlan.isconnected()):
        print("⚠️ Wi-Fi 미연결 감지 → 재연결")
        return connect_wifi_from_config()
    return True


def get_ip():
    try:
        if wlan is not None and wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return ""


def start_config_portal():
    network.WLAN(network.STA_IF).active(False)
    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PW)
    ap.active(True)
    print("📶 AP 모드 시작:", ap.ifconfig())

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    s.bind(addr)
    s.listen(1)

    while True:
        cl, _ = s.accept()
        req = cl.recv(1024)
        try:
            req_str = req.decode()
        except Exception:
            req_str = ""

        if "POST /save" in req_str:
            body = req_str.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in req_str else ""
            form = parse_form(body)
            ssid = form.get("ssid", "").strip()
            pw = form.get("pw", "").strip()
            broker = form.get("broker", "").strip()

            if ssid and pw:
                save_wifi_config(ssid, pw, broker or None)
                cl.send(HTML_SAVED.encode())
                cl.close()
                time.sleep(3)
                machine.reset()
            else:
                cl.send(HTML_FORM.encode())
                cl.close()
        else:
            cl.send(HTML_FORM.encode())
            cl.close()


def startup_wifi_or_portal():
    if connect_wifi_from_config():
        return True
    print("⚠️ Wi-Fi 접속 실패 → AP 모드 진입")
    start_config_portal()
    return False


# =========================================================
# MQTT connection
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
    except Exception as e:
        print("⚠️ socket timeout 설정 실패:", e)


def mqtt_connect():
    global client, publish_success_count, MQTT_BROKER

    if not ensure_mqtt_broker():
        print("❌ MQTT 연결 실패: Broker IP 없음")
        close_mqtt_client()
        return False

    for attempt in range(2):
        try:
            close_mqtt_client()
            gc.collect()

            client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
            client.connect()
            apply_socket_timeout()

            publish_success_count = 0
            print("✅ MQTT 연결 완료 (broker =", MQTT_BROKER, ")")
            blink_n(5)

            publish_device_status(state="online", value=None, reason="mqtt_connected")
            return True

        except Exception as e:
            print("❌ MQTT 연결 실패:", e)
            close_mqtt_client()

            # 저장된 broker가 오래된 값이거나 IP가 바뀐 경우를 대비해
            # config broker를 무시하고 discovery로 한 번 더 보정한다.
            old_broker = MQTT_BROKER

            if rediscover_mqtt_broker() and MQTT_BROKER != old_broker:
                print("🔎 새 MQTT Broker 발견 → 재시도:", MQTT_BROKER)
                continue

            # 다음 재시도에서 config/discovery를 다시 수행하도록 비운다.
            MQTT_BROKER = ""
            return False

    return False


def mqtt_ping():
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception as e:
        print("⚠️ ping 실패:", e)
        return False


def mqtt_reconnect_with_backoff():
    backoff = 0.5

    for attempt in range(MQTT_RECONNECT_MAX):
        feed_wdt()
        print("🔁 MQTT 재연결 시도", attempt + 1, "(free mem =", gc.mem_free(), ")")

        close_mqtt_client()
        gc.collect()
        reset_wifi_interface()

        if not wifi_ensure():
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            continue

        if mqtt_connect():
            print("✅ MQTT 재연결 성공")
            return True

        gc.collect()
        time.sleep(backoff)
        backoff = min(backoff * 2, 5)

    print("🚫 MQTT 재연결 포기")
    return False


def maintain_mqtt_connection(now, force_reconnect=False):
    global recovery_fail_count

    if force_reconnect:
        print("♻️ 5분 주기 MQTT 재연결")
        ok = mqtt_reconnect_with_backoff()
    elif wlan is None or (not wlan.isconnected()) or client is None:
        ok = mqtt_reconnect_with_backoff()
    else:
        ok = True

    if ok:
        recovery_fail_count = 0
        return True

    recovery_fail_count += 1
    print("⚠️ 복구 실패 누적:", recovery_fail_count)

    if recovery_fail_count >= MAX_RECOVERY_FAILS:
        hard_recover("doorbell wifi/mqtt stuck")

    return False


# =========================================================
# MQTT publish
# =========================================================
def publish_json(topic, obj):
    global publish_success_count

    # Do not reconnect inside publish_json.
    # Reconnect is handled by the normal maintenance loop.
    if wlan is None or (not wlan.isconnected()) or client is None:
        print("⚠️ publish 불가: MQTT/Wi-Fi 미연결")
        record_publish_failure()
        return False

    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    try:
        gc.collect()

        if DEBUG_PUBLISH:
            print("➡️ publish start:", topic, "len=", len(msg), "free=", gc.mem_free())

        client.publish(topic, msg)

        if DEBUG_PUBLISH:
            print("✅ publish done :", topic)

        publish_success_count += 1
        record_publish_success()
        return True

    except Exception as e:
        print("❗ publish 실패:", e, "(free=", gc.mem_free(), ")")
        close_mqtt_client()
        gc.collect()
        record_publish_failure()
        return False


# =========================================================
# Payload
# =========================================================
def publish_device_status(state="normal", value=None, reason="heartbeat"):
    payload = {
        "id": MQTT_CLIENT_ID,
        "type": "publisher",
        "device_type": "button_sensor",
        "online": True,
        "wifi": (wlan is not None and wlan.isconnected()),
        "mqtt": (client is not None),
        "ip": get_ip(),
        "state": state,
        "value": value,
        "reason": reason,
        "ts": now_str(),
        "publisher_uptime_ms": int(uptime_ms()),
        "publisher_reset_cause": reset_cause_name(),
    }
    return publish_json(STATUS_TOPIC, payload)


def send_button_event():
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "button_pressed",
        "status": "pressed",
        "value": 1,
        "timestamp": now_str(),
        "publisher_uptime_ms": int(uptime_ms()),
        "publisher_reset_cause": reset_cause_name(),
        "priority_class": "routine",
    }

    print("📤 버튼 이벤트 전송")
    blink_once(40, 40)
    return publish_json(MQTT_TOPIC, payload)


# =========================================================
# Button event queue
# =========================================================
def queue_button_event():
    global pending_button_events
    if pending_button_events < MAX_PENDING_BUTTON_EVENTS:
        pending_button_events += 1
    print("📌 대기 중인 버튼 이벤트:", pending_button_events)


def flush_pending_button_events():
    global pending_button_events

    if pending_button_events <= 0:
        return

    if wlan is None or (not wlan.isconnected()) or client is None:
        return

    while pending_button_events > 0:
        ok = send_button_event()
        if not ok:
            close_mqtt_client()
            return

        pending_button_events -= 1
        feed_wdt()
        time.sleep_ms(120)
        publish_device_status(state="triggered", value=1, reason="pending_event")


# =========================================================
# Button polling
# =========================================================
def read_button_raw():
    return button.value()


def update_button_state(state, now):
    raw = read_button_raw()

    if raw != state["last_raw"]:
        state["last_raw"] = raw
        state["last_change_ms"] = now

    if time.ticks_diff(now, state["last_change_ms"]) >= BUTTON_STABLE_MS:
        if raw != state["stable_raw"]:
            state["stable_raw"] = raw

            if raw == 0 and not state["pressed_latched"]:
                if time.ticks_diff(now, state["last_event_ms"]) >= BUTTON_EVENT_GAP_MS:
                    state["pressed_latched"] = True
                    state["last_event_ms"] = now
                    return True

            elif raw == 1:
                state["pressed_latched"] = False

    return False


def handle_button_press():
    print("🔔 버튼 눌림 확정!")

    if wlan is not None and wlan.isconnected() and client is not None:
        ok_event = send_button_event()

        feed_wdt()
        time.sleep_ms(120)
        feed_wdt()

        ok_status = publish_device_status(state="triggered", value=1, reason="event")

        if not ok_event:
            print("⚠️ 버튼 이벤트 publish 실패 → 이벤트 대기열 저장")
            queue_button_event()

        if not ok_status:
            print("⚠️ 버튼 status publish 실패 → 다음 heartbeat에서 재시도")
            close_mqtt_client()
    else:
        print("⚠️ 버튼 눌림 시점에 MQTT 미연결 → 이벤트 대기열 저장")
        queue_button_event()

    set_led(True)
    time.sleep_ms(120)
    set_led(False)


# =========================================================
# Main
# =========================================================
def main():
    print("====================================")
    print("🚀 BOOT START")
    print("🧾 reset cause =", reset_cause_name())
    print("🧠 free mem before gc =", gc.mem_free())
    gc.collect()
    print("🧠 free mem after  gc =", gc.mem_free())
    print("====================================")

    set_led(False)
    blink_n(2)
    pre_boot_stabilize()

    startup_wifi_or_portal()
    while not mqtt_connect():
        time.sleep(5)

    start_watchdog()
    print("🔔 버튼 대기 중... polling mode")

    t_ping = time.ticks_ms()
    t_status = time.ticks_ms()
    t_gc = time.ticks_ms()
    t_led = time.ticks_ms()
    t_mqtt_rotate = time.ticks_ms()

    led_on = False

    button_state = {
        "last_raw": read_button_raw(),
        "stable_raw": read_button_raw(),
        "last_change_ms": time.ticks_ms(),
        "pressed_latched": False,
        "last_event_ms": time.ticks_ms() - BUTTON_EVENT_GAP_MS,
    }

    while True:
        feed_wdt()
        now = time.ticks_ms()
        t_gc = maybe_gc(t_gc)

        if time.ticks_diff(now, t_led) >= 1000:
            led_on = not led_on
            set_led(led_on)
            t_led = now

        # 1) Always keep the connection alive independently from button press.
        if wlan is None or (not wlan.isconnected()) or client is None:
            maintain_mqtt_connection(now)

        # 2) MQTT ping every 30 seconds.
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            print("🔎 keepalive check")

            if not wifi_ensure():
                close_mqtt_client()
                maintain_mqtt_connection(now)

            elif not mqtt_ping():
                close_mqtt_client()
                maintain_mqtt_connection(now)

            t_ping = now

        # 3) MQTT forced reconnect every 5 minutes to avoid stale socket.
        if time.ticks_diff(now, t_mqtt_rotate) >= MQTT_FORCE_RECONNECT_MS:
            maintain_mqtt_connection(now, force_reconnect=True)
            t_mqtt_rotate = time.ticks_ms()

        # 4) Status heartbeat every 60 seconds.
        if time.ticks_diff(now, t_status) >= STATUS_HEARTBEAT_MS:
            ok_status = publish_device_status(state="normal", value=None, reason="heartbeat")
            if not ok_status:
                print("⚠️ heartbeat status publish 실패 → MQTT 연결 초기화")
                close_mqtt_client()
            t_status = now

        # 5) If a previous button event failed, send it after auto recovery.
        flush_pending_button_events()

        # 6) Button polling.
        if update_button_state(button_state, now):
            handle_button_press()

        time.sleep_ms(BUTTON_POLL_MS)


main()
