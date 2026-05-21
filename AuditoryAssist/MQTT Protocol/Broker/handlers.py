# handlers.py
from handler_registry import register_handler
import json
import threading
import socket
import time
from firebase.firebase_utils import send_fcm_messages, save_fcm_token

print("✅ handlers.py 로드됨 - 핸들러 등록 완료")


def publish_json(client, topic, payload_dict, qos=0, retain=False):
    client.publish(topic, json.dumps(payload_dict), qos=qos, retain=retain)


def _now_ms():
    return int(time.time() * 1000)


def is_fire_alert_active(context):
    """
    MQTT_decision_server.py의 publish_fire_alert()가 설정한 fire_alert_until_ms를 확인한다.
    화재 경고 중에는 개별 센서 색상 flash가 빨간 점멸을 덮어쓰면 안 된다.
    """
    until = int(context.get("fire_alert_until_ms", 0) or 0)
    now = _now_ms()

    if until <= now:
        context["fire_alert_until_ms"] = 0
        return False

    return True


def skip_if_recent_red(context):
    if context.get("just_triggered", False):
        print("🔕 최근 flash 발생 → 개별 flash 생략")
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


def publish_hex_flash(client, context, hex_color, sensor_id=None, duration_sec=5):
    if is_fire_alert_active(context):
        print("🔴 fire_alert active → 개별 센서 hex_flash 생략")
        return

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
            "ts_ms": _now_ms(),
        }

        if sensor_id:
            payload["sensor_id"] = sensor_id

        publish_json(client, f"neopixel/{device_id}", payload, qos=0, retain=False)
        print(f"📤 hex_flash 전송 → neopixel/{device_id} : {payload}")


PUSH_ENABLED = True
_PING_HOST = ("8.8.8.8", 53)
_PING_TIMEOUT = 2


def alert_message(title, body):
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

    threading.Thread(target=_bg_send, daemon=True).start()


@register_handler("handle_shz")
def handle_shz(payload, client, context):
    sid = payload["sensor_id"]
    context["sensor_status"][sid] = True
    context.get("sensor_detected_at_ms", {})[sid] = _now_ms()

    print(f"🔥 불꽃 센서 감지: {sid}")
    publish_hex_flash(client, context, "#FD6A00", sensor_id=sid, duration_sec=5)
    alert_message("불꽃 감지", "불꽃 감지 센서에서 불꽃이 감지 되었습니다.")


@register_handler("handle_mq7")
def handle_mq7(payload, client, context):
    sid = payload["sensor_id"]
    status = payload.get("status", "")
    value = payload.get("value")

    if status == "정상":
        context["sensor_status"][sid] = False
        context.get("sensor_detected_at_ms", {})[sid] = 0
        print(f"✅ MQ7 정상 보고: sensor={sid}, value={value}")
        return

    context["sensor_status"][sid] = True
    context.get("sensor_detected_at_ms", {})[sid] = _now_ms()

    print(f"☠️ MQ7 위험 감지: sensor={sid}, status={status}, value={value}")

    # 교수님 요청 반영:
    # 일산화탄소 감지는 가스 감지와 동일한 보라색으로 표시
    publish_hex_flash(client, context, "#8300FD", sensor_id=sid, duration_sec=5)

    alert_message("일산화탄소 감지", "일산화탄소 센서에서 일산화탄소가 감지 되었습니다.")


@register_handler("handle_gas")
def handle_gas(payload, client, context):
    sid = payload["sensor_id"]
    status = payload.get("status", "")
    value = payload.get("value")

    if status == "정상":
        context["sensor_status"][sid] = False
        context.get("sensor_detected_at_ms", {})[sid] = 0
        print(f"✅ GAS 정상 보고: sensor={sid}, value={value}")
        return

    context["sensor_status"][sid] = True
    context.get("sensor_detected_at_ms", {})[sid] = _now_ms()

    print(f"🧪 GAS 위험 감지: sensor={sid}, status={status}, value={value}")
    publish_hex_flash(client, context, "#8300FD", sensor_id=sid, duration_sec=5)
    alert_message("가스 감지", "가스 센서에서 가스가 감지 되었습니다.")


@register_handler("handle_fire")
def handle_fire(payload, client, context):
    sid = payload["sensor_id"]

    /*
    AI 불 감지 카메라는 더 이상 all-True 조합용 상태값에 넣지 않는다.
    실제 화재 경고 출력은 MQTT_decision_server.py에서 publish_fire_alert()로 직접 처리한다.

    이유:
    - AI 감지 단독으로 무드등 빨간 점멸 + 진동 + 경광등을 실행해야 함
    - 불꽃/CO/가스 3개 all-True 조건과 분리해야 함
    */

    print(f"🔥 AI 화재 감지: {sid}")
    alert_message("AI 불 감지", "실시간 카메라에서 불이 감지 되었습니다.")


@register_handler("handle_water_level")
def handle_water_level(payload, client, context):
    sensor_id = payload.get("sensor_id", "water_level_1")

    print(f"💧 수위 센서 감지: {sensor_id}")
    publish_hex_flash(client, context, "#0045FD", sensor_id=sensor_id, duration_sec=5)
    alert_message("수위 감지", "수위 센서에서 수위가 감지 되었으니 물 넘치는 것을 확인을 해주세요.")


@register_handler("handle_doorbell")
def handle_doorbell(payload, client, context):
    sensor_id = payload.get("sensor_id", "doorbell_1")

    print(f"🔔 초인종(버튼) 감지: {sensor_id}")
    publish_hex_flash(client, context, "#00FD05", sensor_id=sensor_id, duration_sec=5)
    alert_message("초인종 버튼 감지", "초인종 버튼이 감지가 되었으니 밖의 문을 확인해주세요.")


@register_handler("register_token")
def register_token(payload, client, context):
    token = payload.get("token")

    if token:
        save_fcm_token(token)
    else:
        print(f"⚠️ FCM 토큰 없음 → payload: {payload}")
