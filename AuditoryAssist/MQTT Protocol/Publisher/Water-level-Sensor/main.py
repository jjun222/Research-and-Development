import machine, time, network, ujson
import socket, os
from umqtt.simple import MQTTClient   # 버튼 센서와 동일 스타일 사용

# ========= 하드웨어/네트워크 설정 =========
WATER_PIN = 5
water_switch = machine.Pin(WATER_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
# water_switch.value() == 0 : 스위치 닫힘(물 높음)

# 상태 확인용 내장 LED (GP25, 이름 "LED")
HEARTBEAT_LED = machine.Pin("LED", machine.Pin.OUT)
HEARTBEAT_LED.value(0)

# (기본값) Wi-Fi
# 비워두면 wifi_config.json이 없을 때 바로 AP 설정 모드로 진입
WIFI_SSID = ''
WIFI_PASSWORD = ''

# Wi-Fi / MQTT 설정 파일
CONFIG_PATH        = "wifi_config.json"
DEFAULT_BROKER_IP  = "192.168.0.24"

# MQTT
MQTT_BROKER   = DEFAULT_BROKER_IP      # config에 따라 변경됨
MQTT_TOPIC    = 'water_level/sensor'   # ✅ MQTT_config.json 기준
MQTT_CLIENTID = 'water_level_1'        # ✅ sensor_id와 동일하게

# 타이밍 / 재연결 파라미터
KEEPALIVE_SEC      = 60                # MQTT keepalive
PING_INTERVAL_MS   = 30_000            # 30초마다 ping
CHECK_INTERVAL_MS  = 500               # 수위 체크 주기 (0.5초)
DEBOUNCE_SAMPLES   = 10                # 다수결 샘플 수
DEBOUNCE_MIN_HIGH  = 7                 # 10번중 7번 이상이면 '물 높음'

WIFI_RETRY_MAX     = 15                # Wi-Fi 연결/재연결 최대 시도 횟수
MQTT_RECONNECT_MAX = 15                # MQTT 재연결 최대 시도 횟수

# 내부 상태
wlan   = None
client = None

# ========= AP 모드 (설정 포털) =========
AP_SSID = "water_level_setup"
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
def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

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
    """기존 wifi_connect() 역할을 config 기반으로 재구현."""
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
    MQTT 브로커에 연결.
    성공 시 True, 실패 시 False.
    """
    global client
    try:
        client = MQTTClient(MQTT_CLIENTID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
        client.connect()
        print("✅ MQTT 연결 완료 (broker =", MQTT_BROKER, ")")
        return True
    except Exception as e:
        print("❌ MQTT 연결 실패:", e)
        client = None
        return False

def mqtt_ping():
    """
    가능하면 ping으로 세션 유지.
    실패하거나 client가 없으면 False.
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
    브로커 재연결을 지수 백오프로 시도.
    최대 MQTT_RECONNECT_MAX회 시도 후 포기.
    실패 시 보드를 리셋해서 완전히 다시 시작.
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

    print("🚫 MQTT 재연결 포기 (이번 사이클) → 보드 리셋")
    time.sleep(1)
    machine.reset()
    return False  # 이 줄은 이론상 도달하지 않지만 형식상 추가

def publish_json(topic, obj):
    """
    JSON 안전 발행. 실패 시 자동 재연결 후 재시도.
    여러 번 안 되면 메시지 드롭하고 리턴.
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

# ========= 수위 판단 =========
def is_water_high():
    """
    스위치 값 다수결로 판단.
    water_switch.value() == 0 : 물 높음(스위치 닫힘)
    """
    cnt = 0
    for _ in range(DEBOUNCE_SAMPLES):
        if water_switch.value() == 0:   # 닫힘(물 높음)
            cnt += 1
        time.sleep_ms(5)
    return cnt >= DEBOUNCE_MIN_HIGH

def send_water_alert():
    """
    물 높음(비정상) 상태일 때만 판단 서버로 이벤트 전송.
    MQTT_config.json + handlers.py에 맞춘 payload 형태:
      sensor_id = "water_level_1"
      event     = "water_detected"
    """
    payload = {
        "sensor_id": MQTT_CLIENTID,    # "water_level_1"
        "event": "water_detected",     # ✅ MQTT_config.json expected_event
        "status": "ABNORMAL",          # 참고용 (서버는 현재 status 안 씀)
        "value": 1,                    # 1 = 물 높음
        "timestamp": now_str()
    }
    print("📤 수위 센서 전송 (물 높음):", payload)
    publish_json(MQTT_TOPIC, payload)

# ========= 메인 =========
def main():
    # 1) 부팅 시 한 번: Wi-Fi / 브로커 설정 or AP 포털
    startup_wifi_or_portal()

    # 2) 초기 MQTT 연결 (성공할 때까지 재시도)
    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    print("📍 수위 센서 모니터링 시작 (자동 복구 모드)")

    prev_state = None  # None / "NORMAL" / "ABNORMAL"
    t_ping = time.ticks_ms()
    t_hb   = time.ticks_ms()
    hb_on  = False

    while True:
        now = time.ticks_ms()

        # 0) 생존 확인용 LED (1초마다 토글)
        if time.ticks_diff(now, t_hb) >= 1000:
            hb_on = not hb_on
            HEARTBEAT_LED.value(hb_on)
            t_hb = now

        # 1) 주기적 Wi-Fi / MQTT 헬스체크
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        # 2) 수위 읽기 + 상태 결정
        if is_water_high():
            state = "ABNORMAL"   # 물 너무 높음
        else:
            state = "NORMAL"     # 정상 수위

        # 3) 상태가 변했을 때만 처리
        if state != prev_state:
            if state == "ABNORMAL":
                print("⚠ 정상 수위를 벗어났습니다.")
                # 👉 이때만 MQTT 판단 서버에 water_detected 이벤트 전송
                send_water_alert()
            else:
                print("✅ 정상 수위입니다. (MQTT 전송 없음)")
                # 필요하면 여기서 "정상" 상태도 서버로 보내도록 확장 가능

            prev_state = state

        time.sleep_ms(CHECK_INTERVAL_MS)

# 자동 실행
main()

