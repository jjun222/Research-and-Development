import machine, time, network, ujson
import socket, os
from simple import MQTTClient  # umqtt.simple

# ========= 하드웨어/네트워크 =========
MQ7_SENSOR_PIN = 27                 # ADC1 = GP27
mq7_sensor = machine.ADC(MQ7_SENSOR_PIN)

LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)

# ========= Wi-Fi / MQTT 기본 설정 =========
WIFI_SSID = ""import machine, time, network, ujson
import socket, os
from simple import MQTTClient

MQ7_SENSOR_PIN = 27
mq7_sensor = machine.ADC(MQ7_SENSOR_PIN)

LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)

try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

WIFI_SSID = ""
WIFI_PASSWORD = ""

CONFIG_PATH        = "wifi_config.json"
DEFAULT_BROKER_IP  = "192.168.0.33"

MQTT_BROKER    = DEFAULT_BROKER_IP
MQTT_TOPIC     = "mq7/sensor"
MQTT_CLIENT_ID = "mq7_sensor_pico"

KEEPALIVE_SEC      = 60
PING_INTERVAL_MS   = 30_000
LED_BLINK_MS       = 500

WARMUP_MS          = 120_000
WARMUP_LOG_MS      = 10_000
WARMUP_LED_MS      = 120

FILTER_SAMPLES     = 7
FILTER_DELAY_MS    = 20
AVG_WINDOW         = 5

THRESHOLD_HIGH     = 30_000
THRESHOLD_LOW      = 28_000
HEARTBEAT_MS       = 10_000

FAULT_MIN_VALID    = 50
FAULT_MAX_VALID    = 65_000
FAULT_CONSEC_BAD   = 5
FAULT_CONSEC_GOOD  = 3
FAULT_LOG_MS       = 10_000

WIFI_RETRY_MAX     = 15
MQTT_RECONNECT_MAX = 15
MAX_RECOVERY_FAILS = 8

wlan   = None
client = None
recovery_fail_count = 0

AP_SSID = "mq7_sensor_setup"
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

def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

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
        elif c == '%' and i+2 < len(s):
            try:
                res += chr(int(s[i+1:i+3], 16))
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

    wlan.active(False)
    time.sleep(1)
    wlan.active(True)
    time.sleep(1)

    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
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
    if wlan is None or (not wlan.isconnected()):
        if not wifi_connect():
            print("⚠️ Wi-Fi 미연결 상태")
            return False
    return True

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

def mqtt_connect():
    global client
    try:
        client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
        client.connect()
        print("✅ MQTT 연결 완료 (broker =", MQTT_BROKER, ")")
        blink_n(5)
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
        if client is None:
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue

        try:
            client.publish(topic, msg)
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e)
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)

    return False

def send_status(value, is_fire, reason="heartbeat", raw_value=None, median_value=None):
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "mq7_detected",
        "status": "화재 감지!" if is_fire else "정상",
        "value": int(value),
        "raw_value": int(raw_value) if raw_value is not None else None,
        "median_value": int(median_value) if median_value is not None else None,
        "reason": reason,
        "timestamp": now_str()
    }
    print("📤 상태 전송:",
          "🔥 감지!" if is_fire else "✅ 정상",
          "(filtered=%d, raw=%s, median=%s, reason=%s)" %
          (int(value), str(raw_value), str(median_value), reason))
    blink_once(40, 40)
    publish_json(MQTT_TOPIC, payload)

filter_history = []

def read_median_sample():
    vals = []
    for _ in range(FILTER_SAMPLES):
        vals.append(mq7_sensor.read_u16())
        time.sleep_ms(FILTER_DELAY_MS)
    vals.sort()
    return vals[len(vals) // 2]

def read_filtered_adc():
    raw = mq7_sensor.read_u16()
    median_v = read_median_sample()

    filter_history.append(median_v)
    if len(filter_history) > AVG_WINDOW:
        filter_history.pop(0)

    avg_v = sum(filter_history) // len(filter_history)
    return raw, median_v, avg_v

def main():
    global recovery_fail_count

    set_led(False)
    blink_n(2)

    startup_wifi_or_portal()

    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    print("📍 MQ7 센서 모니터링 시작")
    print("⏳ 예열 시작: %d초" % (WARMUP_MS // 1000))

    boot_ms = time.ticks_ms()
    t_ping = time.ticks_ms()
    t_led = time.ticks_ms()
    t_last_report = time.ticks_ms()
    t_last_warmup_log = time.ticks_ms()
    t_last_fault_log = time.ticks_ms()

    led_state = False
    current_fire_state = None
    last_sent_state = None

    fault_active = False
    fault_bad_count = 0
    fault_good_count = 0

    while True:
        now = time.ticks_ms()

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            ok_wifi = wifi_ensure()
            ok_mqtt = mqtt_ping() if ok_wifi else False

            if (not ok_wifi) or (not ok_mqtt):
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    print("⚠️ 복구 실패 누적:", recovery_fail_count)
                    blink_once(200, 200)
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("mq7 wifi/mqtt stuck")
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

            time.sleep(0.05)
            continue

        if time.ticks_diff(now, t_led) >= LED_BLINK_MS:
            led_state = not led_state
            set_led(led_state)
            t_led = now

        raw_value, median_value, mq7_value = read_filtered_adc()

        if mq7_value <= FAULT_MIN_VALID or mq7_value >= FAULT_MAX_VALID:
            fault_bad_count += 1
            fault_good_count = 0
        else:
            fault_good_count += 1
            fault_bad_count = 0

        if (not fault_active) and fault_bad_count >= FAULT_CONSEC_BAD:
            fault_active = True
            print("⚠️ 센서 이상 감지:", mq7_value, raw_value, median_value)

        if fault_active and fault_good_count >= FAULT_CONSEC_GOOD:
            fault_active = False
            print("✅ 센서 이상 해제")

        if fault_active:
            if time.ticks_diff(now, t_last_fault_log) >= FAULT_LOG_MS:
                print("⚠️ 센서 이상 상태 유지 중...",
                      "(filtered=%d, raw=%d, median=%d)" % (mq7_value, raw_value, median_value))
                t_last_fault_log = now
            time.sleep(0.1)
            continue

        if current_fire_state is None:
            current_fire_state = (mq7_value >= THRESHOLD_HIGH)
        else:
            if current_fire_state:
                if mq7_value <= THRESHOLD_LOW:
                    current_fire_state = False
            else:
                if mq7_value >= THRESHOLD_HIGH:
                    current_fire_state = True

        if last_sent_state is None or current_fire_state != last_sent_state:
            send_status(mq7_value, current_fire_state, "state_change", raw_value, median_value)
            last_sent_state = current_fire_state
            t_last_report = now
        elif time.ticks_diff(now, t_last_report) >= HEARTBEAT_MS:
            send_status(mq7_value, current_fire_state, "heartbeat", raw_value, median_value)
            t_last_report = now

        time.sleep(0.1)

main()
WIFI_PASSWORD = ""

CONFIG_PATH        = "wifi_config.json"
DEFAULT_BROKER_IP  = "192.168.0.33"   # 현재 브로커 IP로 수정

MQTT_BROKER    = DEFAULT_BROKER_IP
MQTT_TOPIC     = "mq7/sensor"
MQTT_CLIENT_ID = "mq7_sensor_pico"

# ========= 동작 파라미터 =========
KEEPALIVE_SEC      = 60
PING_INTERVAL_MS   = 30_000
LED_BLINK_MS       = 500

# [1] 예열
WARMUP_MS          = 120_000          # 2분
WARMUP_LOG_MS      = 10_000
WARMUP_LED_MS      = 120

# [2] 필터
FILTER_SAMPLES     = 7
FILTER_DELAY_MS    = 20
AVG_WINDOW         = 5

# [3] 히스테리시스
THRESHOLD_HIGH     = 30_000
THRESHOLD_LOW      = 28_000

# [4] 10초 heartbeat
HEARTBEAT_MS       = 10_000

# [7] 센서 이상값 처리
FAULT_MIN_VALID    = 50
FAULT_MAX_VALID    = 65_000
FAULT_CONSEC_BAD   = 5
FAULT_CONSEC_GOOD  = 3
FAULT_LOG_MS       = 10_000

WIFI_RETRY_MAX     = 15
MQTT_RECONNECT_MAX = 15

wlan   = None
client = None

# ========= AP 모드 =========
AP_SSID = "mq7_sensor_setup"
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

def set_led(v):
    led.value(1 if v else 0)

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
        if c == "+":
            res += " "
        elif c == "%" and i+2 < len(s):
            try:
                res += chr(int(s[i+1:i+3], 16))
                i += 2
            except Exception:
                res += c
        else:
            res += c
        i += 1
    return res

def parse_form(body):
    out = {}
    parts = body.split("&")
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
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

    wlan.active(False)
    time.sleep(1)
    wlan.active(True)
    time.sleep(1)

    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
        attempt += 1
        time.sleep(0.5)

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패")
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
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
    if wlan is None or (not wlan.isconnected()):
        if not wifi_connect():
            print("⚠️ Wi-Fi 미연결 상태")
            return False
    return True

# ========= AP 설정 포털 =========
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
        print("🔁 MQTT 재연결 시도", attempt + 1)
        wifi_ensure()

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

def publish_json(topic, obj):
    global client
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5
    for attempt in range(4):
        if client is None:
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue

        try:
            client.publish(topic, msg)
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e)
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)

    print("🚫 publish 포기")
    return False

def send_status(value, is_fire, reason="heartbeat", raw_value=None, median_value=None):
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "mq7_detected",
        "status": "화재 감지!" if is_fire else "정상",
        "value": int(value),
        "raw_value": int(raw_value) if raw_value is not None else None,
        "median_value": int(median_value) if median_value is not None else None,
        "reason": reason,
        "timestamp": now_str()
    }
    print("📤 상태 전송:",
          "🔥 감지!" if is_fire else "✅ 정상",
          "(filtered=%d, raw=%s, median=%s, reason=%s)" %
          (int(value), str(raw_value), str(median_value), reason))
    publish_json(MQTT_TOPIC, payload)

# ========= 센서 필터 =========
filter_history = []

def read_median_sample():
    vals = []
    for _ in range(FILTER_SAMPLES):
        vals.append(mq7_sensor.read_u16())
        time.sleep_ms(FILTER_DELAY_MS)
    vals.sort()
    return vals[len(vals) // 2]

def read_filtered_adc():
    raw = mq7_sensor.read_u16()
    median_v = read_median_sample()

    filter_history.append(median_v)
    if len(filter_history) > AVG_WINDOW:
        filter_history.pop(0)

    avg_v = sum(filter_history) // len(filter_history)
    return raw, median_v, avg_v

# ========= 메인 =========
def main():
    startup_wifi_or_portal()

    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    print("📍 MQ7 센서 모니터링 시작")
    print("⏳ 예열 시작: %d초" % (WARMUP_MS // 1000))

    boot_ms = time.ticks_ms()
    t_ping = time.ticks_ms()
    t_led = time.ticks_ms()
    t_last_report = time.ticks_ms()
    t_last_warmup_log = time.ticks_ms()
    t_last_fault_log = time.ticks_ms()

    led_state = False
    current_fire_state = None
    last_sent_state = None

    fault_active = False
    fault_bad_count = 0
    fault_good_count = 0

    while True:
        now = time.ticks_ms()

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        # 예열 단계
        if time.ticks_diff(now, boot_ms) < WARMUP_MS:
            if time.ticks_diff(now, t_led) >= WARMUP_LED_MS:
                led_state = not led_state
                led.value(led_state)
                t_led = now

            if time.ticks_diff(now, t_last_warmup_log) >= WARMUP_LOG_MS:
                remain = max(0, (WARMUP_MS - time.ticks_diff(now, boot_ms)) // 1000)
                print("⏳ 예열 중... 남은 시간:", remain, "초")
                t_last_warmup_log = now

            time.sleep(0.05)
            continue

        # 정상 동작 LED
        if time.ticks_diff(now, t_led) >= LED_BLINK_MS:
            led_state = not led_state
            led.value(led_state)
            t_led = now

        raw_value, median_value, mq7_value = read_filtered_adc()

        # 센서 이상값 처리
        if mq7_value <= FAULT_MIN_VALID or mq7_value >= FAULT_MAX_VALID:
            fault_bad_count += 1
            fault_good_count = 0
        else:
            fault_good_count += 1
            fault_bad_count = 0

        if (not fault_active) and fault_bad_count >= FAULT_CONSEC_BAD:
            fault_active = True
            print("⚠️ 센서 이상 감지:", mq7_value, raw_value, median_value)

        if fault_active and fault_good_count >= FAULT_CONSEC_GOOD:
            fault_active = False
            print("✅ 센서 이상 해제")

        if fault_active:
            if time.ticks_diff(now, t_last_fault_log) >= FAULT_LOG_MS:
                print("⚠️ 센서 이상 상태 유지 중...",
                      "(filtered=%d, raw=%d, median=%d)" % (mq7_value, raw_value, median_value))
                t_last_fault_log = now
            time.sleep(0.1)
            continue

        # 히스테리시스
        if current_fire_state is None:
            current_fire_state = (mq7_value >= THRESHOLD_HIGH)
        else:
            if current_fire_state:
                if mq7_value <= THRESHOLD_LOW:
                    current_fire_state = False
            else:
                if mq7_value >= THRESHOLD_HIGH:
                    current_fire_state = True

        # 상태 변화 즉시 전송
        if last_sent_state is None or current_fire_state != last_sent_state:
            send_status(
                value=mq7_value,
                is_fire=current_fire_state,
                reason="state_change",
                raw_value=raw_value,
                median_value=median_value
            )
            last_sent_state = current_fire_state
            t_last_report = now

        # 10초 heartbeat
        elif time.ticks_diff(now, t_last_report) >= HEARTBEAT_MS:
            send_status(
                value=mq7_value,
                is_fire=current_fire_state,
                reason="heartbeat",
                raw_value=raw_value,
                median_value=median_value
            )
            t_last_report = now

        time.sleep(0.1)

main()
