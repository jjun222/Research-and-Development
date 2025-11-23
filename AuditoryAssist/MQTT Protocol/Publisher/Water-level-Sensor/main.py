import machine, time, network, ujson
from umqtt.simple import MQTTClient   # 버튼 센서와 동일 스타일 사용

# ========= 하드웨어/네트워크 설정 =========
WATER_PIN = 5
water_switch = machine.Pin(WATER_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

# Wi-Fi
WIFI_SSID = 'HealthcareConvergenceLab'
WIFI_PASSWORD = 'Healthcare1234!'

# MQTT
MQTT_BROKER   = '192.168.0.24'
MQTT_TOPIC    = 'water_level/sensor'   # ✅ MQTT_config.json 기준
MQTT_CLIENTID = 'water_level_1'       # ✅ sensor_id와 동일하게

# 타이밍 파라미터
PING_INTERVAL_MS = 30_000   # 30초마다 ping
CHECK_INTERVAL_MS = 500     # 수위 체크 주기 (0.5초)
DEBOUNCE_SAMPLES  = 10      # 다수결 샘플 수
DEBOUNCE_MIN_HIGH = 7       # 10번중 7번 이상이면 '물 높음'

# 내부 상태
wlan = None
client = None

# ========= 유틸 =========
def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

def wifi_connect_blocking():
    """Wi-Fi가 연결될 때까지 블로킹 재시도."""
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

    if wlan.isconnected():
        return

    print("📡 Wi-Fi 연결 중...")
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        time.sleep(0.5)
    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())

def wifi_ensure():
    """연결이 끊기면 즉시 재연결."""
    if not wlan.isconnected():
        wifi_connect_blocking()

def mqtt_connect_blocking():
    """MQTT 브로커에 블로킹 접속."""
    global client
    client = MQTTClient(MQTT_CLIENTID, MQTT_BROKER)
    client.connect()
    print("✅ MQTT 연결 완료")

def mqtt_ping():
    """가능하면 ping으로 세션 유지."""
    try:
        client.ping()
        return True
    except Exception as e:
        print("⚠️ ping 실패:", e)
        return False

def mqtt_reconnect_with_backoff():
    """브로커 재연결을 지수 백오프로 시도."""
    backoff = 0.5
    for attempt in range(6):  # 0.5 → 1 → 2 → 4 → 5 → 5초
        try:
            wifi_ensure()
            try:
                client.disconnect()
            except:
                pass
            mqtt_connect_blocking()
            return True
        except Exception as e:
            print("❌ MQTT 재연결 실패[%d]:" % (attempt+1), e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
    return False

def publish_json(topic, obj):
    """JSON 안전 발행. 실패 시 자동 재연결 후 재시도."""
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5
    for attempt in range(4):
        try:
            client.publish(topic, msg)
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt+1), e)
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
    return False

# ========= 수위 판단 =========
def is_water_high():
    """
    스위치 값 다수결로 판단.
    water_switch.value() == 0 : 물 높음(스위치 닫힘)
    """
    cnt = 0
    for _ in range(DEBOUNCE_SAMPLES):
        if water_switch.value() == 0:   # 닫힘(물 높음)
            cnt += 1
        time.sleep_ms(5)
    return cnt >= DEBOUNCE_MIN_HIGH

def send_water_alert():
    """
    물 높음(비정상) 상태일 때만 판단 서버로 이벤트 전송.
    MQTT_config.json + handlers.py에 맞춘 payload 형태:
      sensor_id = "water_level_1"
      event     = "water_detected"
    """
    payload = {
        "sensor_id": MQTT_CLIENTID,    # "water_level_1"
        "event": "water_detected",     # ✅ MQTT_config.json expected_event
        "status": "ABNORMAL",          # 참고용 (서버는 현재 status 안 씀)
        "value": 1,                    # 1 = 물 높음
        "timestamp": now_str()
    }
    print("📤 수위 센서 전송 (물 높음):", payload)
    publish_json(MQTT_TOPIC, payload)

# ========= 메인 =========
def main():
    wifi_connect_blocking()
    mqtt_connect_blocking()

    print("📍 수위 센서 모니터링 시작 (자동 복구 모드)")

    prev_state = None  # None / "NORMAL" / "ABNORMAL"
    t_ping = time.ticks_ms()

    while True:
        now = time.ticks_ms()

        # 주기적 Wi-Fi / MQTT 헬스체크
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        # 수위 읽기 + 상태 결정
        if is_water_high():
            state = "ABNORMAL"   # 물 너무 높음
        else:
            state = "NORMAL"     # 정상 수위

        # 상태가 변했을 때만 처리
        if state != prev_state:
            if state == "ABNORMAL":
                print("⚠ 정상 수위를 벗어났습니다.")
                # 👉 이때만 MQTT 판단 서버에 water_detected 이벤트 전송
                send_water_alert()
            else:
                print("✅ 정상 수위입니다. (MQTT 전송 없음)")
                # 필요하면 여기서 "정상" 상태도 서버로 보내도록 확장 가능

            prev_state = state

        time.sleep_ms(CHECK_INTERVAL_MS)

# 자동 실행
main()

