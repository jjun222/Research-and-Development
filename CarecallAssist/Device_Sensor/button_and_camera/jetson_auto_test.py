#!/usr/bin/env python3

import json
import socket
import threading
import time

import paho.mqtt.client as mqtt


# 젯슨 UDP 탐색 설정
DISCOVERY_PORT = 30303
DISCOVERY_MESSAGE = b"MQTT_DISCOVER"
DISCOVERY_TIMEOUT = 5

# MQTT 설정
MQTT_KEEPALIVE = 30
STATUS_TOPIC = "carecall/rpi5/status"
HEARTBEAT_TOPIC = "carecall/rpi5/heartbeat"

connected_event = threading.Event()


def discover_jetson():
    """
    같은 공유기 안에서 UDP 브로드캐스트를 전송하여
    젯슨의 IP와 MQTT 포트를 자동으로 찾습니다.
    """

    while True:
        print("[DISCOVERY] 젯슨을 찾는 중...")

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPROTO_UDP,
        )

        try:
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_BROADCAST,
                1,
            )
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )

            sock.settimeout(DISCOVERY_TIMEOUT)
            sock.bind(("", 0))

            sock.sendto(
                DISCOVERY_MESSAGE,
                ("255.255.255.255", DISCOVERY_PORT),
            )

            while True:
                data, sender = sock.recvfrom(4096)

                try:
                    response = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    print("[DISCOVERY] 잘못된 응답 무시:", data)
                    continue

                device = response.get("device", "")
                host = response.get("host", sender[0])
                port = int(response.get("port", 1883))

                if device and device != "jetson_orin_nano":
                    print("[DISCOVERY] 다른 장치 응답 무시:", device)
                    continue

                print("[DISCOVERY] 젯슨 발견")
                print(f"  device : {device}")
                print(f"  host   : {host}")
                print(f"  port   : {port}")

                return host, port

        except socket.timeout:
            print("[DISCOVERY] 응답 없음, 2초 후 다시 탐색합니다.")

        except OSError as error:
            print("[DISCOVERY] UDP 오류:", error)

        finally:
            sock.close()

        time.sleep(2)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("[MQTT] 젯슨 MQTT 연결 성공")

        connected_event.set()

        client.publish(
            STATUS_TOPIC,
            payload="online",
            qos=1,
            retain=True,
        )

        client.subscribe(
            "carecall/jetson/commands/#",
            qos=1,
        )

    else:
        print(f"[MQTT] 연결 실패: reason_code={reason_code}")
        connected_event.clear()


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
        print(f"[MQTT] 연결 끊김: reason_code={reason_code}")


def on_message(client, userdata, message):
    try:
        payload = message.payload.decode("utf-8")
    except UnicodeDecodeError:
        payload = repr(message.payload)

    print(
        f"[MQTT 수신] topic={message.topic}, "
        f"payload={payload}"
    )


def create_mqtt_client():
    client_id = f"rpi5-{socket.gethostname()}"

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.reconnect_delay_set(
        min_delay=1,
        max_delay=10,
    )

    client.will_set(
        STATUS_TOPIC,
        payload="offline",
        qos=1,
        retain=True,
    )

    return client


def main():
    while True:
        client = None
        connected_event.clear()

        try:
            jetson_host, jetson_port = discover_jetson()

            client = create_mqtt_client()

            print(
                f"[MQTT] 연결 시도: "
                f"{jetson_host}:{jetson_port}"
            )

            client.connect(
                host=jetson_host,
                port=jetson_port,
                keepalive=MQTT_KEEPALIVE,
            )

            client.loop_start()

            if not connected_event.wait(timeout=10):
                raise TimeoutError("MQTT 연결 시간 초과")

            while connected_event.is_set():
                message = {
                    "device": "raspberry_pi_5",
                    "hostname": socket.gethostname(),
                    "timestamp": int(time.time()),
                }

                result = client.publish(
                    HEARTBEAT_TOPIC,
                    payload=json.dumps(message),
                    qos=0,
                    retain=False,
                )

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    print("[MQTT] heartbeat 전송:", message)
                else:
                    print(
                        "[MQTT] heartbeat 전송 실패:",
                        result.rc,
                    )

                for _ in range(10):
                    if not connected_event.is_set():
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            print("\n[종료] 사용자가 프로그램을 종료했습니다.")
            break

        except Exception as error:
            print("[오류]", error)
            print("[재연결] 2초 후 젯슨을 다시 찾습니다.")
            time.sleep(2)

        finally:
            connected_event.clear()

            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

                try:
                    client.loop_stop()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
