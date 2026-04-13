# main.py — Raspberry Pi Pico W (MicroPython)
# Vibrator subscriber for ALL-TRUE fire alert
# Topics:
#   - vibrator/<DEVICE_ID>
#   - vibrator/broadcast
#
# Commands:
#   - {"command":"vibrate_fire_alert","duration_ms":10000,"on_ms":400,"off_ms":200,"intensity":0.85}
#   - {"command":"vibrate_once","ms":800,"intensity":0.8}
#   - {"command":"vibrate_stop"}

import sys, time, ubinascii, machine, socket, os

# --- Pico W 전용 MicroPython 확인
try:
    import network
except ImportError:
    raise OSError(
        "이 보드에는 'network' 모듈이 없습니다.\n"
        "Pico W 전용 MicroPython 펌웨어를 설치하세요."
    )

from micropython import const
from machine import Pin, PWM
import ujson

# MQTT 라이브러리: robust 우선, simple 폴백
try:
    from umqtt.robust import MQTTClient
except Exception:
    try:
        from umqtt.simple import MQTTClient
    except Exception:
        raise OSError(
            "umqtt 라이브러리를 찾을 수 없습니다.\n"
            "REPL에서 설치:\n"
            "  import mip; mip.install('umqtt.simple'); mip.install('umqtt.robust')"
        )

# =========================================================
# 기본 설정
# =========================================================
WIFI_SSID     = ""
WIFI_PASSWORD = ""

CONFIG_PATH       = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

MQTT_BROKER = DEFAULT_BROKER_IP
PORT        = const(1883)
KEEPALIVE   = const(30)
DEVICE_ID   = "Vibrator_1"

CLIENT_ID = b"PICO_" + ubinascii.hexlify(machine.unique_id())
SUB_TOPICS = (
    b"vibrator/%s" % DEVICE_ID.encode(),
    b"vibrator/broadcast",
)

WIFI_RETRY_MAX         = const(20)
WIFI_RETRY_WAIT_MS     = const(500)
BOOT_SETTLE_MS         = const(2500)   # 외부전원 단독 부팅 안정화 대기
BROKER_PROBE_TIMEOUT_S = const(2)
MAIN_LOOP_SLEEP_MS     = const(5)
RECONNECT_DELAY_MS     = const(2000)

# =========================================================
# AP 모드(설정 포털)
# =========================================================
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
<p>설정이 저장되었습니다. 3초 후 재부팅합니다.</p>
</body></html>
"""

# =========================================================
# LED 상태 표시
# =========================================================
try:
    LED = Pin("LED", Pin.OUT)
except Exception:
    LED = None

def led_on():
    if LED:
        LED.value(1)

def led_off():
    if LED:
        LED.value(0)

def led_blink(times=1, on_ms=120, off_ms=120):
    if not LED:
        return
    for _ in range(times):
        LED.value(1)
        time.sleep_ms(on_ms)
        LED.value(0)
        time.sleep_ms(off_ms)

# =========================================================
# 핀 매핑 & PWM
# =========================================================
PWM_FREQ = const(1000)  # 1kHz

ENA = PWM(Pin(15))   # 모터 A
IN1 = Pin(14, Pin.OUT)
IN2 = Pin(13, Pin.OUT)

ENB = PWM(Pin(12))   # 모터 B
IN3 = Pin(11, Pin.OUT)
IN4 = Pin(10, Pin.OUT)

ENA.freq(PWM_FREQ)
ENB.freq(PWM_FREQ)

# =========================================================
# 모터 제어
# =========================================================
def set_motor_forward():
    IN1.value(1); IN2.value(0)
    IN3.value(1); IN4.value(0)

def set_power_ratio(ratio):
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

# =========================================================
# 진동 패턴 상태(FSM, 논블로킹)
# =========================================================
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
        "next_toggle_ms": now,
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

    if time.ticks_diff(_state["end_ms"], now) <= 0:
        stop_pattern()
        return

    if time.ticks_diff(now, _state["next_toggle_ms"]) >= 0:
        if _state["vibrating"]:
            set_power_ratio(0.0)
            _state["vibrating"] = False
            _state["next_toggle_ms"] = time.ticks_add(now, _state["off_ms"])
        else:
            set_motor_forward()
            set_power_ratio(_state["intensity"])
            _state["vibrating"] = True
            _state["next_toggle_ms"] = time.ticks_add(now, _state["on_ms"])

# =========================================================
# wifi_config.json 유틸
# =========================================================
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

# =========================================================
# Wi-Fi / MQTT
# =========================================================
wlan   = None
client = None

def radio_reset():
    global wlan

    try:
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
    except Exception:
        pass

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    try:
        wlan.disconnect()
    except Exception:
        pass

    try:
        wlan.active(False)
    except Exception:
        pass

    time.sleep_ms(300)

    try:
        wlan.active(True)
    except Exception:
        pass

    time.sleep_ms(500)

def wifi_is_connected():
    global wlan
    return wlan is not None and wlan.active() and wlan.isconnected()

def try_connect_wifi(ssid, pw, force_reset=True):
    global wlan

    if not ssid or not pw:
        print("⚠️ SSID 또는 PW 없음, 연결 시도 생략")
        return False

    if force_reset:
        radio_reset()
    else:
        if wlan is None:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)

    if wifi_is_connected():
        print("✅ 이미 Wi-Fi 연결 상태:", wlan.ifconfig())
        return True

    print("📡 Wi-Fi 연결 시도:", ssid)
    try:
        wlan.connect(ssid, pw)
    except Exception as e:
        print("❌ wlan.connect 실패:", e)
        return False

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
        attempt += 1
        time.sleep_ms(WIFI_RETRY_WAIT_MS)

    if not wlan.isconnected():
        try:
            st = wlan.status()
        except Exception:
            st = "unknown"
        print("❌ Wi-Fi 연결 실패 (status=%s)" % str(st))
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    led_blink(3, 80, 80)   # Wi-Fi 성공
    return True

def connect_wifi_from_config(force_reset=True):
    global MQTT_BROKER

    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid")
        pw   = cfg.get("password")
        broker = cfg.get("broker") or DEFAULT_BROKER_IP

        if ssid and pw:
            MQTT_BROKER = broker
            if try_connect_wifi(ssid, pw, force_reset=force_reset):
                print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
                return True

    if WIFI_SSID and WIFI_PASSWORD:
        MQTT_BROKER = DEFAULT_BROKER_IP
        print("⚠️ config 없음/실패 → 기본 SSID 시도:", WIFI_SSID)
        if try_connect_wifi(WIFI_SSID, WIFI_PASSWORD, force_reset=force_reset):
            print("🌐 기본 설정으로 연결, broker =", MQTT_BROKER)
            return True

    return False

def wifi_connect():
    return connect_wifi_from_config(force_reset=False)

def wifi_ensure():
    if wifi_is_connected():
        return True
    return connect_wifi_from_config(force_reset=True)

def probe_broker(ip, port=1883, timeout_s=2):
    s = None
    try:
        addr = socket.getaddrinfo(ip, port)[0][-1]
        s = socket.socket()
        s.settimeout(timeout_s)
        s.connect(addr)
        return True
    except Exception as e:
        print("⚠️ broker reachability check 실패:", e)
        return False
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass

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
    if connect_wifi_from_config(force_reset=True):
        return True

    print("⚠️ Wi-Fi 접속 실패 → 설정용 AP 모드 진입")
    start_config_portal()
    return False

# =========================================================
# MQTT 관련
# =========================================================
def on_msg(topic, msg):
    led_blink(1, 40, 40)   # 명령 수신 표시

    try:
        s = msg.decode() if isinstance(msg, (bytes, bytearray)) else str(msg)
        payload = ujson.loads(s) if s and s[0] in "{[" else {}
    except Exception as e:
        print("JSON parse error:", e, msg)
        return

    cmd = str(payload.get("command", "")).strip()

    if cmd == "vibrate_fire_alert":
        dur   = int(payload.get("duration_ms", 10000))
        on_ms = int(payload.get("on_ms", 400))
        off_ms = int(payload.get("off_ms", 200))
        inten = float(payload.get("intensity", 0.85))
        print("[VIB] fire_alert start:", dur, on_ms, off_ms, inten)
        start_fire_alert(dur, on_ms, off_ms, inten)

    elif cmd == "vibrate_once":
        ms = int(payload.get("ms", 800))
        inten = float(payload.get("intensity", 0.8))
        print("[VIB] once:", ms, inten)
        start_fire_alert(ms, ms, 9999999, inten)

    elif cmd == "vibrate_stop":
        print("[VIB] stop")
        stop_pattern()

    else:
        print("[VIB] unknown cmd:", cmd, payload)

def mqtt_connect_and_sub():
    global client

    if not probe_broker(MQTT_BROKER, PORT, BROKER_PROBE_TIMEOUT_S):
        raise OSError("broker unreachable: %s:%d" % (MQTT_BROKER, PORT))

    c = MQTTClient(CLIENT_ID, MQTT_BROKER, port=PORT, keepalive=KEEPALIVE)
    c.set_callback(on_msg)
    c.connect()

    for t in SUB_TOPICS:
        c.subscribe(t, qos=1)
        print("SUB:", t)

    client = c
    return c

# =========================================================
# 메인 루프
# =========================================================
def main():
    global client

    stop_all()   # 부팅 직후 모터 완전 OFF
    led_off()
    led_blink(2, 100, 100)   # 부팅 시작

    # 외부전원 단독 부팅 안정화 대기
    time.sleep_ms(BOOT_SETTLE_MS)

    # Wi-Fi 연결 또는 AP 포털
    startup_wifi_or_portal()

    last_ping = ticks_ms()

    while True:
        try:
            if not wifi_ensure():
                stop_pattern()
                client = None
                time.sleep_ms(RECONNECT_DELAY_MS)
                continue

            if client is None:
                print("📡 MQTT 연결 시도:", MQTT_BROKER)
                client = mqtt_connect_and_sub()
                print("✅ MQTT 연결 완료. broker =", MQTT_BROKER)
                led_blink(5, 80, 80)   # MQTT 성공
                last_ping = ticks_ms()

            client.check_msg()
            pattern_tick()

            if time.ticks_diff(ticks_ms(), last_ping) > (KEEPALIVE * 500):
                client.ping()
                last_ping = ticks_ms()

            time.sleep_ms(MAIN_LOOP_SLEEP_MS)

        except Exception as e:
            print("MQTT loop err:", e)
            try:
                if client:
                    client.disconnect()
            except Exception:
                pass
            client = None
            stop_pattern()
            led_blink(1, 200, 200)   # 에러/재시도 표시
            time.sleep_ms(RECONNECT_DELAY_MS)

try:
    main()
except KeyboardInterrupt:
    stop_all()
    led_off()
