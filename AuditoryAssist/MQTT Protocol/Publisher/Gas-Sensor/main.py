import gc
import machine
import network
import os
import socket
import time
import ujson
from simple import MQTTClient

# =========================
# Gas sensor MQ5 (product final, cold-boot stabilized)
# - Cold boot power-settle delay before Wi-Fi start
# - Wi-Fi connect tries up to 30 checks before AP mode
# - Keeps WDT / reconnect / AP provisioning / publish protection
# =========================

# ========= Hardware / Network =========
GAS_SENSOR_PIN = 26
LED_PIN = 28

gas_sensor = machine.ADC(GAS_SENSOR_PIN)
led = machine.Pin(LED_PIN, machine.Pin.OUT)

try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

WIFI_SSID = ""
WIFI_PASSWORD = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

# ========= MQTT =========
MQTT_BROKER = DEFAULT_BROKER_IP
MQTT_TOPIC = "gas/sensor"
MQTT_CLIENT_ID = "gas_sensor_pico"
STATUS_TOPIC = "interfaceui/status/publisher/" + MQTT_CLIENT_ID

# ========= Sensor / Publish policy =========
PRIORITY_EMERGENCY = "emergency"
PRIORITY_ROUTINE = "routine"

KEEPALIVE_SEC = 60
PING_INTERVAL_MS = 30_000
LED_BLINK_MS = 500

WARMUP_MS = 20_000
WARMUP_LOG_MS = 10_000
WARMUP_LED_MS = 120
WARMUP_STATUS_MS = 30_000

FILTER_SAMPLES = 7
FILTER_DELAY_MS = 20
AVG_WINDOW = 5

THRESHOLD_HIGH = 30_000
THRESHOLD_LOW = 28_000

HEARTBEAT_MS = 5_000
STATUS_HEARTBEAT_MS = 60_000
PUBLISH_GAP_MS = 120

FAULT_MIN_VALID = 50
FAULT_MAX_VALID = 65_000
FAULT_CONSEC_BAD = 5
FAULT_CONSEC_GOOD = 3
FAULT_LOG_MS = 10_000

# ========= Cold boot / Stability =========
# 30 checks * 0.5 sec = about 15 sec before giving up to AP mode
WIFI_RETRY_MAX = 30
WIFI_RETRY_WAIT_MS = 500

MQTT_RECONNECT_MAX = 10
MAX_RECOVERY_FAILS = 5
MAX_PUBLISH_FAIL_STREAK = 3
MAX_PUBLISH_SILENCE_MS = 180_000

# Cold boot stabilization for UPS / power-only boot
COLD_BOOT_SETTLE_MS = 12_000
SOFT_BOOT_SETTLE_MS = 3_000
WIFI_POST_RESET_WAIT_MS = 1_500
POST_WIFI_CONNECT_SETTLE_MS = 1_000

GC_INTERVAL_MS = 10_000
WDT_TIMEOUT_MS = 8_000
SOCKET_TIMEOUT_SEC = 3

FORCE_ROTATE_AFTER_PUBLISHES = 120
DEBUG_PUBLISH = False

# ========= AP Portal =========
AP_SSID = "gas_sensor_setup"
AP_PW = "123456789"

HTML_FORM = """\
HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>WiFi 설정</title></head>
<body>
<h2>Wi-Fi / MQTT 설정</h2>
<form method="POST" action="/save">
  SSID: <input name="ssid"><br>
  PW: <input name="pw" type="password"><br>
  Broker IP: <input name="broker" value="%s"><br>
  <button type="submit">저장</button>
</form>
</body>
</html>
""" % DEFAULT_BROKER_IP

HTML_SAVED = """\
HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
"""

# ========= Globals =========
wlan = None
client = None
wdt = None

recovery_fail_count = 0
publish_success_count = 0
publish_fail_streak = 0
last_publish_ok_ms = time.ticks_ms()

BOOT_TICKS_MS = time.ticks_ms()
filter_history = []


# ========= Common helpers =========
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
    val = 1 if v else 0
    led.value(val)
    if onboard_led is not None:
        onboard_led.value(val)


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


def get_ip():
    try:
        if wlan is not None and wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return ""


def record_publish_success():
    global publish_success_count, publish_fail_streak, last_publish_ok_ms
    publish_success_count += 1
    publish_fail_streak = 0
    last_publish_ok_ms = time.ticks_ms()


def record_publish_failure():
    global publish_fail_streak
    publish_fail_streak += 1
    print("⚠️ publish 실패 누적:", publish_fail_streak)
    if publish_fail_streak >= MAX_PUBLISH_FAIL_STREAK:
        hard_recover("repeated publish failures")


def check_publish_silence(now_ms):
    if time.ticks_diff(now_ms, last_publish_ok_ms) >= MAX_PUBLISH_SILENCE_MS:
        hard_recover("publish silence timeout")


# ========= Wi-Fi config / portal =========
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
        print("🌐 기본 설정으로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
        return True

    return False


def wifi_ensure():
    if wlan is None or (not wlan.isconnected()):
        print("⚠️ Wi-Fi 미연결 감지 → 재연결")
        return connect_wifi_from_config()
    return True


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
        cl, addr = s.accept()
        print("새 접속:", addr)
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


# ========= MQTT =========
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

        time.sleep(backoff)
        backoff = min(backoff * 2, 5)

    print("🚫 MQTT 재연결 포기")
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
    reset_wifi_interface()
    time.sleep(2)
    machine.reset()


def publish_json(topic, obj):
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5

    for attempt in range(4):
        feed_wdt()

        if not wifi_ensure():
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            continue

        if client is None and not mqtt_reconnect_with_backoff():
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

            record_publish_success()
            maybe_rotate_mqtt()
            return True

        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e)
            close_mqtt_client()
            gc.collect()
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)

    record_publish_failure()
    return False


# ========= Payload builders =========
def publish_device_status(state="normal", value=None, reason="heartbeat"):
    payload = {
        "id": MQTT_CLIENT_ID,
        "type": "publisher",
        "device_type": "gas_sensor",
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


def send_status(value, is_fire, reason="heartbeat", raw_value=None, median_value=None):
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "gas_detected",
        "status": "화재 감지!" if is_fire else "정상",
        "value": int(value),
        "raw_value": int(raw_value) if raw_value is not None else None,
        "median_value": int(median_value) if median_value is not None else None,
        "reason": reason,
        "timestamp": now_str(),
        "publisher_uptime_ms": int(uptime_ms()),
        "publisher_reset_cause": reset_cause_name(),
        "priority_class": PRIORITY_EMERGENCY if is_fire else PRIORITY_ROUTINE,
    }

    print(
        "📤 상태 전송:",
        "🔥 화재 감지!" if is_fire else "✅ 정상",
        "(filtered=%d, raw=%s, median=%s, reason=%s)" %
        (int(value), str(raw_value), str(median_value), reason)
    )

    blink_once(40, 40)
    ok = publish_json(MQTT_TOPIC, payload)
    if not ok:
        print("❌ 상태 publish 최종 실패")
    return ok


# ========= Sensor filtering =========
def read_median_sample():
    vals = []
    for _ in range(FILTER_SAMPLES):
        feed_wdt()
        vals.append(gas_sensor.read_u16())
        time.sleep_ms(FILTER_DELAY_MS)
    vals.sort()
    return vals[len(vals) // 2]


def read_filtered_adc():
    raw = gas_sensor.read_u16()
    median_v = read_median_sample()

    filter_history.append(median_v)
    if len(filter_history) > AVG_WINDOW:
        filter_history.pop(0)

    avg_v = sum(filter_history) // len(filter_history)
    return raw, median_v, avg_v


# ========= Main loop =========
def run_loop():
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
    pre_boot_stabilize()

    startup_wifi_or_portal()

    while not mqtt_connect():
        time.sleep(5)

    start_watchdog()

    print("📍 MQ5 센서 모니터링 시작")
    print("⏳ 예열 시작: %d초" % (WARMUP_MS // 1000))

    boot_ms = time.ticks_ms()
    t_ping = time.ticks_ms()
    t_led = time.ticks_ms()
    t_last_report = time.ticks_ms()
    t_last_warmup_log = time.ticks_ms()
    t_last_fault_log = time.ticks_ms()
    t_last_status = time.ticks_ms()
    t_last_gc = time.ticks_ms()

    led_state = False
    current_fire_state = None
    last_sent_state = None

    fault_active = False
    fault_bad_count = 0
    fault_good_count = 0

    while True:
        feed_wdt()
        now = time.ticks_ms()
        t_last_gc = maybe_gc(t_last_gc)
        check_publish_silence(now)

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            ok_wifi = wifi_ensure()
            ok_mqtt = mqtt_ping() if ok_wifi else False

            if (not ok_wifi) or (not ok_mqtt):
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    print("⚠️ 복구 실패 누적:", recovery_fail_count)
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("gas wifi/mqtt stuck")
                else:
                    recovery_fail_count = 0
            else:
                recovery_fail_count = 0

            t_ping = now

        if time.ticks_diff(now, boot_ms) < WARMUP_MS:
            if time.ticks_diff(now, t_led) >= WARMUP_LED_MS:
                led_state = not led_state
                set_led(led_state)
                t_led = now

            if time.ticks_diff(now, t_last_warmup_log) >= WARMUP_LOG_MS:
                remain = max(0, (WARMUP_MS - time.ticks_diff(now, boot_ms)) // 1000)
                print("⏳ 예열 중... 남은 시간:", remain, "초")
                t_last_warmup_log = now

            if time.ticks_diff(now, t_last_status) >= WARMUP_STATUS_MS:
                publish_device_status(state="warming_up", value=None, reason="heartbeat")
                t_last_status = now

            time.sleep_ms(50)
            continue

        if time.ticks_diff(now, t_led) >= LED_BLINK_MS:
            led_state = not led_state
            set_led(led_state)
            t_led = now

        raw_value, median_value, gas_value = read_filtered_adc()

        if gas_value <= FAULT_MIN_VALID or gas_value >= FAULT_MAX_VALID:
            fault_bad_count += 1
            fault_good_count = 0
        else:
            fault_good_count += 1
            fault_bad_count = 0

        if (not fault_active) and fault_bad_count >= FAULT_CONSEC_BAD:
            fault_active = True
            print("⚠️ 센서 이상 감지:", gas_value, raw_value, median_value)

        if fault_active and fault_good_count >= FAULT_CONSEC_GOOD:
            fault_active = False
            print("✅ 센서 이상 해제")

        if fault_active:
            if time.ticks_diff(now, t_last_fault_log) >= FAULT_LOG_MS:
                print(
                    "⚠️ 센서 이상 상태 유지 중...",
                    "(filtered=%d, raw=%d, median=%d)" %
                    (gas_value, raw_value, median_value)
                )
                t_last_fault_log = now

            if time.ticks_diff(now, t_last_status) >= STATUS_HEARTBEAT_MS:
                publish_device_status(state="fault", value=gas_value, reason="heartbeat")
                t_last_status = now

            time.sleep_ms(100)
            continue

        if current_fire_state is None:
            current_fire_state = (gas_value >= THRESHOLD_HIGH)
        else:
            if current_fire_state:
                if gas_value <= THRESHOLD_LOW:
                    current_fire_state = False
            else:
                if gas_value >= THRESHOLD_HIGH:
                    current_fire_state = True

        if last_sent_state is None or current_fire_state != last_sent_state:
            send_status(gas_value, current_fire_state, "state_change", raw_value, median_value)
            short_gap()
            publish_device_status(
                state="alarm" if current_fire_state else "normal",
                value=gas_value,
                reason="state_change"
            )
            last_sent_state = current_fire_state
            t_last_report = now
            t_last_status = now

        elif time.ticks_diff(now, t_last_report) >= HEARTBEAT_MS:
            send_status(gas_value, current_fire_state, "heartbeat", raw_value, median_value)
            t_last_report = now

        if time.ticks_diff(now, t_last_status) >= STATUS_HEARTBEAT_MS:
            short_gap()
            publish_device_status(
                state="alarm" if current_fire_state else "normal",
                value=gas_value,
                reason="heartbeat"
            )
            t_last_status = now

        time.sleep_ms(100)


def main():
    while True:
        try:
            run_loop()
        except Exception as e:
            print("💥 main loop 예외:", e)
            close_mqtt_client()
            gc.collect()
            time.sleep(2)
            machine.reset()


main()
