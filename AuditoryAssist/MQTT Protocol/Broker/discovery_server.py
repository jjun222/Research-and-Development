# discovery_server.py
import socket
import json
import time

# MQTT_decision_server.py 와 같은 폴더에 있다고 가정
from MQTT_decision_server import _get_local_ip, BROKER_PORT

DISCOVERY_PORT = 30303  # 앱에서 사용하는 포트와 동일해야 함

def run_discovery_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", DISCOVERY_PORT))

    print(f"🔎 MQTT 브로커 discovery 서버 대기 중... (port={DISCOVERY_PORT})")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.strip()
            if msg == b"MQTT_DISCOVER":
                ip = _get_local_ip()  # 판단서버에서 쓰는 함수 재활용
                resp = json.dumps({"ip": ip, "port": BROKER_PORT}).encode()
                sock.sendto(resp, addr)
                print(f"📤 브로커 정보 응답: {addr} → {resp}")
        except Exception as e:
            print("❌ discovery 서버 에러:", e)
            time.sleep(1)

if __name__ == "__main__":
    run_discovery_server()
