# handlers.py
from handler_registry import register_handler
import json
import threading
import socket
import time
from firebase.firebase_utils import send_fcm_messages, save_fcm_token

# metrics
try:
    from metrics_logger import log_event
except Exception as e:
    print("⚠️ metrics_logger import failed:", e)

    def log_event(**kwargs):
        pass

print("✅ handlers.py 로드됨 - 핸들러 등록 완료")

# --- metrics helpers -----------------------------------------------------
def _is_test_payload(payload):
    return isinstance(payload, dict) and bool(payload.get("test_id"))

def _copy_test_fields(dst: dict, src: dict | None) -> dict:
    if not isinstance(src, dict):
        return dst
    keys = [
        "test_group", "test_id", "scenario_id", "trial_no",
        "seq", "t_sent_ms", "t_broker_recv_ms", "priority_class",
        "source_path", "expected_inputs", "received_inputs",
        "expected_devices", "activated_devices"
    ]
    for k in keys:
        if k in src and k not in dst:
            dst[k] = src[k]
    return dst

def publish_with_metrics(client, topic, payload_dict, qos=0, retain=False):
    if _is_test_payload(payload_dict):
        log_event(
            run_id="RUN001",
            test_group=payload_dict.get("test_group", ""),
            test_id=payload_dict.get("test_id", ""),
            scenario_id=payload_dict.get("scenario_id", ""),
            trial_no=payload_dict.get("trial_no", ""),
            phase="BROKER_PUBLISH",
            node_id="decision_server",
            topic=topic,
            device_id=payload_dict.get("device_id", ""),
            seq=payload_dict.get("seq", ""),
            event_name=payload_dict.get("event", ""),
            priority_class=payload_dict.get("priority_class", ""),
            qos=qos,
            t_sent_ms=payload_dict.get("t_sent_ms", ""),
            t_broker_recv_ms=payload_dict.get("t_broker_recv_ms", ""),
            t_broker_publish_ms=int(time.time() * 1000),
            status="published",
            notes=""
        )
    client.publish(topic, json.dumps(payload_dict), qos=qos, retain=retain)

# --- flash 중복 방지 (ALL-TRUE 직후 단색 점등 억제) ----------------------
def skip_if_recent_red(context):
    if context.get("just_triggered", False):
        print("🔕 최근 red_blink 발생 → flash 생략")
        return True
    return False

def set_yellow_lock(context, delay=5):
    context["just_triggered"] = True
    def reset():
        context["just_triggered"] = False
        print("🔄 flash 중복 방지 플래그 초기화됨")
    t = threading.Timer(delay, reset)
    t.daemon = True
    t.start()

def publish_yellow_flash(client, context, sensor_id=None, source_payload=None):
    if skip_if_recent_red(context):
        return
    set_yellow_lock(context, delay=5)
    for device_id in set(context["devices"]):
        payload = {
            "command": "yellow_flash",
            "alert": True,
            "issuer": "decision_server",
            "device_id": device_id,
        }
        if sensor_id:
            payload["sensor_id"] = sensor_id
        payload = _copy_test_fields(payload, source_payload)
        if _is_test_payload(payload):
            payload.setdefault("source_path", "sensor->broker->neopixel")
            payload.setdefault("expected_devices", len(set(context["devices"])))
        publish_with_metrics(client, f"neopixel/{device_id}", payload, qos=0, retain=False)
        print(f"📤 yellow_flash 전송 → neopixel/{device_id} : {payload}")

def publish_hex_flash(client, context, hex_color, sensor_id=None, duration_sec=5, source_payload=None):
    if skip_if_recent_red(context):
        return
    set_yellow_lock(context, delay=duration_sec)
    for device_id in set(context["devices"]):
        payload = {
            "command": "hex_flash",
            "color": hex_color,
            "duration_ms": int(duration_sec * 1000),
            "alert": True,
            "issuer": "decision_server",
            "device_id": device_id,
        }
        if sensor_id:
            payload["sensor_id"] = sensor_id
        payload = _copy_test_fields(payload, source_payload)
        if _is_test_payload(payload):
            payload.setdefault("source_path", "sensor->broker->neopixel")
            payload.setdefault("expected_devices", len(set(context["devices"])))
        publish_with_metrics(client, f"neopixel/{device_id}", payload, qos=0, retain=False)
        print(f"📤 hex_flash 전송 → neopixel/{device_id} : {payload}")

# =======================
#  오프라인에서도 블로킹되지 않는 푸시 전송
# =======================
PUSH_ENABLED = True
_PING_HOST = ("8.8.8.8", 53)
_PING_TIMEOUT = 2

def alert_message(title, body):
    """FCM 푸시를 별도 데몬 스레드에서 전송(오프라인이면 조용히 스킵)."""
    if not PUSH_ENABLED:
        return

    def _bg_send():
        try:
            try:
                s = socket.create_connection(_PING_HOST, timeout=_PING_TIMEOUT)
                s.close()
            except Exception:
                return
            send_fcm_messages(title, body)
        except Exception as e:
            print("⚠️ FCM send error:", e)

    th = threading.Thread(target=_bg_send, daemon=True)
    th.start()

# --- 화재 관련 센서 ------------------------------------------------------
@register_handler("handle_shz")
def handle_shz(payload, client, context):
    sid = payload["sensor_id"]
    context["sensor_status"][sid] = True
    print(f"🔥 불꽃 센서 감지: {sid}")
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5, source_payload=payload)
    alert_message("불꽃 감지", "불꽃 감지 센서에서 불꽃이 감지 되었습니다.")

@register_handler("handle_mq7")
def handle_mq7(payload, client, context):
    sid    = payload["sensor_id"]
    status = payload.get("status", "")
    value  = payload.get("value")
    if status == "정상":
        context["sensor_status"][sid] = False
        print(f"✅ MQ7 정상 보고: sensor={sid}, value={value}")
        return
    context["sensor_status"][sid] = True
    print(f"☠️ MQ7 위험 감지: sensor={sid}, status={status}, value={value}")
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5, source_payload=payload)
    alert_message("일산화탄소 감지", "일산화탄소 센서에서 일산화탄소가 감지 되었습니다.")

@register_handler("handle_gas")
def handle_gas(payload, client, context):
    sid    = payload["sensor_id"]
    status = payload.get("status", "")
    value  = payload.get("value")
    if status == "정상":
        context["sensor_status"][sid] = False
        print(f"✅ GAS 정상 보고: sensor={sid}, value={value}")
        return
    context["sensor_status"][sid] = True
    print(f"🧪 GAS 위험 감지: sensor={sid}, status={status}, value={value}")
    publish_hex_flash(client, context, "#8300FD", sensor_id=sid, duration_sec=5, source_payload=payload)
    alert_message("가스 감지", "가스 센서에서 가스가 감지 되었습니다.")

@register_handler("handle_fire")
def handle_fire(payload, client, context):
    sid = payload["sensor_id"]
    context["sensor_status"][sid] = True
    print(f"🔥 AI 화재 감지: {sid}")
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5, source_payload=payload)
    alert_message("AI 불 감지", "실시간 카메라에서 불이 감지 되었습니다.")

# --- 화재와 별개: 수위/초인종 -------------------------------------------
def publish_to_all_neopixels(client, context, command, extra=None, source_payload=None):
    payload_base = {"command": command, "issuer": "decision_server"}
    if isinstance(extra, dict):
        payload_base.update(extra)

    for device_id in set(context.get("devices", [])):
        payload = dict(payload_base)
        payload["device_id"] = device_id
        payload = _copy_test_fields(payload, source_payload)
        if _is_test_payload(payload):
            payload.setdefault("source_path", "sensor->broker->neopixel")
            payload.setdefault("expected_devices", len(set(context.get("devices", []))))
        publish_with_metrics(client, f"neopixel/{device_id}", payload, qos=0, retain=False)
        print(f"📤 {command} 전송 → neopixel/{device_id}")

@register_handler("handle_water_level")
def handle_water_level(payload, client, context):
    sensor_id = payload.get("sensor_id", "water_level_1")
    print(f"💧 수위 센서 감지: {sensor_id}")
    publish_hex_flash(client, context, "#0045FD", sensor_id=sensor_id, duration_sec=5, source_payload=payload)
    alert_message("수위 감지", "수위 센서에서 수위가 감지 되었으니 물 넘치는 것을 확인을 해주세요.")

@register_handler("handle_doorbell")
def handle_doorbell(payload, client, context):
    sensor_id = payload.get("sensor_id", "doorbell_1")
    print(f"🔔 초인종(버튼) 감지: {sensor_id}")
    publish_hex_flash(client, context, "#00FD05", sensor_id=sensor_id, duration_sec=5, source_payload=payload)
    alert_message("초인종 버튼 감지", "초인종 버튼이 감지가 되었으니 밖의 문을 확인해주세요.")

@register_handler("register_token")
def register_token(payload, client, context):
    token = payload.get("token")
    if token:
        save_fcm_token(token)
    else:
        print(f"⚠️ FCM 토큰 없음 → payload: {payload}")
