#!/usr/bin/env python3

import json
import socket
import threading
import time
from signal import pause

import paho.mqtt.client as mqtt
from gpiozero import Button


# ============================================================
# 라즈베리파이 버튼 설정
# ============================================================

# BCM GPIO 번호
# GPIO17은 물리 핀 11번입니다.
# 현재 버튼이 다른 GPIO에 연결되어 있으면 이 값만 변경하세요.
BUTTON_GPIO = 17

# 버튼 채터링 방지 시간
BUTTON_BOUNCE_TIME = 0.05

# 버튼 MQTT 토픽
BUTTON_TOPIC = "sensor/wall_button_01/button"


# ============================================================
# 젯슨 자동 탐색 설정
# ============================================================

DISCOVERY_PORT = 30303
DISCOVERY_MESSAGE = b"MQTT_DISCOVER"
DISCOVERY_TIMEOUT = 5

EXPECTED_DEVICE = "jetson_orin_nano"


# ============================================================
# MQTT 설정
# ============================================================

MQTT_KEEPALIVE = 30
MQTT_QOS = 1

connected_event = threading.Event()
client_lock = threading.Lock()

mqtt_client = None


# ============================================================
# 버튼 설정
# ============================================================

# pull_up=True:
# 버튼 한쪽은 GPIO, 다른 쪽은 GND에 연결
button = Button(
    BUTTON_GPIO,
    pull_up=True,
    bounce_time=BUTTON_BOUNCE_TIME,
)


def discover_jetson():
    """
    같은 공유기 안에서 UDP 브로드캐스트를 전송하여
    젯슨의 MQTT 브로커 IP와 포트를 자동 탐색합니다.
    """

    while True:
        print("[DISCOVERY] 젯슨을 찾는 중...")

        discovery_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPROTO_UDP,
        )

        try:
            discovery_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_BROADCAST,
                1,
            )

            discovery_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )

            discovery_socket.settimeout(DISCOVERY_TIMEOUT)
            discovery_socket.bind(("", 0))

            discovery_socket.sendto(
                DISCOVERY_MESSAGE,
                ("255.255.255.255", DISCOVERY_PORT),
            )

            while True:
                data, sender_address = discovery_socket.recvfrom(4096)

                try:
                    response = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    print("[DISCOVERY] 잘못된 응답 무시:", data)
                    continue

                device = str(response.get("device", ""))
                host = str(
                    response.get("host", sender_address[0])
                )
                port = int(response.get("port", 1883))

                if (
                    EXPECTED_DEVICE
                    and device
                    and device != EXPECTED_DEVICE
                ):
                    print(
                        "[DISCOVERY] 다른 장치 응답 무시:",
                        device,
                    )
                    continue

                print("[DISCOVERY] 젯슨 발견")
                print(f"  device : {device or 'unknown'}")
                print(f"  host   : {host}")
                print(f"  port   : {port}")

                return host, port

        except socket.timeout:
            print(
                "[DISCOVERY] 젯슨 응답 없음. "
                "2초 후 다시 탐색합니다."
            )

        except OSError as error:
            print("[DISCOVERY] UDP 오류:", error)

        finally:
            discovery_socket.close()

        time.sleep(2)


def publish_button_state(value):
    """
    버튼 상태를 MQTT로 전송합니다.

    눌림: 1
    해제: 0
    """

    global mqtt_client

    if not connected_event.is_set():
        print(
            f"[BUTTON] MQTT 연결 안 됨, 전송 실패: {value}"
        )
        return

    with client_lock:
        client = mqtt_client

    if client is None:
        print("[BUTTON] MQTT 클라이언트 없음")
        return

    payload = str(int(value))

    try:
        result = client.publish(
            topic=BUTTON_TOPIC,
            payload=payload,
            qos=MQTT_QOS,
            retain=False,
        )

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            if value == 1:
                print(
                    f"[BUTTON] 눌림 전송 → "
                    f"{BUTTON_TOPIC} : 1"
                )
            else:
                print(
                    f"[BUTTON] 해제 전송 → "
                    f"{BUTTON_TOPIC} : 0"
                )
        else:
            print(
                f"[BUTTON] 전송 실패: rc={result.rc}"
            )

    except Exception as error:
        print("[BUTTON] MQTT 전송 오류:", error)


def button_pressed():
    """버튼을 눌렀을 때 실행됩니다."""

    publish_button_state(1)


def button_released():
    """버튼에서 손을 뗐을 때 실행됩니다."""

    publish_button_state(0)


def on_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties,
):
    if reason_code == 0:
        print("[MQTT] 젯슨 MQTT 연결 성공")

        connected_event.set()

        # 연결 직후 현재 버튼 상태 동기화
        current_state = 1 if button.is_pressed else 0

        print(
            f"[MQTT] 현재 버튼 상태 동기화: "
            f"{current_state}"
        )

        publish_button_state(current_state)

    else:
        connected_event.clear()

        print(
            f"[MQTT] 연결 실패: "
            f"reason_code={reason_code}"
        )


def on_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties,
):
    connected_event.clear()

    if reason_code == 0:
        print("[MQTT] 정상 연결 종료")
    else:
        print(
            f"[MQTT] 연결 끊김: "
            f"reason_code={reason_code}"
        )


def create_mqtt_client():
    """Paho MQTT 클라이언트를 생성합니다."""

    client_id = f"wall-button-01-{socket.gethostname()}"

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    client.reconnect_delay_set(
        min_delay=1,
        max_delay=10,
    )

    return client


def main():
    global mqtt_client

    # 버튼 눌림·해제 이벤트 등록
    button.when_pressed = button_pressed
    button.when_released = button_released

    print("========================================")
    print(" CareCall 벽면 버튼 MQTT")
    print("========================================")
    print(f"GPIO  : BCM {BUTTON_GPIO}")
    print(f"Topic : {BUTTON_TOPIC}")
    print("누름  : 1")
    print("해제  : 0")
    print("========================================")

    while True:
        client = None

        try:
            connected_event.clear()

            jetson_host, jetson_port = discover_jetson()

            client = create_mqtt_client()

            with client_lock:
                mqtt_client = client

            print(
                f"[MQTT] 젯슨 연결 시도: "
                f"{jetson_host}:{jetson_port}"
            )

            client.connect(
                host=jetson_host,
                port=jetson_port,
                keepalive=MQTT_KEEPALIVE,
            )

            # MQTT 네트워크 송수신 스레드 시작
            client.loop_start()

            if not connected_event.wait(timeout=10):
                raise TimeoutError("MQTT 연결 시간 초과")

            print("[SYSTEM] 버튼 입력 대기 중...")

            # MQTT 연결이 유지되는 동안 대기
            while connected_event.is_set():
                time.sleep(1)

            print(
                "[SYSTEM] 연결이 끊어져 "
                "젯슨을 다시 탐색합니다."
            )

        except KeyboardInterrupt:
            print("\n[SYSTEM] 프로그램을 종료합니다.")
            break

        except Exception as error:
            print("[ERROR]", error)
            print(
                "[SYSTEM] 2초 후 젯슨을 "
                "다시 탐색합니다."
            )
            time.sleep(2)

        finally:
            connected_event.clear()

            with client_lock:
                mqtt_client = None

            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

                try:
                    client.loop_stop()
                except Exception:
                    pass

    button.close()


if __name__ == "__main__":
    main()
