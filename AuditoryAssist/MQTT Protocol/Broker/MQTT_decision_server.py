# MQTT_decision_server.py
import json, time, socket, datetime, os
from collections import defaultdict, deque
import paho.mqtt.client as mqtt
import threading

from handler_registry import HANDLER_NAME_MAP
import handlers
_ = handlers.__name__
from firebase.firebase_utils import save_fcm_token

BROKER_IP   = os.getenv("MQTT_BROKER_IP", "127.0.0.1")
BROKER_PORT = 1883
KEEPALIVE   = 30
DISCOVERY_PORT = 30303

CONTROL_TOPIC   = "decision/control"
APP_NEOPIXEL    = "interfaceui/commands/mood"
STATUS_SERVER   = "interfaceui/status/server"
REG_REQUEST     = "interfaceui/registry/request"
HELLO_SERVER    = "interfaceui/registry/hello/server"
PUSH_REGISTER   = "interfaceui/push/register"

LOG_STREAM_PREFIX  = "interfaceui/logs"
LOG_HISTORY_REQ    = "interfaceui/logs/request"
LOG_HISTORY_PREFIX = "interfaceui/logs/history"

VIBRATOR_TOPIC_PREFIX = "vibrator"
BEACON_TOPIC_PREFIX   = "beacon"
VERBOSE_PUBLISH_LOG = False

with open("MQTT_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)
MQTT_TOPICS = list(config.keys())

MQTT_event_status = {
    cfg["sensor_id"]: False
    for cfg in config.values()
    if cfg.get("participates_in_alltrue", True)
}

def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""

userdata = {
    "devices": ["Neopixel_1", "Neopixel_2"],
    "vib_devices": ["Vibrator_1"],
    "beacon_devices": ["Beacon_1"],
    "default_command": "fire_confirmed",
    "sensor_status": MQTT_event_status,
    "just_triggered": False,
    "server_ip": _get_local_ip(),
}

def _refresh_server_ip() -> str:
    ip = _get_local_ip()
    if ip:
        userdata["server_ip"] = ip
    return userdata.get("server_ip", "") or ""

def _discovery_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", DISCOVERY_PORT))
    print(f"🔎 MQTT 브로커 discovery 서버 대기 중... (UDP {DISCOVERY_PORT})")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            if data.strip() == b"MQTT_DISCOVER":
                ip = _refresh_server_ip()
                if not ip:
                    continue
                resp = json.dumps({"ip": ip, "port": BROKER_PORT}).encode()
                sock.sendto(resp, addr)
                print(f"📤 브로커 정보 응답: {addr} → {resp}")
        except Exception as e:
            print("❌ discovery loop error:", e)
            time.sleep(1.0)

def start_discovery_server():
    threading.Thread(target=_discovery_loop, daemon=True).start()

def _now_ts_ms() -> int:
    return int(time.time() * 1000)

def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

def _status_payload(online: bool) -> str:
    return json.dumps({
        "id": "server", "name": "중앙 관리 서버",
        "type": "server", "status": "online" if online else "offline",
        "ts": int(time.time()), "ts_ms": _now_ts_ms(), "iso": _now_iso(),
    })

def _hello_payload() -> str:
    return json.dumps({
        "id": "server", "name": "중앙 관리 서버", "type": "server",
        "ip": _refresh_server_ip(),
        "ts": int(time.time()), "ts_ms": _now_ts_ms(), "iso": _now_iso(),
    })

def publish_server_status(client, online: bool):
    print(f"📣 STATUS publish → {STATUS_SERVER} : online={online}")
    client.publish(STATUS_SERVER, _status_payload(online), qos=1, retain=True)

def publish_server_hello(client):
    payload = _hello_payload()
    print(f"📣 HELLO publish → {HELLO_SERVER} : {payload}")
    client.publish(HELLO_SERVER, payload, qos=1, retain=True)

_RING_MAX = 2000
ring = defaultdict(lambda: deque(maxlen=_RING_MAX))

def _log_key(typ: str, id_: str) -> str:
    return f"{typ}|{id_}"

def log_publish(client, *, typ: str, id_: str, level: str, msg: str, **extra):
    rec = {"id": id_, "type": typ, "level": level, "msg": msg,
           "ts": int(time.time()), "ts_ms": _now_ts_ms(), "iso": _now_iso()}
    if extra:
        rec.update(extra)
    topic = f"{LOG_STREAM_PREFIX}/{typ}/{id_}"
    client.publish(topic, json.dumps(rec), qos=0, retain=False)
    ring[_log_key(typ, id_)].append(rec)


def publish_vibrate_fire_alert(client, context, *, duration_ms=10000, on_ms=400, off_ms=200, intensity=0.85):
    for dev in (context.get("vib_devices") or ["Vibrator_1"]):
        payload = {
            "command": "vibrate_fire_alert", "pattern": "fire_alert",
            "duration_ms": int(duration_ms), "on_ms": int(on_ms), "off_ms": int(off_ms),
            "intensity": float(intensity), "alert": True, "sensor_id": "all_true",
            "issuer": "decision_server", "device_id": dev,
        }
        client.publish(f"{VIBRATOR_TOPIC_PREFIX}/{dev}", json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="info", msg="vibrator command sent", target=dev)

def publish_vibrate_stop(client, context):
    for dev in (context.get("vib_devices") or ["Vibrator_1"]):
        payload = {"command": "vibrate_stop", "issuer": "decision_server", "device_id": dev}
        client.publish(f"{VIBRATOR_TOPIC_PREFIX}/{dev}", json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="debug", msg="vibrator stop sent", target=dev)

def publish_beacon_fire_alert(client, context, *, duration_ms=10000, on_ms=250, off_ms=250):
    for dev in (context.get("beacon_devices") or ["Beacon_1"]):
        payload = {
            "command": "beacon_fire_alert", "duration_ms": int(duration_ms),
            "on_ms": int(on_ms), "off_ms": int(off_ms), "alert": True,
            "sensor_id": "all_true", "issuer": "decision_server", "device_id": dev,
        }
        client.publish(f"{BEACON_TOPIC_PREFIX}/{dev}", json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="info", msg="beacon command sent", target=dev)

def publish_beacon_stop(client, context):
    for dev in (context.get("beacon_devices") or ["Beacon_1"]):
        payload = {"command": "beacon_stop", "issuer": "decision_server", "device_id": dev}
        client.publish(f"{BEACON_TOPIC_PREFIX}/{dev}", json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="debug", msg="beacon stop sent", target=dev)

def all_True_publisher(client, context):
    print(f"🧪 sensor_status: {context['sensor_status']}")
    if context["sensor_status"] and all(context["sensor_status"].values()):
        print("🚨 ALL-TRUE detected")
        base = {"command": context["default_command"], "sensor_id": "all_true", "alert": True, "issuer": "decision_server"}
        for dev in (context.get("devices") or ["Neopixel_1"]):
            payload = dict(base)
            payload["device_id"] = dev
            client.publish(f"neopixel/{dev}", json.dumps(payload), qos=1, retain=False)
        publish_vibrate_fire_alert(client, context)
        publish_beacon_fire_alert(client, context)
        for k in context["sensor_status"]:
            context["sensor_status"][k] = False
        context["just_triggered"] = False


def forward_mood_to_neopixel(client, raw: dict, context):
    try:
        if raw.get("command") != "set_mood":
            return
        hex_color = str(raw.get("color", "#FFFFFF")).strip().upper()
        if not (hex_color.startswith("#") and len(hex_color) == 7):
            return
        brightness = int(raw.get("brightness", 255))
        if not (0 <= brightness <= 255):
            return
        target = raw.get("target")
        targets = [target] if target else (context.get("devices") or ["Neopixel_1"])
        for dev in targets:
            payload = {"command": "set_mood", "color": hex_color, "brightness": brightness, "issuer": "decision_server", "device_id": dev}
            client.publish(f"neopixel/{dev}", json.dumps(payload), qos=1, retain=False)
    except Exception as e:
        print("❌ forward error:", e)

def handle_history_request(client, payload: dict):
    req_id = str(payload.get("id") or "server")
    req_type = str(payload.get("type") or ("server" if req_id == "server" else "subscriber"))
    limit = int(payload.get("limit", 200))
    limit = 50 if limit < 1 else (1000 if limit > 1000 else limit)
    before = payload.get("before_ts")
    buf = list(ring.get(_log_key(req_type, req_id), []))
    if before:
        buf = [r for r in buf if r.get("ts", 0) < int(before)]
    items = buf[-limit:]
    resp_topic = f"{LOG_HISTORY_PREFIX}/{req_type}/{req_id}"
    client.publish(resp_topic, json.dumps({"id": req_id, "type": req_type, "items": items}), qos=0, retain=False)


def on_message(client, context, msg):
    try:
        topic = msg.topic
        raw = msg.payload.decode(errors="ignore") if msg.payload else ""
        payload = json.loads(raw) if raw and raw[0] in "[{" else {"text": raw}

        if topic.startswith(f"{LOG_STREAM_PREFIX}/"):
            parts = topic.split("/", 4)
            if len(parts) >= 4:
                typ, id_ = parts[2], parts[3]
                rec = payload if isinstance(payload, dict) else {"msg": payload}
                rec.setdefault("id", id_)
                rec.setdefault("type", typ)
                rec.setdefault("level", "info")
                rec.setdefault("ts", int(time.time()))
                rec.setdefault("ts_ms", _now_ts_ms())
                rec.setdefault("iso", _now_iso())
                ring[_log_key(typ, id_)].append(rec)
            return

        if topic == APP_NEOPIXEL:
            forward_mood_to_neopixel(client, payload, context)
            return
        if topic == REG_REQUEST:
            publish_server_hello(client)
            return
        if topic == LOG_HISTORY_REQ:
            handle_history_request(client, payload)
            return
        if topic == PUSH_REGISTER:
            token = None
            try:
                raw_s = msg.payload.decode() if msg.payload else ""
                token = json.loads(raw_s).get("token") if raw_s.strip().startswith("{") else raw_s.strip()
            except Exception:
                token = None
            if token:
                try:
                    save_fcm_token(token)
                except Exception as e:
                    print("❌ save_fcm_token error:", e)
            return
        if topic == CONTROL_TOPIC and payload.get("command") == "reset_all":
            for k in context["sensor_status"]:
                context["sensor_status"][k] = False
            context["just_triggered"] = False
            publish_vibrate_stop(client, context)
            publish_beacon_stop(client, context)
            return

        cfg = config.get(topic)
        if not cfg:
            print("❗unregistered topic:", topic)
            return
        if payload.get("sensor_id") != cfg["sensor_id"] or payload.get("event") != cfg["expected_event"]:
            print("❌ unexpected sensor payload:", payload)
            return
        handler = HANDLER_NAME_MAP.get(cfg["handler"])
        if not handler:
            print("❗no handler:", cfg["handler"])
            return
        handler(payload, client, context)
        if cfg.get("participates_in_alltrue", True):
            all_True_publisher(client, context)
    except Exception as e:
        print("❌ on_message exception:", e)


def on_connect(client, context, flags, rc, _=None):
    print("✅ MQTT connected (rc=", rc, ")")
    publish_server_status(client, True)
    publish_server_hello(client)
    for t in MQTT_TOPICS:
        client.subscribe(t, qos=1)
    client.subscribe(CONTROL_TOPIC, qos=1)
    client.subscribe(APP_NEOPIXEL, qos=1)
    client.subscribe(REG_REQUEST, qos=1)
    client.subscribe(LOG_HISTORY_REQ, qos=1)
    client.subscribe(PUSH_REGISTER, qos=1)
    client.subscribe(f"{LOG_STREAM_PREFIX}/+/+", qos=0)
    orig_publish = client.publish
    def _pub_wrap(topic, payload=None, qos=0, retain=False):
        return orig_publish(topic, payload, qos, retain)
    client.publish = _pub_wrap

def loop():
    start_discovery_server()
    while True:
        try:
            _refresh_server_ip()
            client = mqtt.Client(client_id="decision_server", userdata=userdata)
            client.will_set(STATUS_SERVER, _status_payload(False), qos=1, retain=True)
            client.on_connect = lambda c, u, f, rc: on_connect(c, u, f, rc)
            client.on_message = lambda c, u, m: on_message(c, u, m)
            print("📡 MQTT 서버 연결 시도…")
            client.connect(BROKER_IP, BROKER_PORT, keepalive=KEEPALIVE)
            print("🚀 판단 서버 실행 중")
            last_hello = time.time()
            last_hb_log = time.time()
            while True:
                client.loop(timeout=1.0)
                now = time.time()
                if now - last_hello >= 60:
                    publish_server_hello(client)
                    last_hello = now
                if now - last_hb_log >= 60:
                    log_publish(client, typ="server", id_="server", level="debug", msg="heartbeat")
                    last_hb_log = now
        except Exception as e:
            print(f"❌ MQTT loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    loop()
