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
FIRE_CONFIRMED_TOPIC  = "alerts/fire_confirmed"
VERBOSE_PUBLISH_LOG = False

# all-True / 화재 경고 설정값
FIRE_ALERT_DEFAULT_DURATION_MS = 10000
FIRE_ALERT_LOCK_EXTRA_MS = 1000
ALLTRUE_WINDOW_MS = 15000
ALLTRUE_DEBOUNCE_MS = 3000

with open("MQTT_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

MQTT_TOPICS = list(config.keys())

# 교수님 요청 반영:
# - AI 불 감지 카메라는 all-True 조합을 기다리지 않고 단독으로 화재 확정 동작
# - all-True 조합은 불꽃 + 일산화탄소 + 가스 3개만 사용
AI_FIRE_TOPIC = "AI_fire_alert"
AI_FIRE_SENSOR_ID = "AI_D_fire"
ALLTRUE_EXCLUDED_TOPICS = {AI_FIRE_TOPIC}
ALLTRUE_EXCLUDED_SENSOR_IDS = {AI_FIRE_SENSOR_ID}

ALLTRUE_REQUIRED_SENSOR_IDS = [
    cfg["sensor_id"]
    for topic, cfg in config.items()
    if cfg.get("participates_in_alltrue", True)
    and topic not in ALLTRUE_EXCLUDED_TOPICS
    and cfg.get("sensor_id") not in ALLTRUE_EXCLUDED_SENSOR_IDS
]

# all-True에 참여하는 센서들의 현재 상태
MQTT_event_status = {
    sid: False
    for sid in ALLTRUE_REQUIRED_SENSOR_IDS
}

# all-True에 참여하는 센서들의 마지막 위험 감지 시간
MQTT_event_detected_at_ms = {
    sid: 0
    for sid in ALLTRUE_REQUIRED_SENSOR_IDS
}


def _get_local_ip():
    """
    인터넷 연결 없이 Raspberry Pi의 LAN IPv4를 찾는다.

    기존 8.8.8.8 UDP connect 방식은 학교망/인터넷 차단 환경에서 실패할 수 있으므로
    Ubuntu/Linux 네트워크 인터페이스의 IPv4 주소를 직접 조회한다.
    우선순위: wlan0 -> eth0 -> 그 외 private IPv4.
    """
    try:
        import subprocess
        import ipaddress

        out = subprocess.check_output(
            ["ip", "-j", "-4", "addr", "show"],
            text=True,
        )
        interfaces = json.loads(out)

        for ifname in ("wlan0", "eth0"):
            for item in interfaces:
                if item.get("ifname") != ifname:
                    continue
                for addr in item.get("addr_info", []):
                    ip = addr.get("local")
                    if not ip:
                        continue
                    parsed = ipaddress.ip_address(ip)
                    if parsed.is_private and not parsed.is_loopback:
                        return ip

        for item in interfaces:
            if item.get("ifname") == "lo":
                continue
            for addr in item.get("addr_info", []):
                ip = addr.get("local")
                if not ip:
                    continue
                parsed = ipaddress.ip_address(ip)
                if parsed.is_private and not parsed.is_loopback:
                    return ip

    except Exception as e:
        print("⚠️ local ip detection failed:", e)

    return ""


userdata = {
    "devices": ["Neopixel_1", "Neopixel_2"],
    "vib_devices": ["Vibrator_1"],
    "beacon_devices": ["Beacon_1"],

    "default_command": "fire_confirmed",

    # all-True 상태 관리
    "sensor_status": MQTT_event_status,
    "sensor_detected_at_ms": MQTT_event_detected_at_ms,
    "alltrue_required_sensor_ids": ALLTRUE_REQUIRED_SENSOR_IDS,
    "alltrue_window_ms": ALLTRUE_WINDOW_MS,
    "alltrue_debounce_ms": ALLTRUE_DEBOUNCE_MS,
    "last_alltrue_ms": 0,

    # 개별 flash 중복 방지
    "just_triggered": False,

    # 화재 경고 우선순위 잠금
    # 이 시간이 지나기 전까지 handlers.py에서 개별 센서 색상 명령을 차단함
    "fire_alert_until_ms": 0,

    "server_ip": _get_local_ip(),
}


def _refresh_server_ip() -> str:
    ip = _get_local_ip()
    if ip:
        userdata["server_ip"] = ip
    return userdata.get("server_ip", "") or ""


def _now_ts_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _discovery_payload(ip: str) -> bytes:
    """
    UDP Discovery 응답 payload.

    기존 앱/장치 호환을 위해 ip, port는 유지하고,
    상용화 확장을 위해 mqtt_uri, device_id, name, type, discovery_version도 제공한다.
    """
    return json.dumps({
        "device_id": "auditoryassist_hub_001",
        "name": "AuditoryAssist Hub",
        "type": "mqtt_broker",
        "ip": ip,
        "port": BROKER_PORT,
        "mqtt_uri": f"tcp://{ip}:{BROKER_PORT}",
        "discovery_version": 1,
        "ts_ms": _now_ts_ms(),
    }).encode()


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

                resp = _discovery_payload(ip)
                sock.sendto(resp, addr)
                print(f"📤 브로커 정보 응답: {addr} → {resp}")

        except Exception as e:
            print("❌ discovery loop error:", e)
            time.sleep(1.0)


def start_discovery_server():
    threading.Thread(target=_discovery_loop, daemon=True).start()


def _status_payload(online: bool) -> str:
    ip = _refresh_server_ip()
    return json.dumps({
        "id": "server",
        "name": "중앙 관리 서버",
        "type": "server",
        "status": "online" if online else "offline",
        "ip": ip,
        "port": BROKER_PORT,
        "mqtt_uri": f"tcp://{ip}:{BROKER_PORT}" if ip else "",
        "ts": int(time.time()),
        "ts_ms": _now_ts_ms(),
        "iso": _now_iso(),
    })


def _hello_payload() -> str:
    ip = _refresh_server_ip()

    return json.dumps({
        "id": "server",
        "name": "중앙 관리 서버",
        "type": "server",
        "ip": ip,
        "port": BROKER_PORT,
        "mqtt_uri": f"tcp://{ip}:{BROKER_PORT}" if ip else "",
        "ts": int(time.time()),
        "ts_ms": _now_ts_ms(),
        "iso": _now_iso(),
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
    rec = {
        "id": id_,
        "type": typ,
        "level": level,
        "msg": msg,
        "ts": int(time.time()),
        "ts_ms": _now_ts_ms(),
        "iso": _now_iso(),
    }

    if extra:
        rec.update(extra)

    topic = f"{LOG_STREAM_PREFIX}/{typ}/{id_}"

    client.publish(topic, json.dumps(rec), qos=0, retain=False)
    ring[_log_key(typ, id_)].append(rec)


def is_fire_alert_active(context) -> bool:
    until = int(context.get("fire_alert_until_ms", 0) or 0)
    now = _now_ts_ms()

    if until <= now:
        context["fire_alert_until_ms"] = 0
        return False

    return True


def set_fire_alert_lock(context, duration_ms: int = FIRE_ALERT_DEFAULT_DURATION_MS):
    lock_ms = int(duration_ms) + FIRE_ALERT_LOCK_EXTRA_MS
    context["fire_alert_until_ms"] = _now_ts_ms() + lock_ms
    print(f"🔒 fire_alert lock 설정: {lock_ms}ms")


def clear_fire_alert_lock(context):
    context["fire_alert_until_ms"] = 0
    print("🔓 fire_alert lock 해제")


def publish_vibrate_fire_alert(
    client,
    context,
    *,
    duration_ms=10000,
    on_ms=400,
    off_ms=200,
    intensity=0.85,
    sensor_id="all_true",
):
    for dev in (context.get("vib_devices") or ["Vibrator_1"]):
        payload = {
            "command": "vibrate_fire_alert",
            "pattern": "fire_alert",
            "duration_ms": int(duration_ms),
            "on_ms": int(on_ms),
            "off_ms": int(off_ms),
            "intensity": float(intensity),
            "alert": True,
            "sensor_id": sensor_id,
            "issuer": "decision_server",
            "device_id": dev,
            "ts_ms": _now_ts_ms(),
        }

        client.publish(
            f"{VIBRATOR_TOPIC_PREFIX}/{dev}",
            json.dumps(payload),
            qos=1,
            retain=False,
        )

        log_publish(
            client,
            typ="server",
            id_="server",
            level="info",
            msg="vibrator command sent",
            target=dev,
            sensor_id=sensor_id,
        )


def publish_vibrate_stop(client, context):
    for dev in (context.get("vib_devices") or ["Vibrator_1"]):
        payload = {
            "command": "vibrate_stop",
            "issuer": "decision_server",
            "device_id": dev,
            "ts_ms": _now_ts_ms(),
        }

        client.publish(
            f"{VIBRATOR_TOPIC_PREFIX}/{dev}",
            json.dumps(payload),
            qos=1,
            retain=False,
        )

        log_publish(
            client,
            typ="server",
            id_="server",
            level="debug",
            msg="vibrator stop sent",
            target=dev,
        )


def publish_beacon_fire_alert(
    client,
    context,
    *,
    duration_ms=10000,
    on_ms=250,
    off_ms=250,
    sensor_id="all_true",
):
    for dev in (context.get("beacon_devices") or ["Beacon_1"]):
        payload = {
            "command": "beacon_fire_alert",
            "duration_ms": int(duration_ms),
            "on_ms": int(on_ms),
            "off_ms": int(off_ms),
            "alert": True,
            "sensor_id": sensor_id,
            "issuer": "decision_server",
            "device_id": dev,
            "ts_ms": _now_ts_ms(),
        }

        client.publish(
            f"{BEACON_TOPIC_PREFIX}/{dev}",
            json.dumps(payload),
            qos=1,
            retain=False,
        )

        log_publish(
            client,
            typ="server",
            id_="server",
            level="info",
            msg="beacon command sent",
            target=dev,
            sensor_id=sensor_id,
        )


def publish_beacon_stop(client, context):
    for dev in (context.get("beacon_devices") or ["Beacon_1"]):
        payload = {
            "command": "beacon_stop",
            "issuer": "decision_server",
            "device_id": dev,
            "ts_ms": _now_ts_ms(),
        }

        client.publish(
            f"{BEACON_TOPIC_PREFIX}/{dev}",
            json.dumps(payload),
            qos=1,
            retain=False,
        )

        log_publish(
            client,
            typ="server",
            id_="server",
            level="debug",
            msg="beacon stop sent",
            target=dev,
        )


def publish_fire_alert(
    client,
    context,
    *,
    sensor_id="all_true",
    source="all_true",
    duration_ms=FIRE_ALERT_DEFAULT_DURATION_MS,
):
    """
    화재 확정 상태 공통 출력 함수.

    사용 위치:
    1. AI 불 감지 카메라가 감지된 경우
    2. 불꽃 + CO + 가스 3개가 최근 alltrue_window_ms 안에 모두 감지된 경우
    3. Node-RED 화재 트리거 fire_test / fire_trigger / force_fire_alert 발생

    동작:
    1. fire_alert lock 설정
    2. 무드등 빨간색 ↔ 기존 평상시 색상 점멸 명령 전송
    3. 진동장치 명령 전송
    4. 경광등 명령 전송
    5. 앱/로컬 알림용 alerts/fire_confirmed publish
    """
    duration_ms = int(duration_ms)
    ts_ms = _now_ts_ms()

    set_fire_alert_lock(context, duration_ms)

    base = {
        "command": context["default_command"],
        "sensor_id": sensor_id,
        "alert": True,
        "issuer": "decision_server",
        "source": source,
        "duration_ms": duration_ms,
        "ts_ms": ts_ms,
    }

    for dev in (context.get("devices") or ["Neopixel_1"]):
        payload = dict(base)
        payload["device_id"] = dev

        client.publish(
            f"neopixel/{dev}",
            json.dumps(payload),
            qos=1,
            retain=False,
        )

        log_publish(
            client,
            typ="server",
            id_="server",
            level="warning",
            msg="neopixel fire alert sent",
            target=dev,
            sensor_id=sensor_id,
            source=source,
            duration_ms=duration_ms,
        )

    publish_vibrate_fire_alert(
        client,
        context,
        duration_ms=duration_ms,
        on_ms=400,
        off_ms=200,
        intensity=0.85,
        sensor_id=sensor_id,
    )

    publish_beacon_fire_alert(
        client,
        context,
        duration_ms=duration_ms,
        on_ms=250,
        off_ms=250,
        sensor_id=sensor_id,
    )

    alert_payload = {
        "type": "fire_confirmed",
        "level": "critical",
        "command": "fire_confirmed",
        "source": source,
        "sensor_id": sensor_id,
        "title": "화재 감지",
        "body": "화재 위험 조건이 충족되어 무드등, 진동장치, 경광등을 동작합니다.",
        "duration_ms": duration_ms,
        "ts_ms": ts_ms,
    }

    client.publish(
        FIRE_CONFIRMED_TOPIC,
        json.dumps(alert_payload, ensure_ascii=False),
        qos=1,
        retain=False,
    )

    log_publish(
        client,
        typ="server",
        id_="server",
        level="warning",
        msg="fire confirmed alert published",
        topic=FIRE_CONFIRMED_TOPIC,
        sensor_id=sensor_id,
        source=source,
        duration_ms=duration_ms,
    )


def all_True_publisher(client, context):
    """
    최근 alltrue_window_ms 안에 all-True 대상 센서들이 모두 감지되었는지 확인한다.

    변경 후 기준:
    - AI 불 감지 카메라는 all-True 조합에서 제외
    - 불꽃 + 일산화탄소 + 가스 3개가 15초 안에 모두 감지되면 화재 확정
    """
    required = list(
        context.get("alltrue_required_sensor_ids")
        or context.get("sensor_status", {}).keys()
    )
    detected_at = context.get("sensor_detected_at_ms", {})
    now = _now_ts_ms()

    window_ms = int(context.get("alltrue_window_ms", ALLTRUE_WINDOW_MS))
    debounce_ms = int(context.get("alltrue_debounce_ms", ALLTRUE_DEBOUNCE_MS))

    if not required:
        return

    missing = []

    for sid in required:
        last = int(detected_at.get(sid, 0) or 0)

        if last <= 0 or now - last > window_ms:
            missing.append(sid)

    print(
        f"🧪 all-True window check: "
        f"required={required}, "
        f"window={window_ms}ms, "
        f"detected_at={detected_at}, "
        f"missing={missing}"
    )

    if missing:
        return

    last_alltrue = int(context.get("last_alltrue_ms", 0) or 0)

    if now - last_alltrue < debounce_ms:
        print("🔕 ALL-TRUE debounce skip")
        return

    print("🚨 ALL-TRUE detected by 3-sensor recent detection window")

    context["last_alltrue_ms"] = now

    publish_fire_alert(
        client,
        context,
        sensor_id="all_true",
        source="three_sensor_all_true_window",
        duration_ms=FIRE_ALERT_DEFAULT_DURATION_MS,
    )

    # 다음 all-True 판정을 위해 최근 감지 기록 초기화
    for sid in required:
        context["sensor_status"][sid] = False
        context["sensor_detected_at_ms"][sid] = 0

    context["just_triggered"] = False


def forward_mood_to_neopixel(client, raw: dict, context):
    try:
        if raw.get("command") != "set_mood":
            return

        # 화재 경고 중에는 앱에서 보낸 일반 무드등 색상 변경도 차단
        # 단, payload에 "force": true가 있으면 강제 허용
        if is_fire_alert_active(context) and not bool(raw.get("force", False)):
            print("🔕 fire_alert active → 앱 무드등 색상 변경 차단")

            log_publish(
                client,
                typ="server",
                id_="server",
                level="warning",
                msg="mood command blocked during fire alert",
                source="app",
            )
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
            payload = {
                "command": "set_mood",
                "color": hex_color,
                "brightness": brightness,
                "issuer": "decision_server",
                "device_id": dev,
                "ts_ms": _now_ts_ms(),
            }

            client.publish(
                f"neopixel/{dev}",
                json.dumps(payload),
                qos=1,
                retain=False,
            )

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

    client.publish(
        resp_topic,
        json.dumps({
            "id": req_id,
            "type": req_type,
            "items": items,
        }),
        qos=0,
        retain=False,
    )


def handle_control_command(client, context, payload: dict):
    cmd = payload.get("command")

    if cmd == "reset_all":
        for k in context["sensor_status"]:
            context["sensor_status"][k] = False

        for k in context.get("sensor_detected_at_ms", {}):
            context["sensor_detected_at_ms"][k] = 0

        context["last_alltrue_ms"] = 0
        context["just_triggered"] = False

        clear_fire_alert_lock(context)

        publish_vibrate_stop(client, context)
        publish_beacon_stop(client, context)

        log_publish(
            client,
            typ="server",
            id_="server",
            level="info",
            msg="reset_all received",
            source=payload.get("source", "unknown"),
        )
        return

    if cmd in ("fire_test", "fire_trigger", "force_fire_alert"):
        duration_ms = int(payload.get("duration_ms", FIRE_ALERT_DEFAULT_DURATION_MS))

        publish_fire_alert(
            client,
            context,
            sensor_id="manual_fire_test",
            source=payload.get("source", "nodered_ui"),
            duration_ms=duration_ms,
        )

        log_publish(
            client,
            typ="server",
            id_="server",
            level="warning",
            msg="manual fire trigger received",
            source=payload.get("source", "nodered_ui"),
            duration_ms=duration_ms,
        )
        return

    print(f"❗unknown control command: {payload}")


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

        if topic == CONTROL_TOPIC:
            handle_control_command(client, context, payload)
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

        # 교수님 요청 반영:
        # AI 불 감지 카메라는 all-True 조합을 기다리지 않고
        # 감지 즉시 무드등 빨간 점멸 + 진동 + 경광등을 동작시킨다.
        if topic == AI_FIRE_TOPIC:
            publish_fire_alert(
                client,
                context,
                sensor_id=payload.get("sensor_id", AI_FIRE_SENSOR_ID),
                source="ai_camera_direct",
                duration_ms=FIRE_ALERT_DEFAULT_DURATION_MS,
            )

            log_publish(
                client,
                typ="server",
                id_="server",
                level="warning",
                msg="AI fire direct trigger received",
                sensor_id=payload.get("sensor_id", AI_FIRE_SENSOR_ID),
                topic=topic,
            )
            return

        # 나머지 all-True 대상 센서는 MQTT_config.json 기준 + 서버 내부 제외 리스트 기준으로 판단한다.
        # 현재 기준: 불꽃 + CO + 가스
        if (
            cfg.get("participates_in_alltrue", True)
            and topic not in ALLTRUE_EXCLUDED_TOPICS
            and cfg.get("sensor_id") not in ALLTRUE_EXCLUDED_SENSOR_IDS
        ):
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
            print(f"🧪 all-True required sensors: {ALLTRUE_REQUIRED_SENSOR_IDS}")
            print(f"🔥 AI direct fire topic: {AI_FIRE_TOPIC}")

            last_hello = time.time()
            last_hb_log = time.time()

            while True:
                client.loop(timeout=1.0)
                now = time.time()

                if now - last_hello >= 60:
                    publish_server_hello(client)
                    last_hello = now

                if now - last_hb_log >= 60:
                    log_publish(
                        client,
                        typ="server",
                        id_="server",
                        level="debug",
                        msg="heartbeat",
                    )
                    last_hb_log = now

        except Exception as e:
            print(f"❌ MQTT loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    loop()
