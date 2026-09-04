#!/usr/bin/env python3

import socket
import json
import subprocess
import time

DISCOVERY_PORT = 30303
MQTT_PORT = 1883
DISCOVERY_KEYWORD = b"MQTT_DISCOVER"


def get_jetson_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        try:
            result = subprocess.check_output(["hostname", "-I"]).decode().strip()
            return result.split()[0]
        except Exception:
            return "127.0.0.1"


def main():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind(("", DISCOVERY_PORT))

    print(f"MQTT discovery server started on UDP port {DISCOVERY_PORT}")

    while True:
        try:
            data, addr = udp_sock.recvfrom(1024)
            message = data.strip()

            if message == DISCOVERY_KEYWORD:
                jetson_ip = get_jetson_ip()

                response = {
                    "device": "jetson_orin_nano",
                    "service": "mqtt_broker",
                    "host": jetson_ip,
                    "port": MQTT_PORT,
                    "timestamp": time.time()
                }

                udp_sock.sendto(json.dumps(response).encode(), addr)
                print(f"Discovery response sent to {addr}: {response}")

        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
