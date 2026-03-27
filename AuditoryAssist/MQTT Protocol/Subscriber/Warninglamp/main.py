import network, time, ubinascii, ujson as json
import socket, os, machine
from machine import Pin
from umqtt.simple import MQTTClient

ENA = Pin(15, Pin.OUT, value=0)
IN1 = Pin(16, Pin.OUT, value=0)
IN2 = Pin(17, Pin.OUT, value=0)

def beacon_on():
    IN1.value(0); IN2.value(0); ENA.value(1)

def beacon_off():
    ENA.value(0)

try:
    onboard_led = Pin("LED", Pin.OUT)
except Exception:
    onboard_led = None

def set_led(v):
    if onboard_led is not None:
        onboard_led.value(1 if v else 0)

def blink_once(on_ms=80, off_ms=80):
    set_led(True); time.sleep_ms(on_ms)
    set_led(False); time.sleep_ms(off_ms)

def blink_n(n, on_ms=80, off_ms=80):
    for _ in range(n):
        blink_once(on_ms, off_ms)

DEVICE_ID = "Beacon_1"
WIFI_SSID = ""
WIFI_PW = ""
CONFIG_PATH = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"
BROKER = DEFAULT_BROKER_IP
PORT = 1883
KEEPALIVE = 60
WIFI_RETRY_MAX = 15
MQTT_RECONNECT_MAX = 10
MAX_RECOVERY_FAILS = 8
BOOT_SETTLE_MS = 2000

AP_SSID = "Beacon_1_setup"
AP_PW = "123456789"

HTML_FORM = '''HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>경광등 WiFi 설정</title></head>
<body>
<h2>Wi-Fi / MQTT 설정 (Beacon_1)</h2>
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

TOPIC_CMD = "beacon/%s" % DEVICE_ID
TOPIC_STATUS = "interfaceui/status/subscriber/%s" % DEVICE_ID
TOPIC_HELLO = "interfaceui/registry/hello/%s" % DEVICE_ID
TOPIC_REQ = "interfaceui/registry/request"
TOPIC_LOG = "interfaceui/logs/subscriber/%s" % DEVICE_ID

client = None
wlan = None
_EFFECT_GEN = 0
recovery_fail_count = 0

def log(level, msg, **extra):
    try:
        print("[{}][{}] {} {}".format(DEVICE_ID, level, msg, extra if extra else ""))
    except Exception:
        pass
    try:
        if client:
            rec = {"id": DEVICE_ID, "type": "subscriber", "level": level, "msg": msg, "ts": int(time.time())}
            if extra: rec.update(extra)
            client.publish(TOPIC_LOG, json.dumps(rec))
    except Exception:
        pass

def load_wifi_config():
    if CONFIG_PATH not in os.listdir(): return None
    try:
        with open(CONFIG_PATH, "r") as f: return json.loads(f.read())
    except Exception as e:
        print("⚠️ config load 실패:", e); return None

def save_wifi_config(ssid, pw, broker_ip=None):
    cfg = {"ssid": ssid, "password": pw}
    if broker_ip: cfg["broker"] = broker_ip
    with open(CONFIG_PATH, "w") as f: f.write(json.dumps(cfg))
    print("✅ Wi-Fi 설정 저장 완료:", cfg)

def try_connect_wifi(ssid, pw, timeout_sec=20):
    global wlan
    if not ssid or not pw: return False
    ap = network.WLAN(network.AP_IF); ap.active(False)
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
    try: wlan.disconnect()
    except Exception: pass
    wlan.active(False); time.sleep(1)
    wlan.active(True); time.sleep(1)
    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)
    t0 = time.time()
    while not wlan.isconnected() and (time.time()-t0 < timeout_sec):
        time.sleep(0.5)
    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패"); return False
    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    blink_n(3); return True

def connect_wifi_from_config(timeout_sec=20):
    global BROKER
    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid"); pw = cfg.get("password")
        if ssid and pw and try_connect_wifi(ssid, pw, timeout_sec):
            BROKER = cfg.get("broker") or DEFAULT_BROKER_IP
            print("🌐 config로 Wi-Fi 연결 OK, broker =", BROKER)
            return True
    if WIFI_SSID and WIFI_PW and try_connect_wifi(WIFI_SSID, WIFI_PW, timeout_sec):
        BROKER = DEFAULT_BROKER_IP; return True
    return False

def wifi_ensure():
    global wlan
    if wlan is None or (not wlan.isconnected()):
        return connect_wifi_from_config()
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
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
    wlan.active(False)
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
    if connect_wifi_from_config(): return True
    start_config_portal(); return False

def publish_status(c, online=True):
    try:
        c.publish(TOPIC_STATUS, json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber", "status": "online" if online else "offline", "ts": int(time.time())}), retain=True)
    except Exception as e:
        print("status publish err:", e)

def publish_hello(c):
    try:
        global wlan
        if wlan is None:
            wlan = network.WLAN(network.STA_IF)
        ip = wlan.ifconfig()[0]
        c.publish(TOPIC_HELLO, json.dumps({"id": DEVICE_ID, "ip": ip, "name": DEVICE_ID, "type": "subscriber", "ts": int(time.time())}), retain=True)
        log("info", "hello published", ip=ip)
    except Exception as e:
        print("hello publish err:", e)

def make_client():
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF); wlan.active(True)
    mac = wlan.config('mac')
    cid = b"beacon-" + ubinascii.hexlify(mac)
    c = MQTTClient(cid, BROKER, port=PORT, keepalive=KEEPALIVE)
    c.set_last_will(TOPIC_STATUS, json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber", "status": "offline", "ts": int(time.time())}), retain=True)
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
        try: c.check_msg()
        except Exception: pass
        if not _is_current(token): return False
        time.sleep(0.05)
    return _is_current(token)

def handle_message(c, t_b, m_b):
    topic = t_b.decode() if isinstance(t_b, bytes) else str(t_b)
    raw = m_b.decode() if isinstance(m_b, bytes) else str(m_b)
    print("🔔", topic, "→", raw)
    try:
        data = json.loads(raw) if raw and raw[0] in "{[" else {"text": raw}
        cmd = (data.get("command") or data.get("text","")).strip()
    except Exception:
        log("error","json parse failed", raw=raw)
        return
    if topic == TOPIC_REQ:
        publish_hello(c); return
    if cmd == "beacon_fire_alert":
        token = _new_token()
        duration_ms = int(data.get("duration_ms",10000))
        on_ms = int(data.get("on_ms",250))
        off_ms = int(data.get("off_ms",250))
        blink_once(40,40)
        end = time.ticks_add(time.ticks_ms(), duration_ms)
        state = False
        while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
            state = not state
            set_led(state)
            beacon_on() if state else beacon_off()
            if not _sleep_with_token(c, on_ms if state else off_ms, token):
                break
        if _is_current(token):
            beacon_off(); set_led(False)
        return
    if cmd == "beacon_stop":
        _new_token(); beacon_off(); set_led(False); return
    log("warn","unknown cmd", cmd=cmd)

def mqtt_connect_and_subscribe():
    global client
    print("📡 MQTT 연결 시도 중... (broker =", BROKER, ")")
    client = make_client()
    try:
        client.set_callback(lambda t,m: handle_message(client,t,m))
        client.connect()
        publish_status(client, True)
        publish_hello(client)
        client.subscribe(TOPIC_CMD, qos=1)
        client.subscribe(TOPIC_REQ, qos=1)
        log("info","mqtt connected")
        blink_n(5); return True
    except Exception as e:
        print("MQTT connect err:", e)
        try: client.disconnect()
        except Exception: pass
        client = None; return False

def hard_recover(reason="unknown"):
    global client, wlan
    print("♻️ 하드 복구 실행:", reason)
    blink_n(4,120,120)
    try:
        if client: client.disconnect()
    except Exception: pass
    client = None
    try:
        if wlan is not None:
            wlan.active(False); time.sleep(1); wlan.active(True); time.sleep(1)
    except Exception: pass
    beacon_off(); set_led(False); time.sleep(2); machine.reset()

def main():
    global client, recovery_fail_count
    beacon_off(); set_led(False); blink_n(2)
    time.sleep_ms(BOOT_SETTLE_MS)
    startup_wifi_or_portal()
    while not mqtt_connect_and_subscribe():
        time.sleep(3)
    last_ping = time.time(); last_hello = time.time()
    while True:
        try:
            if not wifi_ensure():
                recovery_fail_count += 1
                if recovery_fail_count >= MAX_RECOVERY_FAILS:
                    hard_recover("beacon wifi disconnected")
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
            print("loop err:", e)
            log("error","mqtt loop error", error=str(e))
            ok = False
            for _ in range(MQTT_RECONNECT_MAX):
                if connect_wifi_from_config() and mqtt_connect_and_subscribe():
                    ok = True; break
                time.sleep(3)
            if ok:
                recovery_fail_count = 0
            else:
                recovery_fail_count += 1
                blink_once(200,200)
                if recovery_fail_count >= MAX_RECOVERY_FAILS:
                    hard_recover("beacon mqtt loop stuck")
                time.sleep(2)

main()
