import time, network, machine, neopixel, ubinascii
import ujson as json
import socket, os
from umqtt.simple import MQTTClient

DEVICE_ID = "Neopixel_1"
NEO_PIN = 28
NUM_LED = 12

WIFI_SSID = ""
WIFI_PASSWORD = ""

CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

MQTT_BROKER = DEFAULT_BROKER_IP
MQTT_PORT = 1883
KEEPALIVE = 60
MAX_RECOVERY_FAILS = 8

TOPIC_CMD_THIS = "neopixel/%s" % DEVICE_ID
TOPIC_CMD_ALL = "neopixel/ALL"
TOPIC_STATUS = "interfaceui/status/subscriber/%s" % DEVICE_ID
TOPIC_HELLO = "interfaceui/registry/hello/%s" % DEVICE_ID
TOPIC_REQ = "interfaceui/registry/request"
TOPIC_LOG = "interfaceui/logs/subscriber/%s" % DEVICE_ID

DEFAULT_MOOD_RGB = (250, 248, 104)
DEFAULT_BRIGHT = 255

AP_SSID = "Neopixel_1_setup"
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
</form></body></html>
''' % DEFAULT_BROKER_IP

HTML_SAVED = '''HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>
'''

def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)

def hex_to_rgb(s):
    s = s.strip().lstrip("#")
    return (int(s[0:2],16), int(s[2:4],16), int(s[4:6],16))

def apply_brightness(rgb, br):
    r,g,b = rgb
    return (r*br//255, g*br//255, b*br//255)

np = neopixel.NeoPixel(machine.Pin(NEO_PIN), NUM_LED)
mood_rgb = DEFAULT_MOOD_RGB
mood_bright = DEFAULT_BRIGHT
client = None
wlan = None
_EFFECT_GEN = 0
recovery_fail_count = 0

def set_all(color):
    for i in range(NUM_LED):
        np[i] = color
    np.write()

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
    with open(CONFIG_PATH, "w") as f:
        f.write(json.dumps(cfg))
    print("✅ Wi-Fi 설정 저장 완료:", cfg)

def radio_reset():
    global wlan
    try:
        ap = network.WLAN(network.AP_IF); ap.active(False)
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

def try_connect_wifi(ssid, pw, timeout_sec=20, force_reset=True):
    global wlan
    if not ssid or not pw:
        return False
    if force_reset:
        radio_reset()
    elif wlan is None:
        wlan = network.WLAN(network.STA_IF); wlan.active(True)
    if wlan.isconnected():
        print("✅ 이미 Wi-Fi 연결 상태:", wlan.ifconfig()); return True
    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)
    t0 = time.time()
    while not wlan.isconnected() and (time.time()-t0 < timeout_sec):
        time.sleep(0.5)
    if wlan.isconnected():
        print("✅ Wi-Fi 연결 완료:", wlan.ifconfig()); return True
    print("❌ Wi-Fi 연결 실패"); return False

def connect_wifi_from_config(timeout_sec=20, force_reset=True):
    global MQTT_BROKER
    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid"); pw = cfg.get("password")
        if ssid and pw and try_connect_wifi(ssid, pw, timeout_sec, force_reset):
            MQTT_BROKER = cfg.get("broker") or DEFAULT_BROKER_IP
            print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
            return True
    if WIFI_SSID and WIFI_PASSWORD and try_connect_wifi(WIFI_SSID, WIFI_PASSWORD, timeout_sec, force_reset):
        MQTT_BROKER = DEFAULT_BROKER_IP; return True
    return False

def wifi_ensure():
    global wlan
    if wlan is None or (not wlan.isconnected()):
        return connect_wifi_from_config(force_reset=True)
    return True

def url_decode(s):
    res=""; i=0
    while i < len(s):
        c=s[i]
        if c=='+': res+=' '
        elif c=='%' and i+2 < len(s):
            try: res += chr(int(s[i+1:i+3],16)); i += 2
            except Exception: res += c
        else: res += c
        i += 1
    return res

def parse_form(body):
    out={}
    for p in body.split('&'):
        if '=' in p:
            k,v=p.split('=',1); out[k]=url_decode(v)
    return out

def start_config_portal():
    sta = network.WLAN(network.STA_IF); sta.active(False)
    ap = network.WLAN(network.AP_IF); ap.config(essid=AP_SSID, password=AP_PW); ap.active(True)
    print("📶 AP 모드 시작:", ap.ifconfig())
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception: pass
    s.bind(addr); s.listen(1)
    while True:
        cl, _ = s.accept(); req = cl.recv(1024)
        try: req_str = req.decode()
        except Exception: req_str = ""
        if "POST /save" in req_str:
            body = req_str.split("\r\n\r\n",1)[1] if "\r\n\r\n" in req_str else ""
            form = parse_form(body)
            ssid = form.get("ssid","").strip(); pw = form.get("pw","").strip(); broker = form.get("broker","").strip()
            if ssid and pw:
                save_wifi_config(ssid,pw,broker or None)
                cl.send(HTML_SAVED.encode()); cl.close(); time.sleep(3); machine.reset()
            else:
                cl.send(HTML_FORM.encode()); cl.close()
        else:
            cl.send(HTML_FORM.encode()); cl.close()

def startup_wifi_or_portal():
    if connect_wifi_from_config(force_reset=True): return True
    start_config_portal(); return False

def log(level, msg, **extra):
    try:
        print("[%s][%s] %s %s" % (DEVICE_ID, level, msg, extra if extra else ""))
    except Exception:
        pass
    try:
        if client is None: return
        rec = {"id": DEVICE_ID, "type": "subscriber", "level": level, "msg": msg, "ts": int(time.time())}
        if extra: rec.update(extra)
        client.publish(TOPIC_LOG, json.dumps(rec))
    except Exception:
        pass

def publish_status(c, online=True):
    try:
        c.publish(TOPIC_STATUS, json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber", "status": "online" if online else "offline", "ts": int(time.time())}), retain=True)
    except Exception as e:
        print("⚠️ status publish 실패:", e)

def publish_hello(c):
    try:
        ip = network.WLAN(network.STA_IF).ifconfig()[0]
        payload = json.dumps({"id": DEVICE_ID, "ip": ip, "name": DEVICE_ID, "type": "subscriber", "ts": int(time.time())})
        c.publish(TOPIC_HELLO, payload, retain=True)
        log("info", "hello published", ip=ip)
    except Exception as e:
        print("⚠️ HELLO publish 실패:", e)

def make_client():
    cid = b"pico-" + ubinascii.hexlify(machine.unique_id())
    c = MQTTClient(cid, MQTT_BROKER, port=MQTT_PORT, keepalive=KEEPALIVE)
    will = json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber", "status": "offline", "ts": int(time.time())})
    c.set_last_will(TOPIC_STATUS, will, retain=True)
    return c

NAMED = {"red": (255,0,0), "yellow": (255,255,0), "green": (0,255,0), "blue": (0,0,255), "purple": (128,0,128), "brown": (165,42,42), "white": (255,255,255), "black": (0,0,0)}

def _new_effect_token():
    global _EFFECT_GEN
    _EFFECT_GEN = (_EFFECT_GEN + 1) & 0x7fffffff
    return _EFFECT_GEN

def _is_current(token):
    return token == _EFFECT_GEN

def _sleep_with_token(c, seconds, token, poll=0.1):
    end = time.ticks_add(time.ticks_ms(), int(seconds*1000))
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try: c.check_msg()
        except Exception: pass
        if not _is_current(token): return False
        time.sleep(poll)
    return _is_current(token)

def handle_message(c, topic_b, msg_b):
    global mood_rgb, mood_bright
    topic = topic_b.decode() if isinstance(topic_b, bytes) else str(topic_b)
    raw = msg_b.decode() if isinstance(msg_b, bytes) else str(msg_b)
    log("debug", "cmd recv", topic=topic, raw=raw[:120])
    try:
        data = json.loads(raw) if raw and raw[0] in "{[" else {"text": raw}
        cmd = (data.get("command") or data.get("text","")).strip()
        sensor_id = data.get("sensor_id")
        if topic == TOPIC_REQ:
            publish_hello(c); return
        if cmd in ("fire_warning", "yellow_flash"):
            if sensor_id == "gas_sensor_pico":
                cmd = "hex_flash"; data["color"] = data.get("color", "#8300FD"); data["duration_ms"] = data.get("duration_ms", 5000)
            else:
                cmd = "hex_flash"; data["color"] = data.get("color", "#FD6A00"); data["duration_ms"] = data.get("duration_ms", 5000)
        if cmd == "purple_blink_3s" and sensor_id == "water_level_1":
            cmd = "hex_flash"; data["color"] = "#0045FD"; data["duration_ms"] = 5000
        if cmd == "brown_blink_3s" and sensor_id == "doorbell_1":
            cmd = "hex_flash"; data["color"] = "#00FD05"; data["duration_ms"] = 5000

        if cmd == "set_mood":
            _new_effect_token()
            hex_color = (data.get("color") or "#FFFFFF").strip()
            brightness = clamp(int(data.get("brightness", DEFAULT_BRIGHT)), 0, 255)
            mood_rgb, mood_bright = hex_to_rgb(hex_color), brightness
            set_all(apply_brightness(mood_rgb, mood_bright))
            return

        if cmd == "hex_flash":
            token = _new_effect_token()
            hex_color = (data.get("color") or "#FFFFFF").strip()
            try: rgb = hex_to_rgb(hex_color)
            except Exception: rgb = (255,255,255)
            duration_ms = int(data.get("duration_ms", 5000))
            flash = apply_brightness(rgb, mood_bright)
            base = apply_brightness(mood_rgb, mood_bright)
            set_all(flash)
            if _sleep_with_token(c, duration_ms/1000.0, token):
                set_all(base)
            return

        if cmd.endswith("_blink_3s"):
            token = _new_effect_token()
            name = cmd.replace("_blink_3s","")
            blink = apply_brightness(NAMED.get(name, NAMED["white"]), mood_bright)
            base = apply_brightness(mood_rgb, mood_bright)
            end = time.ticks_add(time.ticks_ms(), 3000)
            on = False
            while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
                set_all(blink if on else base)
                on = not on
                _sleep_with_token(c, 0.25, token)
            if _is_current(token): set_all(base)
            return

        if cmd in ("fire_confirmed", "red_blink"):
            token = _new_effect_token()
            red = apply_brightness(NAMED["red"], mood_bright)
            base = apply_brightness(mood_rgb, mood_bright)
            end = time.ticks_add(time.ticks_ms(), 10000)
            on = False
            while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
                set_all(red if on else base)
                on = not on
                _sleep_with_token(c, 0.25, token)
            if _is_current(token): set_all(base)
            return

        if cmd in ("off","black"):
            _new_effect_token(); set_all((0,0,0)); return
        log("warn","unknown cmd", cmd=cmd)
    except Exception as e:
        log("error","handle_message error", error=str(e))

def mqtt_connect_and_subscribe():
    global client
    print("📡 MQTT 연결 시도 중... (broker =", MQTT_BROKER, ")")
    client = make_client()
    try:
        client.set_callback(lambda t, m: handle_message(client, t, m))
        client.connect()
        publish_status(client, True)
        publish_hello(client)
        client.subscribe(TOPIC_CMD_THIS, qos=1)
        client.subscribe(TOPIC_CMD_ALL, qos=1)
        client.subscribe(TOPIC_REQ, qos=1)
        log("info","mqtt connected")
        return True
    except Exception as e:
        print("❌ MQTT 연결 실패:", repr(e))
        try: client.disconnect()
        except Exception: pass
        client = None
        return False

def hard_recover(reason="unknown"):
    global client, wlan
    print("♻️ 하드 복구 실행:", reason)
    try:
        if client: client.disconnect()
    except Exception: pass
    client = None
    try:
        if wlan is not None:
            wlan.active(False); time.sleep(1); wlan.active(True); time.sleep(1)
    except Exception: pass
    time.sleep(2); machine.reset()

def main():
    global recovery_fail_count
    set_all(apply_brightness(mood_rgb, mood_bright))
    startup_wifi_or_portal()
    while not mqtt_connect_and_subscribe():
        time.sleep(3)
    last_ping = time.time()
    last_hello = time.time()
    while True:
        try:
            if not wifi_ensure():
                recovery_fail_count += 1
                if recovery_fail_count >= MAX_RECOVERY_FAILS:
                    hard_recover("neopixel wifi disconnected")
                time.sleep(2); continue
            client.check_msg()
            now = time.time()
            if now - last_ping >= KEEPALIVE // 2:
                client.ping(); last_ping = now
            if now - last_hello >= 60:
                publish_hello(client); last_hello = now
            recovery_fail_count = 0
            time.sleep(0.05)
        except Exception as e:
            print("❌ MQTT 오류:", e)
            log("error","mqtt loop error", error=str(e))
            ok = False
            for _ in range(10):
                if connect_wifi_from_config(force_reset=True) and mqtt_connect_and_subscribe():
                    ok = True; break
                time.sleep(3)
            if ok:
                recovery_fail_count = 0
            else:
                recovery_fail_count += 1
                if recovery_fail_count >= MAX_RECOVERY_FAILS:
                    hard_recover("neopixel mqtt loop stuck")
                time.sleep(2)

main()
