import machine
import time
import network
import socket
import os
from umqtt.simple import MQTTClient
import ujson

# ── Wi-Fi 기본값 / 설정 파일 ────────────────────────────────────────────────
# 비워두면 wifi_config.json이 없을 때 바로 AP 설정 모드로 진입
WIFI_SSID     = ''
WIFI_PASSWORD = ''

CONFIG_PATH       = "wifi_config.json"    # 플래시에 저장
DEFAULT_BROKER_IP = "192.168.0.24"

# ── MQTT ─────────────────────────────────────────────────────────────────────
MQTT_BROKER    = DEFAULT_BROKER_IP
MQTT_TOPIC     = 'doorbell/sensor'      # 판단서버 config.json 기준
MQTT_CLIENT_ID = "doorbell_1"

# ── GPIO / 버튼 ─────────────────────────────────────────────────────────────
BUTTON_PIN   = 1                       # GPIO 1번
DEBOUNCE_MS  = 300                     # 디바운스 시간

button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
# 배선: GPIO1 ↔ 버튼 ↔ GND
# PULL_UP이므로 평소 HIGH(1), 누르면 GND로 FALLING(0)

# ── 동작/재연결 파라미터 ────────────────────────────────────────────────────
KEEPALIVE_SEC      = 60                # MQTT keepalive
PING_INTERVAL_MS   = 30_000            # 30초마다 ping/헬스체크
WIFI_RETRY_MAX     = 15                # Wi-Fi 연결/재연결 최대 시도 횟수
MQTT_RECONNECT_MAX = 15                # MQTT 재연결 최대 시도 횟수

# ── AP 모드 (설정 포털) ─────────────────────────────────────────────────────
AP_SSID = "doorbell_setup"
AP_PW   = "123456789"  # 8자 이상

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

# ── 상태 플래그 & 전역 네트워크 상태 ────────────────────────────────────────
_last_press_ms = 0
_press_flag    = False

wlan   = None
client = None

# ── 유틸 ─────────────────────────────────────────────────────────────────────
def get_timestamp_string():
    now = time.localtime()
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*now)

# wifi_config.json load/save
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

# URL 디코딩 & 폼 파싱 (AP 포털용)
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

# ── Wi-Fi 관련 (config 기반) ────────────────────────────────────────────────
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
    기존 wifi_connect()를 config 기반으로 재구현.
    """
    return connect_wifi_from_config()

def wifi_ensure():
    """
    Wi-Fi가 끊겨 있으면 다시 붙여보기.
    (재연결 시에는 AP 포털로 가지 않고, 저장된 config/기본 SSID만 사용)
    """
    if not wifi_connect():
        print("⚠️ Wi-Fi 미연결 상태, 나중에 다시 시도")

# ── AP 설정 포털 ────────────────────────────────────────────────────────────
def start_config_portal():
    """
    설정용 AP를 열고, 폼에서 SSID/PW/Broker를 입력받아 저장 후 리부트.
    (Neopixel / MQ5 / MQ7 / SHZ / 수위센서와 동일 구조)
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

# ── MQTT 관련 ───────────────────────────────────────────────────────────────
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
            print(f"📤 전송 완료 → {topic}: {obj}")
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e)
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)

    print("🚫 publish 포기 (메시지 드롭)")
    return False

def send_status(value):
    """value: 1=버튼 눌림 이벤트, 0=정상(or 기타)"""
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "button_pressed" if value == 1 else "normal",
        "value": value,
        "timestamp": get_timestamp_string()
    }
    publish_json(MQTT_TOPIC, payload)

# ── IRQ 콜백: 가볍게(플래그만 세움) ────────────────────────────────────────
def _button_irq_handler(pin):
    global _last_press_ms, _press_flag
    now = time.ticks_ms()
    # 최소 간격(디바운스)
    if time.ticks_diff(now, _last_press_ms) < DEBOUNCE_MS:
        return
    _press_flag = True
    _last_press_ms = now

# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    global _press_flag

    # 1) 부팅 시 한 번: Wi-Fi / 브로커 설정 or AP 포털
    startup_wifi_or_portal()

    # 2) 초기 MQTT 연결 (성공할 때까지 재시도)
    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    # 버튼: FALLING(1→0)에서 눌림 감지
    button.irq(trigger=machine.Pin.IRQ_FALLING, handler=_button_irq_handler)

    print("🔔 버튼 대기 중... (GPIO 1, PULL_UP)")

    t_ping = time.ticks_ms()

    while True:
        now = time.ticks_ms()

        # 주기적 Wi-Fi / MQTT 헬스체크
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        try:
            if _press_flag:
                _press_flag = False
                # 노이즈 억제용 소량 지연 후 실제 값 재확인
                time.sleep_ms(25)
                if button.value() == 0:  # 여전히 LOW이면 진짜 눌림
                    print("🔔 버튼 눌림 확정!")
                    send_status(1)  # 1 = 버튼 눌림 이벤트
            time.sleep_ms(20)
        except KeyboardInterrupt:
            print("\n🛑 종료")
            break
        except Exception as e:
            print("⚠️ 루프 오류:", e)
            time.sleep_ms(200)

# 실행
main()
