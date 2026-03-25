# main.py (MicroPython, Pico W + L298N, MQTT 제어 + AP 기반 Wi-Fi 설정 포털)
import network, time, ubinascii, ujson as json
import socket, os
from machine import Pin
from umqtt.simple import MQTTClient

# ==== 하드웨어 핀 (L298N 예시) ====
ENA = Pin(15, Pin.OUT, value=0)  # ENA PWM 대신 ON/OFF
IN1 = Pin(16, Pin.OUT, value=0)
IN2 = Pin(17, Pin.OUT, value=0)

def beacon_on():
    IN1.value(0)
    IN2.value(0)  # 저측 싱크
    ENA.value(1)

def beacon_off():
    ENA.value(0)

# ==== 기본 설정 ====
DEVICE_ID = "Beacon_1"

# (기본값) 연구실 공유기 SSID / PW
# → 다른 장소에서 쓰기 좋게 비워두면, wifi_config.json 없을 때 AP 모드로 바로 진입
WIFI_SSID = ""
WIFI_PW   = ""

# Wi-Fi / MQTT 설정 파일
CONFIG_PATH       = "wifi_config.json"       # 플래시에 저장
DEFAULT_BROKER_IP = "192.168.0.24"           # 기본 브로커 IP

BROKER      = DEFAULT_BROKER_IP
PORT        = 1883
KEEPALIVE   = 60

# Wi-Fi 재시도 횟수 설정
WIFI_RETRY_MAX = 15  # 최대 15번까지 연결 재시도 (timeout_sec로도 제한)

# AP 모드용 (설정 포털)
AP_SSID = "Beacon_1_setup"
AP_PW   = "123456789"

HTML_FORM = """\
HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>경광등 WiFi 설정</title></head>
<body>
<h2>Wi-Fi / MQTT 설정 (Beacon_1)</h2>
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

# 토픽
TOPIC_CMD    = "beacon/%s" % DEVICE_ID
TOPIC_STATUS = "interfaceui/status/subscriber/%s" % DEVICE_ID
TOPIC_HELLO  = "interfaceui/registry/hello/%s" % DEVICE_ID
TOPIC_REQ    = "interfaceui/registry/request"
TOPIC_LOG    = "interfaceui/logs/subscriber/%s" % DEVICE_ID

# 전역 상태
client = None
wlan   = None
_EFFECT_GEN = 0

# Wi-Fi 로그 너무 많이 찍히는 것 방지용
_wifi_connected_logged = False
_wifi_config_logged    = False

# ==== 로그 유틸 ====
def log(level, msg, **extra):
    try:
        print("[{}][{}] {} {}".format(DEVICE_ID, level, msg, extra if extra else ""))
    except:
        pass
    try:
        if client:
            rec = {
                "id": DEVICE_ID,
                "type": "subscriber",
                "level": level,
                "msg": msg,
                "ts": int(time.time())
            }
            if extra:
                rec.update(extra)
            client.publish(TOPIC_LOG, json.dumps(rec))
    except:
        pass

# ==== wifi_config.json load/save ====
def load_wifi_config():
    if CONFIG_PATH not in os.listdir():
        return None
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.loads(f.read())
    except Exception as e:
        print("⚠️ config load 실패:", e)
        return None

def save_wifi_config(ssid, pw, broker_ip=None):
    cfg = {"ssid": ssid, "password": pw}
    if broker_ip:
        cfg["broker"] = broker_ip
    try:
        with open(CONFIG_PATH, "w") as f:
            f.write(json.dumps(cfg))
        print("✅ Wi-Fi 설정 저장 완료:", cfg)
    except Exception as e:
        print("❌ config 저장 실패:", e)

# ==== Wi-Fi 연결 로직 ====
def try_connect_wifi(ssid, pw, timeout_sec=20):
    """
    주어진 SSID/PW로 Wi-Fi 연결 시도.
    성공 시 True, 실패 시 False.
    """
    global wlan, _wifi_connected_logged
    if not ssid or not pw:
        print("⚠️ SSID 또는 PW 없음, 연결 시도 생략")
        return False

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
    else:
        wlan.active(True)

    # 이미 붙어있으면 로그는 한 번만
    if wlan.isconnected():
        if not _wifi_connected_logged:
            print("✅ 이미 Wi-Fi 연결 상태:", wlan.ifconfig())
            _wifi_connected_logged = True
        return True

    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)
    t0 = time.time()
    while not wlan.isconnected() and (time.time() - t0 < timeout_sec):
        time.sleep(0.5)

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패")
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    _wifi_connected_logged = True
    return True

def connect_wifi_from_config(timeout_sec=20):
    """
    1) wifi_config.json 있으면 → 그 SSID/PW로 접속 + BROKER 설정
    2) 없거나 실패 → 코드 상의 WIFI_SSID/WIFI_PW로 한 번 더 시도
    """
    global BROKER, _wifi_config_logged

    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid")
        pw   = cfg.get("password")
        if ssid and pw:
            if try_connect_wifi(ssid, pw, timeout_sec):
                broker = cfg.get("broker")
                BROKER = broker or DEFAULT_BROKER_IP
                if not _wifi_config_logged:
                    print("🌐 config로 Wi-Fi 연결 OK, broker =", BROKER)
                    _wifi_config_logged = True
                return True

    # fallback: 코드 안에 박아둔 기본 SSID
    if WIFI_SSID and WIFI_PW:
        print("⚠️ config 없음/실패 → 기본 SSID 시도:", WIFI_SSID)
        if try_connect_wifi(WIFI_SSID, WIFI_PW, timeout_sec):
            BROKER = DEFAULT_BROKER_IP
            print("🌐 기본 설정으로 연결, broker =", BROKER)
            return True

    return False

# ==== AP 설정 포털 ====
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

def start_config_portal():
    """
    설정용 AP를 열고, 폼에서 SSID/PW/Broker를 입력받아 저장 후 리부트.
    """
    # STA 끄고 AP 켜기
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
    wlan.active(False)

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
                import machine
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
    - config/기본 SSID로 Wi-Fi 연결을 먼저 시도하고,
    - 실패하면 AP 포털로 진입해서 사용자 입력을 기다렸다가 재부팅.
    """
    if connect_wifi_from_config():
        return True
    print("⚠️ Wi-Fi 접속 실패 → 설정용 AP 모드 진입")
    start_config_portal()
    return False

# ==== MQTT 유틸 ====
def publish_status(c, online=True):
    try:
        c.publish(
            TOPIC_STATUS,
            json.dumps({
                "id": DEVICE_ID,
                "name": DEVICE_ID,
                "type": "subscriber",
                "status": "online" if online else "offline",
                "ts": int(time.time())
            }),
            retain=True
        )
    except Exception as e:
        print("status publish err:", e)

def publish_hello(c):
    try:
        global wlan
        if wlan is None:
            wlan = network.WLAN(network.STA_IF)
        ip = wlan.ifconfig()[0]
        c.publish(
            TOPIC_HELLO,
            json.dumps({
                "id": DEVICE_ID,
                "ip": ip,
                "name": DEVICE_ID,
                "type": "subscriber",
                "ts": int(time.time())
            }),
            retain=True
        )
        log("info", "hello published", ip=ip)
    except Exception as e:
        print("hello publish err:", e)

def make_client():
    # STA_IF 인터페이스의 MAC 주소 사용
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
    mac = wlan.config('mac')
    cid = b"beacon-" + ubinascii.hexlify(mac)
    c = MQTTClient(cid, BROKER, port=PORT, keepalive=KEEPALIVE)
    c.set_last_will(
        TOPIC_STATUS,
        json.dumps({
            "id": DEVICE_ID,
            "name": DEVICE_ID,
            "type": "subscriber",
            "status": "offline",
            "ts": int(time.time())
        }),
        retain=True
    )
    return c

def _new_token():
    global _EFFECT_GEN
    _EFFECT_GEN = (_EFFECT_GEN + 1) & 0x7fffffff
    return _EFFECT_GEN

def _is_current(token):
    return token == _EFFECT_GEN

def _sleep_with_token(c, ms, token):
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try:
            c.check_msg()
        except:
            pass
        if not _is_current(token):
            return False
        time.sleep(0.05)
    return _is_current(token)

# ==== 메시지 처리 ====
def handle_message(c, t_b, m_b):
    topic = t_b.decode() if isinstance(t_b, bytes) else str(t_b)
    raw   = m_b.decode() if isinstance(m_b, bytes) else str(m_b)
    print("🔔", topic, "→", raw)
    try:
        data = json.loads(raw) if raw and raw[0] in "{[" else {"text": raw}
        cmd  = (data.get("command") or data.get("text", "")).strip()
    except Exception:
        log("error", "json parse failed", raw=raw)
        return

    # 레지스트리 요청 → 헬로우 재발행
    if topic == TOPIC_REQ:
        publish_hello(c)
        return

    # 화재 경보: 깜빡임 패턴 시작
    if cmd == "beacon_fire_alert":
        token = _new_token()
        duration_ms = int(data.get("duration_ms", 10000))
        on_ms  = int(data.get("on_ms", 250))
        off_ms = int(data.get("off_ms", 250))
        log("info", "beacon fire alert start",
            duration_ms=duration_ms, on_ms=on_ms, off_ms=off_ms)
        end = time.ticks_add(time.ticks_ms(), duration_ms)
        state = False
        while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
            state = not state
            if state:
                beacon_on()
            else:
                beacon_off()
            if not _sleep_with_token(c, on_ms if state else off_ms, token):
                break
        if _is_current(token):
            beacon_off()
            log("info", "beacon fire alert end")
        return

    # 즉시 정지
    if cmd == "beacon_stop":
        _new_token()    # 이후 루프 즉시 종료되도록 토큰 갱신
        beacon_off()
        log("info", "beacon stop")
        return

    log("warn", "unknown cmd", cmd=cmd)

def mqtt_connect_and_subscribe():
    global client
    print("📡 MQTT 연결 시도 중... (broker =", BROKER, ")")
    client = make_client()
    try:
        client.set_callback(lambda t, m: handle_message(client, t, m))
        client.connect()
        publish_status(client, True)
        publish_hello(client)
        client.subscribe(TOPIC_CMD, qos=1); print("📶 구독:", TOPIC_CMD)
        client.subscribe(TOPIC_REQ, qos=1); print("📶 구독:", TOPIC_REQ)
        log("info", "mqtt connected")
        return True
    except Exception as e:
        print("MQTT connect err:", e)
        try:
            client.disconnect()
        except:
            pass
        client = None
        return False

# ==== main 루프 ====
def main():
    global client
    beacon_off()  # 안전 소등

    # 1) 부팅 시 한 번: Wi-Fi / 브로커 설정 or AP 포털
    startup_wifi_or_portal()

    # 2) MQTT 연결 (성공할 때까지 재시도)
    while not mqtt_connect_and_subscribe():
        time.sleep(3)

    last_ping  = time.time()
    last_hello = time.time()

    while True:
        try:
            # 수신 메시지 처리
            client.check_msg()
            now = time.time()

            # 주기적 ping
            if now - last_ping >= KEEPALIVE // 2:
                try:
                    client.ping()
                except Exception as e:
                    raise e
                last_ping = now

            # 60초마다 hello 재발행(레지스트리 유지용)
            if now - last_hello >= 60:
                publish_hello(client)
                last_hello = now

            time.sleep(0.05)

        except Exception as e:
            print("loop err:", e)
            log("error", "mqtt loop error", error=str(e))
            time.sleep(1)

            # 재연결 시도 (Wi-Fi + MQTT)
            ok = False
            for _ in range(10):
                if connect_wifi_from_config() and mqtt_connect_and_subscribe():
                    ok = True
                    break
                time.sleep(3)

            if not ok:
                print("⚠️ 재연결 실패, 5초 후 다시 시도")
                time.sleep(5)

main()
