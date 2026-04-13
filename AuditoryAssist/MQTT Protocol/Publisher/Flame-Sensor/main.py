import gc
import machine
import network
import os
import socket
import time
import ujson
from simple import MQTTClient

# =========================
# SHZ flame sensor (product final)
# - 불꽃 감지 발생 시에만 shz/sensor 이벤트 발행
# - 불꽃 해제 시에는 status topic만 갱신
# - heartbeat도 status topic만 사용
# - 재감지 가능
# - WDT / reconnect / AP provisioning / publish 보호 유지
# =========================

FIRE_SENSOR_PIN = 15
BOOT_SETTLE_MS = 2000

WIFI_SSID = ""
WIFI_PASSWORD = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

MQTT_BROKER = DEFAULT_BROKER_IP
MQTT_TOPIC = "shz/sensor"
MQTT_CLIENT_ID = "shz_sensor_pico"
STATUS_TOPIC = "interfaceui/status/publisher/" + MQTT_CLIENT_ID

KEEPALIVE_SEC = 60
PING_INTERVAL_MS = 30000
STATUS_HEARTBEAT_MS = 60000
PUBLISH_GAP_MS = 120

WIFI_RETRY_MAX = 15
MQTT_RECONNECT_MAX = 10
MAX_RECOVERY_FAILS = 5

SAMPLE_INTERVAL_MS = 20
DETECT_STABLE_COUNT = 5
CLEAR_STABLE_COUNT = 5
MIN_EVENT_GAP_MS = 1000
LED_BLINK_MS = 500

GC_INTERVAL_MS = 20000
WDT_TIMEOUT_MS = 8000
SOCKET_TIMEOUT_SEC = 3
DEBUG_PUBLISH = True

PUBLISH_FAIL_RESET_THRESHOLD = 5
FORCE_ROTATE_AFTER_PUBLISHES = 120

AP_SSID = "shz_sensor_setup"
AP_PW = "123456789"

HTML_FORM = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>WiFi 설정</title></head><body>
<h2>Wi-Fi / MQTT 설정</h2>
<form method='POST' action='/save'>
SSID: <input name='ssid'><br>
PW: <input name='pw' type='password'><br>
Broker IP: <input name='broker' value='%s'><br>
<button type='submit'>저장</button>
</form></body></html>
""" % DEFAULT_BROKER_IP

HTML_SAVED = """HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
"""

fire_sensor = machine.Pin(FIRE_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)
try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

wlan = None
client = None
wdt = None

recovery_fail_count = 0
publish_success_count = 0
consecutive_publish_failures = 0
BOOT_TICKS_MS = time.ticks_ms()


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


def set_led(v):
    value = 1 if v else 0
    led.value(value)
    if onboard_led is not None:
        onboard_led.value(value)


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


def short_gap(ms=PUBLISH_GAP_MS):
    feed_wdt()
    gc.collect()
    time.sleep_ms(ms)
    feed_wdt()


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
    if consecutive_publish_failures >= PUBLISH_FAIL_RESET_THRESHOLD:
        hard_recover("shz publish failures")


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
        time.sleep_ms(300)
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
    wlan.connect(ssid, pw)

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
        feed_wdt()
        attempt += 1
        time.sleep(0.5)

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패")
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    blink_n(3)
    return True


def connect_wifi_from_config():
    global MQTT_BROKER

    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid")
        pw = cfg.get("password")
        if ssid and pw and try_connect_wifi(ssid, pw):
            MQTT_BROKER = cfg.get("broker") or DEFAULT_BROKER_IP
            print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
            return True

    if WIFI_SSID and WIFI_PASSWORD and try_connect_wifi(WIFI_SSID, WIFI_PASSWORD):
        MQTT_BROKER = DEFAULT_BROKER_IP
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
    start_config_portal()
    return False


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
    try:
        if client is not None and hasattr(client, "sock") and client.sock is not None:
            client.sock.settimeout(SOCKET_TIMEOUT_SEC)
            print("⏱️ MQTT socket timeout =", SOCKET_TIMEOUT_SEC, "sec")
    except Exception as e:
        print("⚠️ socket timeout 설정 실패:", e)


def mqtt_connect():
    global client, publish_success_count
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
        return False


def mqtt_ping():
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception:
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

        try:
            if mqtt_connect():
                print("✅ MQTT 재연결 성공")
                return True
        except Exception as e:
            print("❌ MQTT 재연결 중 예외:", e)

        gc.collect()
        time.sleep(backoff)
        backoff = min(backoff * 2, 5)
    return False


def maybe_rotate_mqtt():
    global publish_success_count
    if publish_success_count >= FORCE_ROTATE_AFTER_PUBLISHES:
        print("♻️ 정기 MQTT 회전 실행 (publish count =", publish_success_count, ")")
        if mqtt_reconnect_with_backoff():
            publish_success_count = 0


def publish_json(topic, obj):
    global publish_success_count
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5
    for attempt in range(4):
        feed_wdt()

        if client is None:
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue

        try:
            gc.collect()
            if DEBUG_PUBLISH:
                print("➡️ publish start:", topic, "try=", attempt + 1, "len=", len(msg), "free=", gc.mem_free())
            client.publish(topic, msg)
            if DEBUG_PUBLISH:
                print("✅ publish done :", topic)
            publish_success_count += 1
            maybe_rotate_mqtt()
            record_publish_success()
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e, "(free=", gc.mem_free(), ")")
            close_mqtt_client()
            gc.collect()
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)

    record_publish_failure()
    return False


def publish_device_status(state="normal", value=None, reason="heartbeat"):
    payload = {
        "id": MQTT_CLIENT_ID,
        "type": "publisher",
        "device_type": "flame_sensor",
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


def send_detect_event():
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "shz_detected",
        "status": "불꽃 감지!",
        "value": 1,
        "raw_active_low": 0,
        "reason": "state_change",
        "timestamp": now_str(),
        "publisher_uptime_ms": int(uptime_ms()),
        "publisher_reset_cause": reset_cause_name(),
        "priority_class": "emergency",
    }
    return publish_json(MQTT_TOPIC, payload)


def read_sensor_active():
    return fire_sensor.value() == 0


def main():
    global recovery_fail_count

    print("====================================")
    print("🚀 BOOT START")
    print("🧾 reset cause =", reset_cause_name())
    print("🧠 free mem before gc =", gc.mem_free())
    gc.collect()
    print("🧠 free mem after  gc =", gc.mem_free())
    print("====================================")

    set_led(False)
    blink_n(2)
    time.sleep_ms(BOOT_SETTLE_MS)

    startup_wifi_or_portal()
    while not mqtt_connect():
        time.sleep(5)

    start_watchdog()
    print("🔥 SHZ 센서 모니터링 시작")

    t_ping = time.ticks_ms()
    t_last_sample = time.ticks_ms()
    t_last_transition = time.ticks_ms()
    t_last_status = time.ticks_ms()
    t_last_gc = time.ticks_ms()
    t_last_led = time.ticks_ms()

    detect_count = 0
    clear_count = 0
    current_detected = False

    while True:
        feed_wdt()
        now = time.ticks_ms()
        t_last_gc = maybe_gc(t_last_gc)

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            print("🔎 keepalive check")
            ok_wifi = wifi_ensure()
            ok_mqtt = mqtt_ping() if ok_wifi else False
            if (not ok_wifi) or (not ok_mqtt):
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("shz wifi/mqtt stuck")
                else:
                    recovery_fail_count = 0
            else:
                recovery_fail_count = 0
            t_ping = now

        if time.ticks_diff(now, t_last_sample) < SAMPLE_INTERVAL_MS:
            time.sleep_ms(5)
            continue
        t_last_sample = now

        active = read_sensor_active()
        if active:
            detect_count += 1
            clear_count = 0
        else:
            clear_count += 1
            detect_count = 0

        if (not current_detected) and detect_count >= DETECT_STABLE_COUNT and time.ticks_diff(now, t_last_transition) >= MIN_EVENT_GAP_MS:
            current_detected = True
            t_last_transition = now
            print("🔥 불꽃 감지")

            ok1 = send_detect_event()
            if not ok1:
                print("⚠️ SHZ 감지 event publish 실패")

            short_gap()

            ok2 = publish_device_status(state="alarm", value=1, reason="state_change")
            if not ok2:
                print("⚠️ SHZ 상태 publish 실패")

        elif current_detected and clear_count >= CLEAR_STABLE_COUNT and time.ticks_diff(now, t_last_transition) >= MIN_EVENT_GAP_MS:
            current_detected = False
            t_last_transition = now
            print("✅ 불꽃 해제")

            # 해제 시에는 이벤트 토픽(shz/sensor)으로 보내지 않음
            ok2 = publish_device_status(state="normal", value=0, reason="state_change")
            if not ok2:
                print("⚠️ SHZ 상태 publish 실패")

        if time.ticks_diff(now, t_last_status) >= STATUS_HEARTBEAT_MS:
            publish_device_status(
                state="alarm" if current_detected else "normal",
                value=1 if current_detected else 0,
                reason="heartbeat",
            )
            t_last_status = now

        if current_detected:
            set_led(True)
        else:
            if time.ticks_diff(now, t_last_led) >= LED_BLINK_MS:
                t_last_led = now
                set_led(not led.value())

        time.sleep_ms(5)


main()
