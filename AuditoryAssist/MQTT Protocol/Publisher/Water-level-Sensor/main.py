import machine, time, network, ujson, socket, os
from umqtt.simple import MQTTClient

WATER_PIN = 5
water_switch = machine.Pin(WATER_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

WIFI_SSID = ""
WIFI_PASSWORD = ""

CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

MQTT_BROKER = DEFAULT_BROKER_IP
MQTT_TOPIC = "water_level/sensor"
MQTT_CLIENTID = "water_level_1"

KEEPALIVE_SEC = 60
PING_INTERVAL_MS = 30000
CHECK_INTERVAL_MS = 500
DEBOUNCE_SAMPLES = 10
DEBOUNCE_MIN_HIGH = 7
WIFI_RETRY_MAX = 15
MQTT_RECONNECT_MAX = 15
MAX_RECOVERY_FAILS = 8
BOOT_SETTLE_MS = 2000

AP_SSID = "water_level_setup"
AP_PW = "123456789"

HTML_FORM = '''HTTP/1.1 200 OK\r
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
</form>
</body></html>
''' % DEFAULT_BROKER_IP

HTML_SAVED = '''HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
'''

wlan = None
client = None
recovery_fail_count = 0

def set_led(v):
    if onboard_led is not None:
        onboard_led.value(1 if v else 0)

def blink_once(on_ms=80, off_ms=80):
    set_led(True); time.sleep_ms(on_ms)
    set_led(False); time.sleep_ms(off_ms)

def blink_n(n, on_ms=80, off_ms=80):
    for _ in range(n):
        blink_once(on_ms, off_ms)

def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

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
        elif c == "%" and i+2 < len(s):
            try:
                out += chr(int(s[i+1:i+3], 16)); i += 2
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

def try_connect_wifi(ssid, pw):
    global wlan
    if not ssid or not pw:
        return False
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
    try:
        wlan.disconnect()
    except Exception:
        pass
    wlan.active(False); time.sleep(1)
    wlan.active(True); time.sleep(1)
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
    global wlan
    if wlan is None or (not wlan.isconnected()):
        return connect_wifi_from_config()
    return True

def start_config_portal():
    sta = network.WLAN(network.STA_IF); sta.active(False)
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
    s.bind(addr); s.listen(1)
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
                cl.send(HTML_SAVED.encode()); cl.close()
                time.sleep(3); machine.reset()
            else:
                cl.send(HTML_FORM.encode()); cl.close()
        else:
            cl.send(HTML_FORM.encode()); cl.close()

def startup_wifi_or_portal():
    if connect_wifi_from_config():
        return True
    start_config_portal()
    return False

def mqtt_connect():
    global client
    try:
        client = MQTTClient(MQTT_CLIENTID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
        client.connect()
        print("✅ MQTT 연결 완료 (broker =", MQTT_BROKER, ")")
        blink_n(5)
        return True
    except Exception as e:
        print("❌ MQTT 연결 실패:", e)
        client = None
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
    global client
    backoff = 0.5
    for attempt in range(MQTT_RECONNECT_MAX):
        print("🔁 MQTT 재연결 시도", attempt + 1)
        ok_wifi = wifi_ensure()
        if not ok_wifi:
            time.sleep(backoff); backoff = min(backoff * 2, 5); continue
        try:
            if client is not None:
                try: client.disconnect()
                except Exception: pass
            if mqtt_connect():
                return True
        except Exception as e:
            print("❌ MQTT 재연결 중 예외:", e)
        time.sleep(backoff); backoff = min(backoff * 2, 5)
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
            wlan.active(False); time.sleep(1)
            wlan.active(True); time.sleep(1)
    except Exception:
        pass
    time.sleep(2); machine.reset()

def publish_json(topic, obj):
    global client
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()
    backoff = 0.5
    for _ in range(4):
        if client is None:
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff); backoff = min(backoff * 2, 5); continue
        try:
            client.publish(topic, msg)
            return True
        except Exception:
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff); backoff = min(backoff * 2, 5)
    return False

def is_water_high():
    cnt = 0
    for _ in range(DEBOUNCE_SAMPLES):
        if water_switch.value() == 0:
            cnt += 1
        time.sleep_ms(5)
    return cnt >= DEBOUNCE_MIN_HIGH

def send_water_alert():
    payload = {
        "sensor_id": MQTT_CLIENTID,
        "event": "water_detected",
        "status": "ABNORMAL",
        "value": 1,
        "timestamp": now_str()
    }
    blink_once(40, 40)
    print("📤 수위 센서 전송:", payload)
    publish_json(MQTT_TOPIC, payload)

def main():
    global recovery_fail_count
    set_led(False); blink_n(2)
    time.sleep_ms(BOOT_SETTLE_MS)

    startup_wifi_or_portal()
    while not mqtt_connect():
        time.sleep(5)

    prev_state = None
    t_ping = time.ticks_ms()
    t_hb = time.ticks_ms()
    hb_on = False

    while True:
        now = time.ticks_ms()

        if time.ticks_diff(now, t_hb) >= 1000:
            hb_on = not hb_on
            set_led(True if prev_state == "ABNORMAL" else hb_on)
            t_hb = now

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            ok_wifi = wifi_ensure()
            ok_mqtt = mqtt_ping() if ok_wifi else False
            if (not ok_wifi) or (not ok_mqtt):
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    blink_once(200, 200)
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("water wifi/mqtt stuck")
                else:
                    recovery_fail_count = 0
            else:
                recovery_fail_count = 0
            t_ping = now

        state = "ABNORMAL" if is_water_high() else "NORMAL"
        if state != prev_state:
            if state == "ABNORMAL":
                send_water_alert()
                set_led(True)
            else:
                print("✅ 정상 수위입니다.")
            prev_state = state

        time.sleep_ms(CHECK_INTERVAL_MS)

main()
