# handlers.py
from handler_registry import register_handler
import json
import threading
import socket
from firebase.firebase_utils import send_fcm_messages, save_fcm_token

print("✅ handlers.py 로드됨 - 핸들러 등록 완료")

# --- flash 중복 방지 (ALL-TRUE 직후 단색 점등 억제) ---
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
    t.daemon = True          # 프로세스 종료 방해 금지
    t.start()

def publish_yellow_flash(client, context, sensor_id=None):
    # (호환용) 필요 시 노란색 점등이 필요한 경우만 사용
    if skip_if_recent_red(context):
        return
    set_yellow_lock(context, delay=5)
    for device_id in set(context["devices"]):
        payload = {"command": "yellow_flash", "alert": True}
        if sensor_id:
            payload["sensor_id"] = sensor_id
        client.publish(f"neopixel/{device_id}", json.dumps(payload), qos=0, retain=False)
        print(f"📤 yellow_flash 전송 → neopixel/{device_id} : {payload}")

def publish_hex_flash(client, context, hex_color, sensor_id=None, duration_sec=5):
    """임의 HEX 색상으로 duration_sec 동안 점등 후 원래 무드색 복귀"""
    if skip_if_recent_red(context):
        return
    set_yellow_lock(context, delay=duration_sec)
    for device_id in set(context["devices"]):
        payload = {
            "command": "hex_flash",
            "color": hex_color,                       # 예: "#FD6A00"
            "duration_ms": int(duration_sec * 1000),
            "alert": True,
            "issuer": "decision_server",
        }
        if sensor_id:
            payload["sensor_id"] = sensor_id
        client.publish(f"neopixel/{device_id}", json.dumps(payload), qos=0, retain=False)
        print(f"📤 hex_flash 전송 → neopixel/{device_id} : {payload}")

# =======================
#  오프라인에서도 블로킹되지 않는 푸시 전송
# =======================
PUSH_ENABLED = True
_PING_HOST = ("8.8.8.8", 53)   # 인터넷 연결 간단 체크용
_PING_TIMEOUT = 2

def alert_message(title, body):
    """FCM 푸시를 별도 데몬 스레드에서 전송(오프라인이면 조용히 스킵)."""
    if not PUSH_ENABLED:
        return
    def _bg_send():
        try:
            # 인터넷 연결 간단 확인(없으면 바로 스킵해 MQTT/핸들러 블로킹 방지)
            try:
                s = socket.create_connection(_PING_HOST, timeout=_PING_TIMEOUT)
                s.close()
            except Exception:
                return
            # 실제 푸시 전송
            send_fcm_messages(title, body)
        except Exception as e:
            print("⚠️ FCM send error:", e)
    th = threading.Thread(target=_bg_send, daemon=True)
    th.start()

# --- 화재 관련 센서 ---
@register_handler("handle_shz")  # 불꽃 감지
def handle_shz(payload, client, context):
    sid = payload["sensor_id"]
    context["sensor_status"][sid] = True
    print(f"🔥 불꽃 센서 감지: {sid}")
    # ✅ 개별 감지 기본색을 주황(#FD6A00)으로 변경 (5초)
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5)
    alert_message("불꽃 감지", "불꽃 감지 센서에서 불꽃이 감지 되었습니다.")

@register_handler("handle_mq7")  # 일산화탄소
def handle_mq7(payload, client, context):
    sid    = payload["sensor_id"]
    status = payload.get("status", "")   # "정상" / ...
    value  = payload.get("value")
    if status == "정상":
        context["sensor_status"][sid] = False
        print(f"✅ MQ7 정상 보고: sensor={sid}, value={value}")
        return
    context["sensor_status"][sid] = True
    print(f"☠️ MQ7 위험 감지: sensor={sid}, status={status}, value={value}")
    # ✅ 주황(#FD6A00) 5초
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5)
    alert_message("일산화탄소 감지", "일산화탄소 센서에서 일산화탄소가 감지 되었습니다.")

@register_handler("handle_gas")  # 가스(MQ5)
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
    # ✅ 가스는 보라색 #8300FD (5초)
    publish_hex_flash(client, context, "#8300FD", sensor_id=sid, duration_sec=5)
    alert_message("가스 감지", "가스 센서에서 가스가 감지 되었습니다.")

@register_handler("handle_fire")  # AI 불
def handle_fire(payload, client, context):
    sid = payload["sensor_id"]
    context["sensor_status"][sid] = True
    print(f"🔥 AI 화재 감지: {sid}")
    # ✅ 개별 감지 기본색 주황(#FD6A00) 5초
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5)
    alert_message("AI 불 감지", "실시간 카메라에서 불이 감지 되었습니다.")

# --- 화재와 별개: 수위/초인종 ---
def publish_to_all_neopixels(client, context, command, extra=None):
    # (호환용) 기존 _blink_3s 명령 유지 필요시 사용
    payload = {"command": command}
    if isinstance(extra, dict):
        payload.update(extra)
    for device_id in set(context.get("devices", [])):
        client.publish(f"neopixel/{device_id}", json.dumps(payload), qos=0, retain=False)
        print(f"📤 {command} 전송 → neopixel/{device_id}")

@register_handler("handle_water_level")
def handle_water_level(payload, client, context):
    sensor_id = payload.get("sensor_id", "water_level_1")
    print(f"💧 수위 센서 감지: {sensor_id}")
    # 3초 → 5초
    publish_hex_flash(client, context, "#0045FD", sensor_id=sensor_id, duration_sec=5)
    alert_message("수위 감지", "수위 센서에서 수위가 감지 되었으니 물 넘치는 것을 확인을 해주세요.")

@register_handler("handle_doorbell")
def handle_doorbell(payload, client, context):
    sensor_id = payload.get("sensor_id", "doorbell_1")
    print(f"🔔 초인종(버튼) 감지: {sensor_id}")
    # 3초 → 5초
    publish_hex_flash(client, context, "#00FD05", sensor_id=sensor_id, duration_sec=5)
    alert_message("초인종 버튼 감지", "초인종 버튼이 감지가 되었으니 밖의 문을 확인해주세요.")

@register_handler("register_token")
def register_token(payload, client, context):
    token = payload.get("token")
    if token:
        save_fcm_token(token)
    else:
        print(f"⚠️ FCM 토큰 없음 → payload: {payload}")
