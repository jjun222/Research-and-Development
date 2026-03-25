# main.py — Raspberry Pi Pico W (MicroPython)
# Vibrator subscriber for ALL-TRUE fire alert
# Topics:
#   - vibrator/<DEVICE_ID>
#   - vibrator/broadcast   (optional)
#
# Commands:
#   - {"command":"vibrate_fire_alert","duration_ms":10000,"on_ms":400,"off_ms":200,"intensity":0.85}
#   - {"command":"vibrate_once","ms":800,"intensity":0.8}
#   - {"command":"vibrate_stop"}

import sys, time, ubinascii, machine, socket, os

# --- 반드시 Pico W 전용 MicroPython이어야 합니다. (network 모듈 확인)
try:
    import network
except ImportError:
    raise OSError(
        "이 보드에는 'network' 모듈이 없습니다.\n"
        "Pico W 전용 MicroPython 펌웨어를 Thonny에서 설치하세요.\n"
        "도구 > 옵션 > 인터프리터 > MicroPython 설치/업데이트 > 장치: Raspberry Pi Pico W"
    )

from micropython import const
from machine import Pin, PWM
import ujson  # wifi_config.json 저장/로드용

# MQTT 라이브러리: robust 우선, simple로 폴백
try:
    from umqtt.robust import MQTTClient
except Exception:
    try:
        from umqtt.simple import MQTTClient
    except Exception:
        raise OSError(
            "umqtt 라이브러리를 찾을 수 없습니다.\n"
            "REPL에서 설치하세요:\n"
            "  import mip; mip.install('umqtt.simple'); mip.install('umqtt.robust')\n"
            "또는 /umqtt/simple.py, /umqtt/robust.py 파일을 보드에 복사하세요."
        )

# ====== Wi-Fi / MQTT 기본 설정 + 설정 파일 ======
# 비워두면 wifi_config.json이 없을 때 바로 AP 설정 모드로 진입
WIFI_SSID     = ""
WIFI_PASSWORD = ""

CONFIG_PATH       = "wifi_config.json"     # 플래시에 저장
DEFAULT_BROKER_IP = "192.168.0.24"

MQTT_BROKER = DEFAULT_BROKER_IP
PORT        = const(1883)
KEEPALIVE   = const(30)
DEVICE_ID   = "Vibrator_1"

CLIENT_ID = b"PICO_" + ubinascii.hexlify(machine.unique_id())
SUB_TOPICS = (
    b"vibrator/%s" % DEVICE_ID.encode(),
    b"vibrator/broadcast",
)

# Wi-Fi 재시도 횟수
WIFI_RETRY_MAX = const(15)

# ====== AP 모드(설정 포털) ======
AP_SSID = "vibrator_setup"
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

# ====== 핀 매핑 & PWM ======
PWM_FREQ = const(1000)  # 1 kHz

ENA = PWM(Pin(15))   # 모터 A
IN1 = Pin(14, Pin.OUT)
IN2 = Pin(13, Pin.OUT)

ENB = PWM(Pin(12))   # 모터 B
IN3 = Pin(11, Pin.OUT)
IN4 = Pin(10, Pin.OUT)

ENA.freq(PWM_FREQ)
ENB.freq(PWM_FREQ)

# ====== 모터 제어 ======
def set_motor_forward():
    IN1.value(1); IN2.value(0)
    IN3.value(1); IN4.value(0)

def set_power_ratio(ratio: float):
    # ratio: 0.0~1.0 → duty_u16: 0~65535
    if ratio <= 0.0:
        duty = 0
    elif ratio >= 1.0:
        duty = 65535
    else:
        duty = int(65535 * ratio)
    ENA.duty_u16(duty)
    ENB.duty_u16(duty)

def stop_all():
    set_power_ratio(0.0)
    IN1.value(0); IN2.value(0)
    IN3.value(0); IN4.value(0)

# ====== 진동 패턴 상태(FSM, 논블로킹) ======
_state = {
    "active": False,
    "end_ms": 0,
    "on_ms": 300,
    "off_ms": 300,
    "next_toggle_ms": 0,
    "vibrating": False,
    "intensity": 0.8,
}

def ticks_ms():
    return time.ticks_ms()

def start_fire_alert(duration_ms=10000, on_ms=400, off_ms=200, intensity=0.85):
    set_motor_forward()
    now = ticks_ms()
    _state.update({
        "active": True,
        "end_ms": time.ticks_add(now, int(duration_ms)),
        "on_ms": int(on_ms),
        "off_ms": int(off_ms),
        "next_toggle_ms": now,   # 즉시 토글 시도
        "vibrating": False,
        "intensity": max(0.0, min(1.0, float(intensity))),
    })

def stop_pattern():
    _state["active"] = False
    stop_all()

def pattern_tick():
    if not _state["active"]:
        return
    now = ticks_ms()
    # 종료 시각 도달
    if time.ticks_diff(_state["end_ms"], now) <= 0:
        stop_pattern()
        return
    # 토글 타이밍 도달?
    if time.ticks_diff(now, _state["next_toggle_ms"]) >= 0:
        if _state["vibrating"]:
            # turn OFF
            set_power_ratio(0.0)
            _state["vibrating"] = False
            _state["next_toggle_ms"] = time.ticks_add(now, _state["off_ms"])
        else:
            # turn ON
            set_motor_forward()
            set_power_ratio(_state["intensity"])
            _state["vibrating"] = True
            _state["next_toggle_ms"] = time.ticks_add(now, _state["on_ms"])

# ====== wifi_config.json 유틸 ======
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

# ====== Wi-Fi / MQTT ======
wlan   = None
client = None

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

def start_config_portal():
    """
    설정용 AP를 열고, 폼에서 SSID/PW/Broker를 입력받아 저장 후 리부트.
    (다른 센서들과 동일 구조)
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

# ====== MQTT 관련 ======
def on_msg(topic, msg):
    try:
        s = msg.decode() if isinstance(msg, (bytes, bytearray)) else str(msg)
        import ujson as json
        payload = json.loads(s) if s and s[0] in "{[" else {}
    except Exception as e:
        print("JSON parse error:", e, msg)
        return

    cmd = str(payload.get("command", "")).strip()

    if cmd == "vibrate_fire_alert":
        dur = int(payload.get("duration_ms", 10000))
        on  = int(payload.get("on_ms", 400))
        off = int(payload.get("off_ms", 200))
        inten = float(payload.get("intensity", 0.85))
        print("[VIB] fire_alert start:", dur, on, off, inten)
        start_fire_alert(dur, on, off, inten)

    elif cmd == "vibrate_once":
        ms = int(payload.get("ms", 800))
        inten = float(payload.get("intensity", 0.8))
        print("[VIB] once:", ms, inten)
        # 한 번 켰다가 자연 종료 (off 구간을 매우 길게)
        start_fire_alert(ms, ms, 9999999, inten)

    elif cmd == "vibrate_stop":
        print("[VIB] stop")
        stop_pattern()

    else:
        print("[VIB] unknown cmd:", cmd, payload)

def mqtt_connect_and_sub():
    global client
    c = MQTTClient(CLIENT_ID, MQTT_BROKER, port=PORT, keepalive=KEEPALIVE)
    c.set_callback(on_msg)
    c.connect()
    for t in SUB_TOPICS:
        c.subscribe(t, qos=1)
        print("SUB:", t)
    client = c
    return c

# ====== 메인 루프 ======
def main():
    global client

    stop_all()  # 안전 초기화

    # 1) 부팅 시: Wi-Fi / MQTT 브로커 설정 or AP 포털
    startup_wifi_or_portal()

    last_ping = ticks_ms()

    while True:
        try:
            # 1) Wi-Fi가 안 붙어 있으면 계속 재시도 (AP 포털로는 가지 않음)
            if not wifi_connect():
                time.sleep(2)
                continue

            # 2) MQTT 클라이언트 없으면 새로 연결
            if client is None:
                client = mqtt_connect_and_sub()
                print("MQTT: connected. broker =", MQTT_BROKER)
                last_ping = ticks_ms()

            # 3) 수신 처리(논블로킹)
            client.check_msg()

            # 4) 진동 패턴 갱신
            pattern_tick()

            # 5) ping 유지 (KEEPALIVE/2 주기)
            if time.ticks_diff(ticks_ms(), last_ping) > (KEEPALIVE * 500):
                try:
                    client.ping()
                except Exception as e:
                    print("ping err:", e)
                    # 에러 나면 아래 except 블록으로 가서 재연결
                    raise e
                last_ping = ticks_ms()

            time.sleep_ms(5)

        except Exception as e:
            print("MQTT loop err:", e)
            try:
                if client:
                    client.disconnect()
            except:
                pass
            client = None
            stop_pattern()
            time.sleep(2000)

try:
    main()
except KeyboardInterrupt:
    stop_all()
