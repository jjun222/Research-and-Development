"""CareCall limb UDP sensor node for Raspberry Pi Pico 2 W.

This main.py is shared by:
  - right_arm
  - left_arm
  - right_leg
  - left_leg

Each limb uses its own config.py for:
  DEVICE_ID
  PICO_HOSTNAME
  FRAME_ID
  TOPIC_STATUS
  TOPIC_HEARTBEAT
  TOPIC_IMU
  TOPIC_SHOCK

Hardware:
  BNO085
    VIN -> 3V3(OUT)
    GND -> GND
    SDA -> GP4
    SCL -> GP5

  SW-420
    VCC -> 3V3(OUT)
    GND -> GND
    DO  -> GP16

Network behavior:
  1. Connect to saved Wi-Fi.
  2. Send discover_chest every DISCOVERY_INTERVAL_MS.
  3. Receive chest_offer and remember the sender IP.
  4. Once a chest address is known, keep using it.
  5. Missing chest_offer packets do NOT clear chest_address.
  6. Discovery continues in the background.
  7. If the chest DHCP IP changes, a new chest_offer updates
     chest_address automatically.
  8. WLAN is reset only when Wi-Fi really disconnects or when
     a real UDP data-send error occurs.
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
    DEVICE_ID,
    DISCOVERY_INTERVAL_MS,
    DISCOVERY_RECEIVE_BYTES,
    FRAME_ID,
    GYRO_REPORT_HZ,
    HEARTBEAT_INTERVAL_MS,
    I2C_ID,
    IMU_SEND_INTERVAL_MS,
    QUATERNION_REPORT_HZ,
    SCHEMA_VERSION,
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
    WIFI_DISABLE_POWER_SAVE,
    WIFI_RECONNECT_DELAY_MS,
    WIFI_RESET_OFF_MS,
)

from wifi_provision import connect_wifi_forever


# ============================================================
# Global WLAN
# ============================================================

wlan = network.WLAN(network.STA_IF)


# ============================================================
# Sequence / boot ID
# ============================================================

sequence = 0


# ============================================================
# IMU ring buffer
#
# Keep only the newest 20 packets in RAM.
# ============================================================

MAX_STORED_DATA = 20

recent_imu_data = []

imu_data_counter = 0


# ============================================================
# Boot ID
# ============================================================

def make_boot_id():
    try:
        random_part = ubinascii.hexlify(
            os.urandom(4)
        ).decode()

    except Exception:
        random_part = "{:08x}".format(
            time.ticks_ms() & 0xFFFFFFFF
        )

    device_part = ubinascii.hexlify(
        machine.unique_id()
    ).decode()

    return "{}-{}".format(
        device_part,
        random_part,
    )


BOOT_ID = make_boot_id()


# ============================================================
# Sequence
# ============================================================

def next_sequence():
    global sequence

    sequence = (
        sequence + 1
    ) & 0x7FFFFFFF

    return sequence


# ============================================================
# Time
# ============================================================

def now_ms():
    # Pico has no battery-backed RTC.
    # This value is uptime, not Unix time.
    return time.ticks_ms()


# ============================================================
# Utility
# ============================================================

def rounded(value):
    return round(
        float(value),
        6,
    )


def json_bytes(payload):
    return ujson.dumps(
        payload
    ).encode()


# ============================================================
# IMU buffer
# ============================================================

def add_recent_imu_data(payload):
    """Keep one IMU packet and discard the oldest after 20."""

    global imu_data_counter

    imu_data_counter += 1

    stored_data = dict(payload)

    stored_data["data_id"] = (
        imu_data_counter
    )

    recent_imu_data.append(
        stored_data
    )

    if (
        len(recent_imu_data)
        > MAX_STORED_DATA
    ):
        recent_imu_data.pop(0)

    # Visible positions always become 1 ~ 20.
    for index, item in enumerate(
        recent_imu_data
    ):
        item["display_no"] = (
            index + 1
        )


def print_recent_imu_data():
    summary = []

    for item in recent_imu_data:
        summary.append(
            (
                item["display_no"],
                item["data_id"],
            )
        )

    print(
        "[BUFFER] slot/data_id:",
        summary,
    )


# ============================================================
# WLAN configuration
# ============================================================

def configure_wlan():
    """Enable WLAN and disable power saving when supported."""

    wlan.active(True)

    if not WIFI_DISABLE_POWER_SAVE:
        return

    try:
        wlan.config(
            pm=wlan.PM_NONE
        )

        print(
            "[WIFI] power management disabled"
        )

    except Exception as error:
        # Some MicroPython firmware versions may not
        # support PM_NONE.
        print(
            "[WIFI] power management setting skipped:",
            error,
        )


# ============================================================
# Hard WLAN reset
# ============================================================

def force_reset_wlan(reason):
    """Reset WLAN without rebooting the whole Pico."""

    print(
        "[WIFI WATCHDOG] interface reset reason={}".format(
            reason
        )
    )

    try:
        wlan.disconnect()
    except Exception:
        pass

    try:
        wlan.active(False)
    except Exception:
        pass

    time.sleep_ms(
        WIFI_RESET_OFF_MS
    )

    try:
        wlan.active(True)

    except Exception as error:
        print(
            "[WIFI] interface activate warning:",
            error,
        )

    if WIFI_DISABLE_POWER_SAVE:
        try:
            wlan.config(
                pm=wlan.PM_NONE
            )

        except Exception as error:
            print(
                "[WIFI] PM_NONE warning:",
                error,
            )


# ============================================================
# Wi-Fi connection
# ============================================================

def connect_wifi():
    configure_wlan()

    credentials = connect_wifi_forever(
        wlan
    )

    print(
        "[WIFI] ready ssid={} ip={}".format(
            credentials["ssid"],
            wlan.ifconfig()[0],
        )
    )


# ============================================================
# Broadcast address
# ============================================================

def calculate_broadcast_address():
    ip_text = wlan.ifconfig()[0]
    mask_text = wlan.ifconfig()[1]

    try:
        ip_parts = [
            int(part)
            for part
            in ip_text.split(".")
        ]

        mask_parts = [
            int(part)
            for part
            in mask_text.split(".")
        ]

        broadcast_parts = []

        for ip_part, mask_part in zip(
            ip_parts,
            mask_parts,
        ):
            broadcast_parts.append(
                ip_part
                | (255 - mask_part)
            )

        return ".".join(
            str(part)
            for part
            in broadcast_parts
        )

    except Exception:
        return "255.255.255.255"


# ============================================================
# UDP sockets
# ============================================================

def create_udp_sockets():
    # Discovery socket.
    discovery_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
    )

    discovery_socket.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_BROADCAST,
        1,
    )

    discovery_socket.bind(
        ("0.0.0.0", 0)
    )

    discovery_socket.setblocking(
        False
    )

    # Data socket.
    data_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
    )

    return (
        discovery_socket,
        data_socket,
    )


# ============================================================
# Chest discovery
# ============================================================

def send_discovery(discovery_socket):
    request = {
        "schema_version": SCHEMA_VERSION,
        "type": "discover_chest",
        "device_id": DEVICE_ID,
    }

    encoded = json_bytes(
        request
    )

    directed_broadcast = (
        calculate_broadcast_address()
    )

    targets = [
        directed_broadcast
    ]

    # Also try limited broadcast.
    if (
        directed_broadcast
        != "255.255.255.255"
    ):
        targets.append(
            "255.255.255.255"
        )

    sent = False

    for target in targets:
        try:
            discovery_socket.sendto(
                encoded,
                (
                    target,
                    UDP_DISCOVERY_PORT,
                ),
            )

            sent = True

        except OSError as error:
            # Discovery broadcast failure alone must not
            # destroy an already working chest connection.
            print(
                "[DISCOVERY] send warning:",
                target,
                error,
            )

    if sent:
        print(
            "[DISCOVERY] searching for chest_pico on UDP",
            UDP_DISCOVERY_PORT,
        )


# ============================================================
# Receive chest_offer
# ============================================================

def poll_chest_offer(
    discovery_socket,
):
    latest_offer = None

    while True:
        try:
            data, sender = (
                discovery_socket.recvfrom(
                    DISCOVERY_RECEIVE_BYTES
                )
            )

        except OSError:
            # Socket is non-blocking.
            # No packet is available right now.
            break

        try:
            offer = ujson.loads(
                data.decode()
            )

            if (
                offer.get("schema_version")
                != SCHEMA_VERSION
            ):
                continue

            if (
                offer.get("type")
                != "chest_offer"
            ):
                continue

            data_port = int(
                offer.get(
                    "data_port",
                    UDP_DATA_PORT_DEFAULT,
                )
            )

            if (
                data_port < 1
                or data_port > 65535
            ):
                continue

            latest_offer = {
                "address": (
                    sender[0],
                    data_port,
                ),
                "gateway_id": (
                    offer.get(
                        "gateway_id",
                        "chest",
                    )
                ),
            }

        except Exception as error:
            print(
                "[DISCOVERY] invalid offer:",
                error,
            )

    return latest_offer


# ============================================================
# BNO085 initialization
# ============================================================

def initialize_bno085():
    i2c = I2C(
        I2C_ID,
        sda=Pin(
            BNO085_SDA_PIN
        ),
        scl=Pin(
            BNO085_SCL_PIN
        ),
        freq=BNO085_I2C_FREQ,
        timeout=BNO085_I2C_TIMEOUT_US,
    )

    addresses = i2c.scan()

    print(
        "[I2C] found:",
        [
            hex(address)
            for address
            in addresses
        ],
    )

    if (
        0x4A not in addresses
        and 0x4B not in addresses
    ):
        raise RuntimeError(
            "BNO085 was not found at 0x4A or 0x4B"
        )

    bno = BNO08X(
        i2c,
        debug=False,
    )

    bno.enable_feature(
        BNO_REPORT_ACCELEROMETER,
        ACCEL_REPORT_HZ,
    )

    bno.enable_feature(
        BNO_REPORT_GYROSCOPE,
        GYRO_REPORT_HZ,
    )

    bno.enable_feature(
        BNO_REPORT_GAME_ROTATION_VECTOR,
        QUATERNION_REPORT_HZ,
    )

    bno.set_quaternion_euler_vector(
        BNO_REPORT_GAME_ROTATION_VECTOR
    )

    print(
        "[BNO085] initialized"
    )

    return bno


# ============================================================
# Read BNO085
# ============================================================

def read_imu(bno):
    # Driver returns quaternion in:
    # i, j, k, real
    qx, qy, qz, qw = (
        bno.quaternion
    )

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


# ============================================================
# Base UDP packet
# ============================================================

def base_packet(
    packet_type,
    topic,
):
    return {
        "schema_version": SCHEMA_VERSION,
        "type": packet_type,
        "topic": topic,
        "device_id": DEVICE_ID,
        "boot_id": BOOT_ID,
        "sequence": next_sequence(),
        "timestamp_ms": now_ms(),
    }


# ============================================================
# UDP send
# ============================================================

def send_packet(
    data_socket,
    chest_address,
    payload,
):
    try:
        data_socket.sendto(
            json_bytes(payload),
            chest_address,
        )

        return True

    except OSError as error:
        # A real data socket failure is considered
        # a network/session problem.
        print(
            "[UDP] send error:",
            error,
        )

        raise


# ============================================================
# Status
# ============================================================

def send_status(
    data_socket,
    chest_address,
    online,
    imu_online,
):
    payload = base_packet(
        "status",
        TOPIC_STATUS,
    )

    payload["online"] = (
        1 if online else 0
    )

    payload["imu_online"] = (
        1 if imu_online else 0
    )

    if wlan.isconnected():
        payload["ip"] = (
            wlan.ifconfig()[0]
        )
    else:
        payload["ip"] = ""

    send_packet(
        data_socket,
        chest_address,
        payload,
    )


# ============================================================
# Heartbeat
# ============================================================

def send_heartbeat(
    data_socket,
    chest_address,
    imu_online,
):
    try:
        if wlan.isconnected():
            rssi = wlan.status("rssi")
        else:
            rssi = None

    except Exception:
        rssi = None

    payload = base_packet(
        "heartbeat",
        TOPIC_HEARTBEAT,
    )

    payload["online"] = 1

    payload["imu_online"] = (
        1 if imu_online else 0
    )

    payload["rssi"] = rssi

    send_packet(
        data_socket,
        chest_address,
        payload,
    )


# ============================================================
# Shock
# ============================================================

def send_shock(
    data_socket,
    chest_address,
    shock,
):
    payload = base_packet(
        "sensor_data",
        TOPIC_SHOCK,
    )

    payload["shock"] = int(
        shock
    )

    if send_packet(
        data_socket,
        chest_address,
        payload,
    ):
        print(
            "[SHOCK] sent:",
            int(shock),
        )


# ============================================================
# IMU
# ============================================================

def send_imu(
    data_socket,
    chest_address,
    imu,
    shock,
):
    payload = base_packet(
        "sensor_data",
        TOPIC_IMU,
    )

    payload["frame_id"] = (
        FRAME_ID
    )

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

    payload["shock"] = int(
        shock
    )

    # Keep newest 20 IMU packets.
    add_recent_imu_data(
        payload
    )

    if send_packet(
        data_socket,
        chest_address,
        payload,
    ):
        print(
            "[IMU] sent sequence:",
            payload["sequence"],
        )

        print_recent_imu_data()


# ============================================================
# UDP session
# ============================================================

def run_udp_session(
    bno,
    shock_pin,
):
    discovery_socket, data_socket = (
        create_udp_sockets()
    )

    # --------------------------------------------------------
    # Important:
    #
    # Once chest_address is found it stays here.
    #
    # There is intentionally:
    #   no CHEST_OFFER_TTL_MS
    #   no last_offer_at
    #   no chest_offer watchdog
    #
    # A missing chest_offer must NOT clear this address.
    # --------------------------------------------------------

    chest_address = None
    chest_gateway_id = None

    now = time.ticks_ms()

    last_discovery = time.ticks_add(
        now,
        -DISCOVERY_INTERVAL_MS,
    )

    last_sensor_read = time.ticks_add(
        now,
        -SENSOR_READ_INTERVAL_MS,
    )

    last_imu_send = time.ticks_add(
        now,
        -IMU_SEND_INTERVAL_MS,
    )

    last_heartbeat = time.ticks_add(
        now,
        -HEARTBEAT_INTERVAL_MS,
    )

    raw_last = shock_pin.value()

    stable_raw = raw_last

    raw_changed_at = now

    shock = (
        1
        if stable_raw
        == SHOCK_ACTIVE_LEVEL
        else 0
    )

    cached_imu = None
    imu_online = True

    try:
        while True:
            now = time.ticks_ms()

            # ------------------------------------------------
            # Real Wi-Fi loss
            # ------------------------------------------------

            if not wlan.isconnected():
                raise OSError(
                    "Wi-Fi disconnected"
                )

            # ------------------------------------------------
            # Discovery continues forever.
            #
            # It is NOT used as a keepalive.
            # It is mainly used for:
            #
            # 1. Initial chest discovery
            # 2. Detecting a changed DHCP IP
            # ------------------------------------------------

            if (
                time.ticks_diff(
                    now,
                    last_discovery,
                )
                >= DISCOVERY_INTERVAL_MS
            ):
                last_discovery = now

                send_discovery(
                    discovery_socket
                )

            # ------------------------------------------------
            # Receive chest_offer
            # ------------------------------------------------

            offer = poll_chest_offer(
                discovery_socket
            )

            if offer is not None:
                offered_address = (
                    offer["address"]
                )

                address_changed = (
                    offered_address
                    != chest_address
                )

                # Always accept the newest valid offer.
                #
                # Same IP:
                #   address simply stays the same.
                #
                # New DHCP IP:
                #   chest_address is updated automatically.
                chest_address = (
                    offered_address
                )

                chest_gateway_id = (
                    offer["gateway_id"]
                )

                if address_changed:
                    print(
                        "[DISCOVERY] chest_pico found:",
                        chest_gateway_id,
                        chest_address,
                    )

                    # Immediately announce status to the
                    # newly discovered / updated chest.
                    send_status(
                        data_socket,
                        chest_address,
                        True,
                        imu_online,
                    )

                    send_shock(
                        data_socket,
                        chest_address,
                        shock,
                    )

                    # Force next IMU and heartbeat transmission
                    # immediately after address discovery.
                    last_imu_send = (
                        time.ticks_add(
                            now,
                            -IMU_SEND_INTERVAL_MS,
                        )
                    )

                    last_heartbeat = (
                        time.ticks_add(
                            now,
                            -HEARTBEAT_INTERVAL_MS,
                        )
                    )

            # ------------------------------------------------
            # IMPORTANT
            #
            # There is NO code here that does:
            #
            # chest_address = None
            #
            # because chest_offer was not received recently.
            #
            # Once discovered, the address stays valid until:
            #
            # * a new offer updates it, or
            # * the actual Wi-Fi/network session fails.
            # ------------------------------------------------

            # ------------------------------------------------
            # SW-420
            # ------------------------------------------------

            raw = shock_pin.value()

            if raw != raw_last:
                raw_last = raw
                raw_changed_at = now

            if (
                raw != stable_raw
                and time.ticks_diff(
                    now,
                    raw_changed_at,
                )
                >= SHOCK_DEBOUNCE_MS
            ):
                stable_raw = raw

                shock = (
                    1
                    if stable_raw
                    == SHOCK_ACTIVE_LEVEL
                    else 0
                )

                if (
                    chest_address
                    is not None
                ):
                    send_shock(
                        data_socket,
                        chest_address,
                        shock,
                    )

            # ------------------------------------------------
            # BNO085 read
            # ------------------------------------------------

            if (
                time.ticks_diff(
                    now,
                    last_sensor_read,
                )
                >= SENSOR_READ_INTERVAL_MS
            ):
                last_sensor_read = now

                try:
                    cached_imu = (
                        read_imu(bno)
                    )

                    imu_online = True

                except Exception as error:
                    cached_imu = None

                    imu_online = False

                    print(
                        "[BNO085] read error:",
                        error,
                    )

            # ------------------------------------------------
            # IMU transmission
            # ------------------------------------------------

            if (
                chest_address
                is not None
                and cached_imu
                is not None
                and time.ticks_diff(
                    now,
                    last_imu_send,
                )
                >= IMU_SEND_INTERVAL_MS
            ):
                last_imu_send = now

                send_imu(
                    data_socket,
                    chest_address,
                    cached_imu,
                    shock,
                )

            # ------------------------------------------------
            # Heartbeat transmission
            # ------------------------------------------------

            if (
                chest_address
                is not None
                and time.ticks_diff(
                    now,
                    last_heartbeat,
                )
                >= HEARTBEAT_INTERVAL_MS
            ):
                last_heartbeat = now

                send_heartbeat(
                    data_socket,
                    chest_address,
                    imu_online,
                )

                gc.collect()

            time.sleep_ms(10)

    # ========================================================
    # Thonny manual stop
    # ========================================================

    except KeyboardInterrupt:
        if chest_address is not None:
            try:
                send_status(
                    data_socket,
                    chest_address,
                    False,
                    False,
                )

            except Exception:
                pass

        raise

    # ========================================================
    # Always close UDP sockets when session exits
    # ========================================================

    finally:
        try:
            discovery_socket.close()
        except Exception:
            pass

        try:
            data_socket.close()
        except Exception:
            pass


# ============================================================
# Main
# ============================================================

def main():
    print(
        "\n[BOOT] CareCall limb UDP node starting"
    )

    print(
        "[BOOT] device_id:",
        DEVICE_ID,
    )

    print(
        "[BOOT] boot_id:",
        BOOT_ID,
    )

    print(
        "[BOOT] discovery port:",
        UDP_DISCOVERY_PORT,
    )

    print(
        "[BOOT] discovery interval={} ms".format(
            DISCOVERY_INTERVAL_MS
        )
    )

    print(
        "[BOOT] chest address policy="
        "keep until real network failure"
    )


    # ========================================================
    # SW-420
    # ========================================================

    shock_pin = Pin(
        SW420_DO_PIN,
        Pin.IN,
    )


    # ========================================================
    # BNO085 initialization
    # ========================================================

    while True:
        try:
            bno = initialize_bno085()

            break

        except KeyboardInterrupt:
            raise

        except Exception as error:
            print(
                "[BNO085] initialization error:",
                error,
            )

            print(
                "[BNO085] retrying in 3 seconds"
            )

            time.sleep_ms(
                3000
            )


    # ========================================================
    # Network session
    # ========================================================

    while True:
        try:
            connect_wifi()

            run_udp_session(
                bno,
                shock_pin,
            )

        except KeyboardInterrupt:
            print(
                "[SYSTEM] stopped from Thonny"
            )

            raise

        except Exception as error:
            print(
                "[SYSTEM] network/session error:",
                error,
            )

            # Only actual network/session errors arrive here.
            #
            # Missing chest_offer alone does NOT arrive here.
            force_reset_wlan(
                str(error)
            )

            print(
                "[SYSTEM] reconnecting Wi-Fi in {} ms".format(
                    WIFI_RECONNECT_DELAY_MS
                )
            )

            time.sleep_ms(
                WIFI_RECONNECT_DELAY_MS
            )


# ============================================================
# Start
# ============================================================

main()
