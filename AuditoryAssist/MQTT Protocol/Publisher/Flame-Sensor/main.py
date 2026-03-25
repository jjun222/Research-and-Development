import machine, time, network, ujson
import socket, os
from simple import MQTTClient  # umqtt.simple

# ========= 하드웨어 설정 =========
FIRE_SENSOR_PIN = 15
fire_sensor = machine.Pin(FIRE_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_UP)  # 0이면 감지

# ========= Wi-Fi 기본 정보 =========
# 비워두면 wifi_config.json이 없을 때 바로 AP 설정 모드로 진입
WIFI_SSID = ''
WIFI_PASSWORD = ''

# ========= Wi-Fi / MQTT 설정 파일 =========
CONFIG_PATH        = "wifi_config.json"
DEFAULT_BROKER_IP  = "192.168.0.24"

# ========= MQTT 정보 =========
MQTT_BROKER    = DEFAULT_BROKER_IP   # config에 따라 변경될 수 있음
MQTT_TOPIC     = 'shz/sensor'
MQTT_CLIENT_ID = 'shz_sensor_pico'

# ========= 동작 파라미터 =========
KEEPALIVE_SEC      = 60
PING_INTERVAL_MS   = 30_000     # 30초마다 ping/헬스체크
FIRE_HOLDOFF_MS    = 15_000     # 감지 후 정상 전송까지 대기
WIFI_RETRY_MAX     = 15         # Wi-Fi 연결/재연결 최대 시도 횟수
MQTT_RECONNECT_MAX = 15         # MQTT 재연결 최대 시도 횟수

# ========= 내부 상태 =========
wlan   = None
client = None

# ========= AP 모드 (설정 포털) =========
AP_SSID = "shz_sensor_setup"
AP_PW   = "123456789"  # 8글자 이상

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
  Broker IP: <input name="broker" value="192.168.0.24"><br>
  <button type="submit">저장</button>
</form>
</body>
</html>
"""

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

# --- wifi_config.json load/save ---
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

# --- URL 디코딩 & 폼 파싱 (AP 포털용) ---
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
                except:
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

# ========= Wi-Fi 관련 =========
def try_connect_wifi(ssid, pw):
    """
    주어진 SSID/PW로 Wi-Fi 연결 시도.
    성공 시 True, 실패 시 False.
    """
    global wlan
    if not ssid or not pw:
        print("⚠️ SSID 또는 PW 없음, 연결 시도 생략")
        return False

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
    else:
        wlan.active(True)

    if wlan.isconnected():
        print("✅ 이미 Wi-Fi 연결 상태:", wlan.ifconfig())
        return True

    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
        attempt += 1
        time.sleep(0.5)

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패 (재시도 %d회 초과)" % WIFI_RETRY_MAX)
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    return True

def connect_wifi_from_config():
    """
    1) wifi_config.json 있으면 → 그 SSID/PW로 접속 + MQTT_BROKER 설정
    2) 없거나 실패 → 코드 상의 WIFI_SSID/WIFI_PASSWORD로 한 번 더 시도
    """
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

    # fallback: 코드 안에 박아둔 기본 SSID
    if WIFI_SSID and WIFI_PASSWORD:
        print("⚠️ config 없음/실패 → 기본 SSID 시도:", WIFI_SSID)
        if try_connect_wifi(WIFI_SSID, WIFI_PASSWORD):
            MQTT_BROKER = DEFAULT_BROKER_IP
            print("🌐 기본 설정으로 연결, broker =", MQTT_BROKER)
            return True

    return False

def wifi_connect():
    """
    기존 wifi_connect() 역할을 config 기반으로 재구현.
    """
    return connect_wifi_from_config()

def wifi_ensure():
    """
    Wi-Fi가 끊겨 있으면 다시 붙여보기.
    (재연결 시에는 AP 포털로 가지 않고, 저장된 config/기본 SSID만 사용)
    """
    if not wifi_connect():
        print("⚠️ Wi-Fi 미연결 상태, 나중에 다시 시도")

# ========= AP 설정 포털 =========
def start_config_portal():
    """
    설정용 AP를 열고, 폼에서 SSID/PW/Broker를 입력받아 저장 후 리부트.
    (Neopixel / MQ5 / MQ7과 동일 구조)
    """
    # STA 끄고 AP 켜기
    sta = network.WLAN(network.STA_IF)
    sta.active(False)

    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PW)
    ap.active(True)
    print("📶 AP 모드 시작:", ap.ifconfig())
    print("➡ 폰에서", AP_SSID, "접속 후 브라우저에서 http://192.168.4.1 열기")

    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
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
                cl.send(HTML_SAVED)
                cl.close()
                time.sleep(3)
                machine.reset()
            else:
                cl.send(HTML_FORM)
                cl.close()
        else:
            cl.send(HTML_FORM)
            cl.close()

def startup_wifi_or_portal():
    """
    부팅 시 한 번만 호출:
    - wifi_config / 기본 SSID로 Wi-Fi 연결을 먼저 시도하고,
    - 실패하면 AP 포털로 진입해서 사용자 입력을 기다렸다가 재부팅.
    """
    if wifi_connect():
        return True
    print("⚠️ Wi-Fi 접속 실패 → 설정용 AP 모드 진입")
    start_config_portal()
    return False

# ========= MQTT 관련 =========
def mqtt_connect():
    """
    MQTT 브로커 연결.
    성공 시 True, 실패 시 False.
    """
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
    """
    ping으로 세션 유지. 실패하거나 client가 없으면 False.
    """
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
    """
    MQTT 재연결을 지수 백오프로 시도.
    최대 MQTT_RECONNECT_MAX회 시도 후 포기.
    """
    global client
    backoff = 0.5
    for attempt in range(MQTT_RECONNECT_MAX):
        print("🔁 MQTT 재연결 시도", attempt + 1)
        wifi_ensure()

        try:
            if client is not None:
                try:
                    client.disconnect()
                except:
                    pass

            if mqtt_connect():
                print("✅ MQTT 재연결 성공")
                return True

        except Exception as e:
            print("❌ MQTT 재연결 중 예외:", e)

        time.sleep(backoff)
        backoff = min(backoff * 2, 5)

    print("🚫 MQTT 재연결 포기 (이번 사이클)")
    return False

def publish_json(topic, obj):
    """
    JSON payload를 안전하게 발행.
    - 실패 시 MQTT 재연결 시도
    - 여러 번 안 되면 메시지 드롭하고 리턴
    """
    global client
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5
    for attempt in range(4):
        if client is None:
            print("⚠️ MQTT 클라이언트 없음, 재연결 시도")
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

    print("🚫 publish 포기 (메시지 드롭)")
    return False

def send_status(value_str):
    """
    value_str: "감지됨" 또는 "정상"
    """
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "shz_detected",
        "value": value_str,
        "timestamp": get_timestamp_string()
    }
    print(f"📤 상태 전송: {value_str}")
    publish_json(MQTT_TOPIC, payload)

# ========= 메인 루프 =========
def main():
    # 1) 부팅 시 한 번: Wi-Fi / 브로커 설정 or AP 포털
    startup_wifi_or_portal()

    # 2) 초기 MQTT 연결 (성공할 때까지 재시도)
    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    print("📍 SHZ 센서 모니터링 시작 (자동 복구 모드)")

    is_fire_waiting = False
    fire_detected_time = 0
    t_ping = time.ticks_ms()

    while True:
        now = time.ticks_ms()

        # 주기적 Wi-Fi / MQTT 헬스체크
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        sensor_val = fire_sensor.value()  # 0이면 감지됨

        # 🔥 불꽃 감지 이벤트
        if (not is_fire_waiting) and sensor_val == 0:
            send_status("감지됨")
            is_fire_waiting = True
            fire_detected_time = now
            print("🔥 SHZ 감지 → 대기 시작")

        # ⏳ 15초 후 정상 복귀 메시지 한 번
        if is_fire_waiting and time.ticks_diff(now, fire_detected_time) > FIRE_HOLDOFF_MS:
            send_status("정상")
            is_fire_waiting = False
            print("🔄 SHZ 정상 상태 복귀")

        time.sleep(0.1)

# 자동 실행
main()
