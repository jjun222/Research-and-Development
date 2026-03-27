import machine, time, network, ujson
import socket, os
from simple import MQTTClient

# ========= 하드웨어 =========
FIRE_SENSOR_PIN = 15
fire_sensor = machine.Pin(FIRE_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_UP)  # 0이면 감지

LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)

try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

# ========= Wi-Fi / MQTT =========
WIFI_SSID = ""
WIFI_PASSWORD = ""

CONFIG_PATH        = "wifi_config.json"
DEFAULT_BROKER_IP  = "192.168.0.33"

MQTT_BROKER    = DEFAULT_BROKER_IP
MQTT_TOPIC     = "shz/sensor"
MQTT_CLIENT_ID = "shz_sensor_pico"

KEEPALIVE_SEC      = 60
PING_INTERVAL_MS   = 30_000
HEARTBEAT_MS       = 10_000
WIFI_RETRY_MAX     = 15
MQTT_RECONNECT_MAX = 15
MAX_RECOVERY_FAILS = 8

BOOT_SETTLE_MS      = 2_000
SAMPLE_INTERVAL_MS  = 20
DETECT_STABLE_COUNT = 5
CLEAR_STABLE_COUNT  = 5
MIN_EVENT_GAP_MS    = 1_000

wlan   = None
client = None
recovery_fail_count = 0

# ========= AP =========
AP_SSID = "shz_sensor_setup"
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
def get_timestamp_string():
    now = time.localtime()
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*now)

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

# ========= AP =========
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

def send_status(is_detected, reason="heartbeat"):
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "shz_detected",
        "value": "감지됨" if is_detected else "정상",
        "reason": reason,
        "raw_active_low": 0 if is_detected else 1,
        "timestamp": get_timestamp_string()
    }
    print("📤 상태 전송:", payload["value"], "(reason=%s)" % reason)
    blink_once(40, 40)
    publish_json(MQTT_TOPIC, payload)

# ========= 센서 =========
def read_sensor_active():
    return fire_sensor.value() == 0

# ========= 메인 =========
def main():
    global recovery_fail_count

    set_led(False)
    blink_n(2)
    time.sleep_ms(BOOT_SETTLE_MS)

    startup_wifi_or_portal()

    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    print("📍 SHZ 센서 모니터링 시작")

    t_ping = time.ticks_ms()
    t_last_heartbeat = time.ticks_ms()
    t_last_sample = time.ticks_ms()
    last_event_ms = 0

    detect_count = 0
    clear_count = 0
    stable_detected = False
    last_sent_state = None

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

        if (not stable_detected) and detect_count >= DETECT_STABLE_COUNT:
            if time.ticks_diff(now, last_event_ms) >= MIN_EVENT_GAP_MS:
                stable_detected = True
                last_event_ms = now
                send_status(True, "state_change")
                last_sent_state = True
                t_last_heartbeat = now
                print("🔥 SHZ 안정 감지 전환")

        elif stable_detected and clear_count >= CLEAR_STABLE_COUNT:
            if time.ticks_diff(now, last_event_ms) >= MIN_EVENT_GAP_MS:
                stable_detected = False
                last_event_ms = now
                send_status(False, "state_change")
                last_sent_state = False
                t_last_heartbeat = now
                print("🔄 SHZ 정상 복귀")

        if time.ticks_diff(now, t_last_heartbeat) >= HEARTBEAT_MS:
            if last_sent_state is None:
                send_status(stable_detected, "startup_sync")
                last_sent_state = stable_detected
            else:
                send_status(stable_detected, "heartbeat")
            t_last_heartbeat = now

        if stable_detected:
            set_led(True)
        else:
            set_led((now // 500) % 2 == 0)

        time.sleep_ms(5)

main()
