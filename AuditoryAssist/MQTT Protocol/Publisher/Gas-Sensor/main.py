# gas sensor - final revised stable
import machine, time, network, ujson, gc
import socket, os
from simple import MQTTClient   # umqtt.simple

# ========= 하드웨어/네트워크 설정 =========
GAS_SENSOR_PIN = 26
gas_sensor = machine.ADC(GAS_SENSOR_PIN)

# 외부 LED
LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)

# Pico W 내장 LED
try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

WIFI_SSID = ""
WIFI_PASSWORD = ""

CONFIG_PATH       = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

# ========= MQTT 정보 =========
MQTT_BROKER    = DEFAULT_BROKER_IP
MQTT_TOPIC     = "gas/sensor"
MQTT_CLIENT_ID = "gas_sensor_pico"

STATUS_TOPIC   = "interfaceui/status/publisher/" + MQTT_CLIENT_ID

# ========= 테스트 기본값 =========
TEST_GROUP   = "SR"               # SR / FP / PR / CM
TEST_ID      = "SR-01A"
SCENARIO_ID  = "gas_to_broker"
TRIAL_NO     = 1
PRIORITY_EMERGENCY = "emergency"
PRIORITY_ROUTINE   = "routine"
SEQ = 0

# ========= 동작 파라미터 =========
KEEPALIVE_SEC        = 60
PING_INTERVAL_MS     = 30_000
LED_BLINK_MS         = 500

WARMUP_MS            = 20_000
WARMUP_LOG_MS        = 10_000
WARMUP_LED_MS        = 120
WARMUP_STATUS_MS     = 30_000   # 예열 중 status 주기

FILTER_SAMPLES       = 7
FILTER_DELAY_MS      = 20
AVG_WINDOW           = 5

THRESHOLD_HIGH       = 30_000
THRESHOLD_LOW        = 28_000

HEARTBEAT_MS         = 10_000   # 센서 데이터 heartbeat
STATUS_HEARTBEAT_MS  = 60_000   # status 토픽 heartbeat (분리)
PUBLISH_GAP_MS       = 120      # 연속 publish 사이 간격

FAULT_MIN_VALID      = 50
FAULT_MAX_VALID      = 65_000
FAULT_CONSEC_BAD     = 5
FAULT_CONSEC_GOOD    = 3
FAULT_LOG_MS         = 10_000

WIFI_RETRY_MAX       = 15
MQTT_RECONNECT_MAX   = 15
MAX_RECOVERY_FAILS   = 8

GC_INTERVAL_MS       = 30_000
WDT_TIMEOUT_MS       = 30_000   # 8초 -> 30초 완화
DEBUG_PUBLISH        = True

wlan   = None
client = None
wdt    = None

recovery_fail_count = 0
BOOT_TICKS_MS = time.ticks_ms()

# ========= AP 모드 =========
AP_SSID = "gas_sensor_setup"
AP_PW   = "123456789"

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
  PW:   <input name="pw" type="password"><br>
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
<html><body>
<p>저장되었습니다. 3초 후 재부팅합니다.</p>
</body></html>
"""

# ========= 유틸 =========
def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

def uptime_ms():
    return time.ticks_diff(time.ticks_ms(), BOOT_TICKS_MS)

def next_seq():
    global SEQ
    SEQ += 1
    return SEQ

def reset_cause_name():
    cause = machine.reset_cause()
    mapping = {}
    if hasattr(machine, "PWRON_RESET"):
        mapping[machine.PWRON_RESET] = "PWRON_RESET"
    if hasattr(machine, "HARD_RESET"):
        mapping[machine.HARD_RESET] = "HARD_RESET"
    if hasattr(machine, "WDT_RESET"):
        mapping[machine.WDT_RESET] = "WDT_RESET"
    if hasattr(machine, "DEEPSLEEP_RESET"):
        mapping[machine.DEEPSLEEP_RESET] = "DEEPSLEEP_RESET"
    if hasattr(machine, "SOFT_RESET"):
        mapping[machine.SOFT_RESET] = "SOFT_RESET"
    return mapping.get(cause, str(cause))

def start_watchdog():
    global wdt
    try:
        wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
        print("🛡️ WDT 시작:", WDT_TIMEOUT_MS, "ms")
    except Exception as e:
        wdt = None
        print("⚠️ WDT 시작 실패:", e)

def feed_wdt():
    global wdt
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

def build_test_fields(priority_class):
    return {
        "test_group": TEST_GROUP,
        "test_id": TEST_ID,
        "scenario_id": SCENARIO_ID,
        "trial_no": TRIAL_NO,
        "seq": next_seq(),
        "t_sent_ms": int(uptime_ms()),  # monotonic uptime 기준
        "publisher_clock": "monotonic_uptime_ms",
        "publisher_reset_cause": reset_cause_name(),
        "priority_class": priority_class,
        "source_path": "gas_sensor->broker",
        "expected_inputs": 1,
        "received_inputs": 1,
        "expected_devices": 1,
        "activated_devices": 0
    }

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

def get_ip():
    global wlan
    try:
        if wlan is not None and wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return ""

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
    try:
        with open(CONFIG_PATH, "w") as f:
            f.write(ujson.dumps(cfg))
        print("✅ Wi-Fi 설정 저장 완료:", cfg)
    except Exception as e:
        print("❌ config 저장 실패:", e)

def url_decode(s):
    res = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == '+':
            res += ' '
        elif c == '%' and i + 2 < len(s):
            try:
                res += chr(int(s[i + 1:i + 3], 16))
                i += 2
            except Exception:
                res += c
        else:
            res += c
        i += 1
    return res

def parse_form(body):
    out = {}
    parts = body.split('&')
    for p in parts:
        if '=' in p:
            k, v = p.split('=', 1)
            out[k] = url_decode(v)
    return out

# ========= Wi-Fi =========
def try_connect_wifi(ssid, pw):
    global wlan

    if not ssid or not pw:
        print("⚠️ SSID 또는 PW 없음")
        return False

    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    try:
        wlan.disconnect()
    except Exception:
        pass

    try:
        wlan.active(False)
        time.sleep(1)
        wlan.active(True)
        time.sleep(1)
    except Exception as e:
        print("⚠️ WLAN 재활성화 실패:", e)

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
        pw   = cfg.get("password")
        if ssid and pw:
            if try_connect_wifi(ssid, pw):
                broker = cfg.get("broker")
                MQTT_BROKER = broker or DEFAULT_BROKER_IP
                print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
                return True

    if WIFI_SSID and WIFI_PASSWORD:
        if try_connect_wifi(WIFI_SSID, WIFI_PASSWORD):
            MQTT_BROKER = DEFAULT_BROKER_IP
            print("🌐 기본 설정으로 연결, broker =", MQTT_BROKER)
            return True

    return False

def wifi_connect():
    return connect_wifi_from_config()

def wifi_ensure():
    global wlan
    try:
        if wlan is None or (not wlan.isconnected()):
            print("⚠️ Wi-Fi 미연결 감지 → 재연결")
            if not wifi_connect():
                print("⚠️ Wi-Fi 미연결 상태")
                return False
    except Exception as e:
        print("⚠️ wifi_ensure 예외:", e)
        return False
    return True

# ========= AP 포털 =========
def start_config_portal():
    sta = network.WLAN(network.STA_IF)
    sta.active(False)

    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PW)
    ap.active(True)
    print("📶 AP 모드 시작:", ap.ifconfig())
    print("➡ 폰에서", AP_SSID, "접속 후 브라우저에서 http://192.168.4.1 열기")

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
            parts = req_str.split("\r\n\r\n", 1)
            body = parts[1] if len(parts) > 1 else ""
            form = parse_form(body)
            ssid   = form.get("ssid", "").strip()
            pw     = form.get("pw", "").strip()
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
    if wifi_connect():
        return True
    print("⚠️ Wi-Fi 접속 실패 → AP 모드 진입")
    start_config_portal()
    return False

# ========= MQTT =========
def mqtt_connect():
    global client
    try:
        client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
        client.connect()
        print("✅ MQTT 연결 완료 (broker =", MQTT_BROKER, ")")
        blink_n(5)

        # online 상태는 즉시 1회 발행
        ok = publish_device_status(state="online", value=None, reason="mqtt_connected")
        if not ok:
            print("⚠️ online status 발행 실패")
        return True

    except Exception as e:
        print("❌ MQTT 연결 실패:", e)
        client = None
        return False

def mqtt_ping():
    global client
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception as e:
        print("⚠️ ping 실패:", e)
        return False

def mqtt_reconnect_with_backoff():
    global client
    backoff = 0.5

    for attempt in range(MQTT_RECONNECT_MAX):
        feed_wdt()
        print("🔁 MQTT 재연결 시도", attempt + 1)

        ok_wifi = wifi_ensure()
        if not ok_wifi:
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            continue

        try:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

            if mqtt_connect():
                print("✅ MQTT 재연결 성공")
                return True

        except Exception as e:
            print("❌ MQTT 재연결 중 예외:", e)

        time.sleep(backoff)
        backoff = min(backoff * 2, 5)

    print("🚫 MQTT 재연결 포기")
    return False

def hard_recover(reason="unknown"):
    global client, wlan
    print("♻️ 하드 복구 실행:", reason)
    blink_n(4, 120, 120)

    try:
        if client is not None:
            client.disconnect()
    except Exception:
        pass
    client = None

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

def publish_json(topic, obj):
    global client

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
                print("➡️ publish start:", topic, "try=", attempt + 1, "len=", len(msg))
            client.publish(topic, msg)
            if DEBUG_PUBLISH:
                print("✅ publish done :", topic)
            return True

        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e)
            try:
                if client is not None:
                    client.disconnect()
            except Exception:
                pass
            client = None

            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)

    return False

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
        "publisher_reset_cause": reset_cause_name()
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
        "publisher_reset_cause": reset_cause_name()
    }

    payload.update(build_test_fields(PRIORITY_EMERGENCY if is_fire else PRIORITY_ROUTINE))

    print(
        "📤 상태 전송:",
        "🔥 화재 감지!" if is_fire else "✅ 정상",
        "(filtered=%d, raw=%s, median=%s, reason=%s, seq=%d)" %
        (int(value), str(raw_value), str(median_value), reason, payload["seq"])
    )

    blink_once(40, 40)
    ok = publish_json(MQTT_TOPIC, payload)
    if not ok:
        print("❌ 상태 publish 최종 실패")
    return ok

# ========= 필터 =========
filter_history = []

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

# ========= 메인 =========
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

    startup_wifi_or_portal()

    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
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

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            print("🔎 keepalive check")
            ok_wifi = wifi_ensure()
            ok_mqtt = mqtt_ping() if ok_wifi else False

            if (not ok_wifi) or (not ok_mqtt):
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    print("⚠️ 복구 실패 누적:", recovery_fail_count)
                    blink_once(200, 200)
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("gas wifi/mqtt stuck")
                else:
                    recovery_fail_count = 0
            else:
                recovery_fail_count = 0

            t_ping = now

        # 예열 구간
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
                ok = publish_device_status(state="warming_up", value=None, reason="heartbeat")
                if not ok:
                    print("⚠️ warming_up status publish 실패")
                t_last_status = now

            time.sleep(0.05)
            continue

        # 평상시 LED 점멸
        if time.ticks_diff(now, t_led) >= LED_BLINK_MS:
            led_state = not led_state
            set_led(led_state)
            t_led = now

        raw_value, median_value, gas_value = read_filtered_adc()

        # 센서 fault 감지
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

            # fault 상태는 즉시 status 유지
            if time.ticks_diff(now, t_last_status) >= STATUS_HEARTBEAT_MS:
                ok = publish_device_status(state="fault", value=gas_value, reason="heartbeat")
                if not ok:
                    print("⚠️ fault status publish 실패")
                t_last_status = now

            time.sleep(0.1)
            continue

        # 히스테리시스
        if current_fire_state is None:
            current_fire_state = (gas_value >= THRESHOLD_HIGH)
        else:
            if current_fire_state:
                if gas_value <= THRESHOLD_LOW:
                    current_fire_state = False
            else:
                if gas_value >= THRESHOLD_HIGH:
                    current_fire_state = True

        # 상태 변화 시: 센서 publish + status publish
        if last_sent_state is None or current_fire_state != last_sent_state:
            ok1 = send_status(gas_value, current_fire_state, "state_change", raw_value, median_value)

            short_gap()

            ok2 = publish_device_status(
                state="alarm" if current_fire_state else "normal",
                value=gas_value,
                reason="state_change"
            )

            if not ok1:
                print("⚠️ state_change sensor publish 실패")
            if not ok2:
                print("⚠️ state_change status publish 실패")

            last_sent_state = current_fire_state
            t_last_report = now
            t_last_status = now

        # 센서 heartbeat
        elif time.ticks_diff(now, t_last_report) >= HEARTBEAT_MS:
            ok = send_status(gas_value, current_fire_state, "heartbeat", raw_value, median_value)
            if not ok:
                print("⚠️ heartbeat sensor publish 실패")
            t_last_report = now

        # status heartbeat는 별도 주기
        if time.ticks_diff(now, t_last_status) >= STATUS_HEARTBEAT_MS:
            short_gap()
            ok = publish_device_status(
                state="alarm" if current_fire_state else "normal",
                value=gas_value,
                reason="heartbeat"
            )
            if not ok:
                print("⚠️ heartbeat status publish 실패")
            t_last_status = now

        time.sleep(0.1)

def main():
    while True:
        try:
            run_loop()
        except Exception as e:
            print("💥 main loop 예외:", e)
            try:
                time.sleep(2)
            except Exception:
                pass
            machine.reset()

main()
