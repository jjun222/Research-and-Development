"""CareCall limb UDP sensor node for Raspberry Pi Pico 2 W.

Hardware:
  BNO085 VIN -> 3V3(OUT), GND -> GND, SDA -> GP4, SCL -> GP5
  SW-420 VCC -> 3V3(OUT), GND -> GND, DO -> GP16

Network:
  1. Load saved Wi-Fi or receive it directly from the Jetson setup AP.
  2. Broadcast discover_chest JSON on UDP port 5004.
  3. Accept a chest_offer response and use the response sender IP.
  4. Send this limb's sensor JSON to the offered UDP data port.
"""

import gc
import machine
import network
import os
import socket
import time
import ubinascii
import ujson

from machine import I2C, Pin

from bno08x import (
    BNO08X,
    BNO_REPORT_ACCELEROMETER,
    BNO_REPORT_GAME_ROTATION_VECTOR,
    BNO_REPORT_GYROSCOPE,
)

from config import (
    ACCEL_REPORT_HZ,
    BNO085_I2C_FREQ,
    BNO085_I2C_TIMEOUT_US,
    BNO085_SCL_PIN,
    BNO085_SDA_PIN,
    CHEST_OFFER_TTL_MS,
    DEVICE_ID,
    DISCOVERY_INTERVAL_MS,
    DISCOVERY_RECEIVE_BYTES,
    FRAME_ID,
    GYRO_REPORT_HZ,
    HEARTBEAT_INTERVAL_MS,
    I2C_ID,
    IMU_SEND_INTERVAL_MS,
    PICO_HOSTNAME,
    QUATERNION_REPORT_HZ,
    SENSOR_READ_INTERVAL_MS,
    SHOCK_ACTIVE_LEVEL,
    SHOCK_DEBOUNCE_MS,
    SW420_DO_PIN,
    TOPIC_HEARTBEAT,
    TOPIC_IMU,
    TOPIC_SHOCK,
    TOPIC_STATUS,
    UDP_DATA_PORT_DEFAULT,
    UDP_DISCOVERY_PORT,
    WIFI_RECONNECT_DELAY_MS,
)
from wifi_provision import connect_wifi_forever


wlan = network.WLAN(network.STA_IF)
sequence = 0

# Keep only the 20 most recent IMU packets in memory.
MAX_STORED_DATA = 20
recent_imu_data = []
imu_data_counter = 0


def make_boot_id():
    try:
        random_part = ubinascii.hexlify(os.urandom(4)).decode()
    except Exception:
        random_part = "{:08x}".format(time.ticks_ms() & 0xFFFFFFFF)

    device_part = ubinascii.hexlify(machine.unique_id()).decode()
    return "{}-{}".format(device_part, random_part)


BOOT_ID = make_boot_id()


def next_sequence():
    global sequence
    sequence = (sequence + 1) & 0x7FFFFFFF
    return sequence


def now_ms():
    # Pico has no battery-backed clock. This is uptime, not Unix time.
    return time.ticks_ms()


def rounded(value):
    return round(float(value), 6)


def json_bytes(payload):
    return ujson.dumps(payload).encode()


def add_recent_imu_data(payload):
    """Store one IMU packet and discard the oldest packet after 20."""
    global imu_data_counter

    imu_data_counter += 1
    stored_data = dict(payload)
    stored_data["data_id"] = imu_data_counter
    recent_imu_data.append(stored_data)

    if len(recent_imu_data) > MAX_STORED_DATA:
        recent_imu_data.pop(0)

    # Reassign visible positions from 1 through 20 after every update.
    for index, item in enumerate(recent_imu_data):
        item["display_no"] = index + 1


def print_recent_imu_data():
    """Print each visible position together with its original data ID."""
    summary = []

    for item in recent_imu_data:
        summary.append((item["display_no"], item["data_id"]))

    print("[BUFFER] slot/data_id:", summary)


def connect_wifi():
    credentials = connect_wifi_forever(wlan)
    print(
        "[WIFI] ready ssid={} ip={}".format(
            credentials["ssid"],
            wlan.ifconfig()[0],
        )
    )


def calculate_broadcast_address():
    ip_text = wlan.ifconfig()[0]
    mask_text = wlan.ifconfig()[1]

    try:
        ip_parts = [int(part) for part in ip_text.split(".")]
        mask_parts = [int(part) for part in mask_text.split(".")]

        broadcast_parts = []
        for ip_part, mask_part in zip(ip_parts, mask_parts):
            broadcast_parts.append(ip_part | (255 - mask_part))

        return ".".join(str(part) for part in broadcast_parts)
    except Exception:
        return "255.255.255.255"


def create_udp_sockets():
    discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    discovery_socket.bind(("0.0.0.0", 0))
    discovery_socket.setblocking(False)

    data_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return discovery_socket, data_socket


def send_discovery(discovery_socket):
    request = {
        "schema_version": 1,
        "type": "discover_chest",
        "device_id": DEVICE_ID,
    }
    encoded = json_bytes(request)

    directed_broadcast = calculate_broadcast_address()
    targets = [directed_broadcast]

    if directed_broadcast != "255.255.255.255":
        targets.append("255.255.255.255")

    sent = False
    for target in targets:
        try:
            discovery_socket.sendto(
                encoded,
                (target, UDP_DISCOVERY_PORT),
            )
            sent = True
        except OSError as error:
            print("[DISCOVERY] send warning:", target, error)

    if sent:
        print("[DISCOVERY] searching for chest_pico on UDP", UDP_DISCOVERY_PORT)


def poll_chest_offer(discovery_socket):
    latest_offer = None

    while True:
        try:
            data, sender = discovery_socket.recvfrom(DISCOVERY_RECEIVE_BYTES)
        except OSError:
            break

        try:
            offer = ujson.loads(data.decode())

            if offer.get("schema_version") != 1:
                continue

            if offer.get("type") != "chest_offer":
                continue

            data_port = int(
                offer.get("data_port", UDP_DATA_PORT_DEFAULT)
            )

            if data_port < 1 or data_port > 65535:
                continue

            latest_offer = {
                "address": (sender[0], data_port),
                "gateway_id": offer.get("gateway_id", "chest"),
            }

        except Exception as error:
            print("[DISCOVERY] invalid offer:", error)

    return latest_offer


def initialize_bno085():
    i2c = I2C(
        I2C_ID,
        sda=Pin(BNO085_SDA_PIN),
        scl=Pin(BNO085_SCL_PIN),
        freq=BNO085_I2C_FREQ,
        timeout=BNO085_I2C_TIMEOUT_US,
    )

    addresses = i2c.scan()
    print("[I2C] found:", [hex(address) for address in addresses])

    if 0x4A not in addresses and 0x4B not in addresses:
        raise RuntimeError("BNO085 was not found at 0x4A or 0x4B")

    bno = BNO08X(i2c, debug=False)
    bno.enable_feature(BNO_REPORT_ACCELEROMETER, ACCEL_REPORT_HZ)
    bno.enable_feature(BNO_REPORT_GYROSCOPE, GYRO_REPORT_HZ)
    bno.enable_feature(
        BNO_REPORT_GAME_ROTATION_VECTOR,
        QUATERNION_REPORT_HZ,
    )
    bno.set_quaternion_euler_vector(BNO_REPORT_GAME_ROTATION_VECTOR)

    print("[BNO085] initialized")
    return bno


def read_imu(bno):
    # This driver returns quaternion in i, j, k, real order.
    qx, qy, qz, qw = bno.quaternion
    gx, gy, gz = bno.gyro
    ax, ay, az = bno.acc

    return {
        "qx": rounded(qx),
        "qy": rounded(qy),
        "qz": rounded(qz),
        "qw": rounded(qw),
        "gx": rounded(gx),
        "gy": rounded(gy),
        "gz": rounded(gz),
        "ax": rounded(ax),
        "ay": rounded(ay),
        "az": rounded(az),
    }


def base_packet(packet_type, topic):
    return {
        "schema_version": 1,
        "type": packet_type,
        "topic": topic,
        "device_id": DEVICE_ID,
        "boot_id": BOOT_ID,
        "sequence": next_sequence(),
        "timestamp_ms": now_ms(),
    }


def send_packet(data_socket, chest_address, payload):
    try:
        data_socket.sendto(json_bytes(payload), chest_address)
        return True
    except OSError as error:
        print("[UDP] send error:", error)
        return False


def send_status(data_socket, chest_address, online, imu_online):
    payload = base_packet("status", TOPIC_STATUS)
    payload["online"] = 1 if online else 0
    payload["imu_online"] = 1 if imu_online else 0
    payload["ip"] = wlan.ifconfig()[0] if wlan.isconnected() else ""
    send_packet(data_socket, chest_address, payload)


def send_heartbeat(data_socket, chest_address, imu_online):
    try:
        rssi = wlan.status("rssi") if wlan.isconnected() else None
    except Exception:
        rssi = None

    payload = base_packet("heartbeat", TOPIC_HEARTBEAT)
    payload["online"] = 1
    payload["imu_online"] = 1 if imu_online else 0
    payload["rssi"] = rssi
    send_packet(data_socket, chest_address, payload)


def send_shock(data_socket, chest_address, shock):
    payload = base_packet("sensor_data", TOPIC_SHOCK)
    payload["shock"] = int(shock)

    if send_packet(data_socket, chest_address, payload):
        print("[SHOCK] sent:", int(shock))


def send_imu(data_socket, chest_address, imu, shock):
    payload = base_packet("sensor_data", TOPIC_IMU)
    payload["frame_id"] = FRAME_ID
    payload["qx"] = imu["qx"]
    payload["qy"] = imu["qy"]
    payload["qz"] = imu["qz"]
    payload["qw"] = imu["qw"]
    payload["gx"] = imu["gx"]
    payload["gy"] = imu["gy"]
    payload["gz"] = imu["gz"]
    payload["ax"] = imu["ax"]
    payload["ay"] = imu["ay"]
    payload["az"] = imu["az"]
    payload["shock"] = int(shock)

    # Save the newest packet. When this is the 21st packet, the oldest
    # packet is removed and the remaining positions become 1 through 20.
    add_recent_imu_data(payload)

    if send_packet(data_socket, chest_address, payload):
        print("[IMU] sent sequence:", payload["sequence"])
        print_recent_imu_data()


def run_udp_session(bno, shock_pin):
    discovery_socket, data_socket = create_udp_sockets()

    chest_address = None
    chest_gateway_id = None
    last_offer_at = None

    now = time.ticks_ms()
    last_discovery = time.ticks_add(now, -DISCOVERY_INTERVAL_MS)
    last_sensor_read = time.ticks_add(now, -SENSOR_READ_INTERVAL_MS)
    last_imu_send = time.ticks_add(now, -IMU_SEND_INTERVAL_MS)
    last_heartbeat = time.ticks_add(now, -HEARTBEAT_INTERVAL_MS)

    raw_last = shock_pin.value()
    stable_raw = raw_last
    raw_changed_at = now
    shock = 1 if stable_raw == SHOCK_ACTIVE_LEVEL else 0

    cached_imu = None
    imu_online = True

    try:
        while True:
            now = time.ticks_ms()

            if not wlan.isconnected():
                raise OSError("Wi-Fi disconnected")

            if time.ticks_diff(now, last_discovery) >= DISCOVERY_INTERVAL_MS:
                last_discovery = now
                send_discovery(discovery_socket)

            offer = poll_chest_offer(discovery_socket)

            if offer is not None:
                offered_address = offer["address"]
                address_changed = offered_address != chest_address

                chest_address = offered_address
                chest_gateway_id = offer["gateway_id"]
                last_offer_at = now

                if address_changed:
                    print(
                        "[DISCOVERY] chest_pico found:",
                        chest_gateway_id,
                        chest_address,
                    )
                    send_status(data_socket, chest_address, True, imu_online)
                    send_shock(data_socket, chest_address, shock)
                    last_imu_send = time.ticks_add(
                        now,
                        -IMU_SEND_INTERVAL_MS,
                    )
                    last_heartbeat = time.ticks_add(
                        now,
                        -HEARTBEAT_INTERVAL_MS,
                    )

            if (
                chest_address is not None
                and last_offer_at is not None
                and time.ticks_diff(now, last_offer_at) > CHEST_OFFER_TTL_MS
            ):
                print("[DISCOVERY] chest_pico offer expired; searching again")
                chest_address = None
                chest_gateway_id = None
                last_offer_at = None

            raw = shock_pin.value()

            if raw != raw_last:
                raw_last = raw
                raw_changed_at = now

            if (
                raw != stable_raw
                and time.ticks_diff(now, raw_changed_at) >= SHOCK_DEBOUNCE_MS
            ):
                stable_raw = raw
                shock = 1 if stable_raw == SHOCK_ACTIVE_LEVEL else 0

                if chest_address is not None:
                    send_shock(data_socket, chest_address, shock)

            if (
                time.ticks_diff(now, last_sensor_read)
                >= SENSOR_READ_INTERVAL_MS
            ):
                last_sensor_read = now

                try:
                    cached_imu = read_imu(bno)
                    imu_online = True
                except Exception as error:
                    cached_imu = None
                    imu_online = False
                    print("[BNO085] read error:", error)

            if (
                chest_address is not None
                and cached_imu is not None
                and time.ticks_diff(now, last_imu_send)
                >= IMU_SEND_INTERVAL_MS
            ):
                last_imu_send = now
                send_imu(
                    data_socket,
                    chest_address,
                    cached_imu,
                    shock,
                )

            if (
                chest_address is not None
                and time.ticks_diff(now, last_heartbeat)
                >= HEARTBEAT_INTERVAL_MS
            ):
                last_heartbeat = now
                send_heartbeat(data_socket, chest_address, imu_online)
                gc.collect()

            time.sleep_ms(10)

    except KeyboardInterrupt:
        if chest_address is not None:
            send_status(data_socket, chest_address, False, False)
        raise

    finally:
        try:
            discovery_socket.close()
        except Exception:
            pass

        try:
            data_socket.close()
        except Exception:
            pass


def main():
    print("\n[BOOT] CareCall limb UDP node starting")
    print("[BOOT] device_id:", DEVICE_ID)
    print("[BOOT] boot_id:", BOOT_ID)
    print("[BOOT] discovery port:", UDP_DISCOVERY_PORT)

    shock_pin = Pin(SW420_DO_PIN, Pin.IN)

    while True:
        try:
            bno = initialize_bno085()
            break
        except KeyboardInterrupt:
            raise
        except Exception as error:
            print("[BNO085] initialization error:", error)
            print("[BNO085] retrying in 3 seconds")
            time.sleep_ms(3_000)

    while True:
        try:
            connect_wifi()
            run_udp_session(bno, shock_pin)

        except KeyboardInterrupt:
            print("[SYSTEM] stopped from Thonny")
            raise

        except Exception as error:
            print("[SYSTEM] network error:", error)

            try:
                wlan.disconnect()
            except Exception:
                pass

            print("[SYSTEM] reconnecting Wi-Fi")
            time.sleep_ms(WIFI_RECONNECT_DELAY_MS)


main()
