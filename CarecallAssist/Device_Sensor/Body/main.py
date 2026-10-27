"""CareCall Body raw relay for Jetson relative calculations.
Device IDs, pins, topics and sampling intervals are preserved.
Network deadlines, bounded sensor polling and recovery are enabled.
GP16 forwards calibration epochs; limb packets include a raw chest reference.
Relative quaternion, angle and direction math executes only on Jetson.
"""

import gc
import sys
import machine
import math
import network
import socket
import time
import ubinascii
import ujson

from machine import I2C, Pin
from umqtt.simple import MQTTClient, CARECALL_MQTT_VERSION

from bno08x import (
    BNO08X,
    CARECALL_BNO_VERSION,
    BNO_REPORT_ACCELEROMETER,
    BNO_REPORT_GAME_ROTATION_VECTOR,
    BNO_REPORT_GYROSCOPE,
)
from config import *
from wifi_provision import connect_wifi_forever


# ---------------------------------------------------------------------------
# Chest BNO085 settings
# These values are kept here so the existing chest config.py can be reused.
# ---------------------------------------------------------------------------
CHEST_I2C_ID = 0
CHEST_BNO085_SDA_PIN = 4
CHEST_BNO085_SCL_PIN = 5
CHEST_BNO085_I2C_FREQ = 100_000
CHEST_BNO085_I2C_TIMEOUT_US = 200_000

CHEST_ACCEL_REPORT_HZ = 20
CHEST_GYRO_REPORT_HZ = 20
CHEST_QUATERNION_REPORT_HZ = 10

CHEST_IMU_READ_INTERVAL_MS = 100
CHEST_IMU_PUBLISH_INTERVAL_MS = 2_000
CHEST_IMU_RETRY_MS = 5_000
CHEST_IMU_STALE_MS = 1_000
CHEST_IMU_MAX_CONSECUTIVE_ERRORS = 3

CHEST_IMU_TOPIC = "sensor/{}/imu".format(GATEWAY_ID)
REMOTE_DEVICES = tuple(ALLOWED_DEVICES)
SENSOR_DEVICES = (GATEWAY_ID,) + REMOTE_DEVICES

NEUTRAL_LIMB_DIRECTION = (0.0, 0.0, -1.0)
QUATERNION_EPSILON = 0.000000001

# Calibration button wiring: GP16 -> push button -> GND.
# The internal pull-up keeps the unpressed value at 1; pressed is 0.
CALIBRATION_BUTTON_PIN = 16
CALIBRATION_BUTTON_ACTIVE_LEVEL = 0
CALIBRATION_BUTTON_DEBOUNCE_MS = 50


wlan = network.WLAN(network.STA_IF)

mqtt_client = None
last_mqtt_attempt_ms = None
last_mqtt_ping_ms = time.ticks_ms()

chest_bno = None
latest_chest_imu = None
last_chest_imu_attempt_ms = None
last_chest_imu_read_ms = time.ticks_add(
    time.ticks_ms(),
    -CHEST_IMU_READ_INTERVAL_MS,
)
last_chest_imu_publish_ms = time.ticks_add(
    time.ticks_ms(),
    -CHEST_IMU_PUBLISH_INTERVAL_MS,
)
chest_imu_errors = 0
chest_sequence = 0

devices = {}

# The first relative quaternion received for each limb is the neutral pose.
orientation_references = {}

calibration_button = Pin(
    CALIBRATION_BUTTON_PIN,
    Pin.IN,
    Pin.PULL_UP,
)
calibration_button_last_raw = calibration_button.value()
calibration_button_stable = calibration_button_last_raw
calibration_button_changed_ms = time.ticks_ms()

# Each sensor keeps only its 20 most recent packets in RAM.
buffers = {
    device_id: []
    for device_id in SENSOR_DEVICES
}

try:
    led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    led = None


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------
# CareCall Jetson relative-rotation relay v1.
JETSON_CALIBRATION_EPOCH = 0
JETSON_LIMB_CALIBRATION_EPOCH = {}
JETSON_IMU_MAX_QUEUE_MS = 1000


TRACE_ENABLED = False
watchdog = None
last_stage = "boot"
flush_cursor = 0
last_dns_attempt_ms = None
cached_broker_ip = None
network_failure_count = 0
last_radio_reset_ms = None
buffer_stats = {d: {"sent": 0, "expired": 0, "superseded": 0, "overflow": 0}
                for d in SENSOR_DEVICES}


def stage(name):
    global last_stage
    last_stage = name
    if TRACE_ENABLED:
        print("[STAGE]", name)


def feed_watchdog():
    if watchdog is not None:
        watchdog.feed()


def report_error(label, error):
    print("[ERROR] {} stage={} error={}".format(label, last_stage, error))
    sys.print_exception(error)


def local_tasks():
    # Called by Wi-Fi provisioning between its bounded network operations.
    poll_calibration_button(time.ticks_ms())
    stage("bno_poll")
    poll_chest_imu(time.ticks_ms())
    feed_watchdog()
    stage("wifi_wait")


def close_network_socket(sock):
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def maybe_reset_radio():
    global last_radio_reset_ms, network_failure_count
    if network_failure_count < 6:
        return
    now = time.ticks_ms()
    if (last_radio_reset_ms is not None and
            time.ticks_diff(now, last_radio_reset_ms) < 60000):
        return
    last_radio_reset_ms = now
    network_failure_count = 0
    print("[WIFI RECOVERY] repeated MQTT failures; restarting WLAN")
    reset_mqtt()
    wlan.active(False)
    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < WIFI_RESET_OFF_MS:
        local_tasks()
        time.sleep_ms(20)
    # run() closes old UDP sockets and reconnects with saved credentials.


def expired_imu(item, now):
    if not item["topic"].endswith("/imu"):
        return False
    age = time.ticks_diff(now, item["queued_at_ms"])
    return age < 0 or age > JETSON_IMU_MAX_QUEUE_MS


def set_led(on):
    if led is not None:
        led.value(1 if on else 0)


def rounded(value):
    return round(float(value), 6)


def next_chest_sequence():
    global chest_sequence
    chest_sequence = (chest_sequence + 1) & 0x7FFFFFFF
    return chest_sequence


def make_boot_id():
    import os
    unique = ubinascii.hexlify(machine.unique_id()).decode()
    random = ubinascii.hexlify(os.urandom(8)).decode()
    return unique + "-" + random


CHEST_BOOT_ID = make_boot_id()


def close_udp(udp_socket):
    if udp_socket is None:
        return

    try:
        udp_socket.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Quaternion and angle calculation
# Quaternion order is (x, y, z, w), matching the supplied bno08x.py.
# ---------------------------------------------------------------------------
def quaternion_normalize(quaternion):
    x, y, z, w = quaternion
    magnitude = math.sqrt(x * x + y * y + z * z + w * w)

    if magnitude < QUATERNION_EPSILON:
        raise ValueError("zero-length quaternion")

    normalized = (
        x / magnitude,
        y / magnitude,
        z / magnitude,
        w / magnitude,
    )

    # q and -q represent the same orientation. Keeping w positive makes
    # the values easier to read and prevents unnecessary sign jumps in logs.
    if normalized[3] < 0.0:
        return tuple(-value for value in normalized)

    return normalized


def quaternion_conjugate(quaternion):
    x, y, z, w = quaternion
    return (-x, -y, -z, w)


def quaternion_multiply(first, second):
    ax, ay, az, aw = first
    bx, by, bz, bw = second

    return quaternion_normalize((
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ))


def quaternion_to_euler_degrees(quaternion):
    x, y, z, w = quaternion_normalize(quaternion)

    sin_roll_cos_pitch = 2.0 * (w * x + y * z)
    cos_roll_cos_pitch = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sin_roll_cos_pitch, cos_roll_cos_pitch)

    sin_pitch = 2.0 * (w * y - z * x)
    if sin_pitch > 1.0:
        sin_pitch = 1.0
    elif sin_pitch < -1.0:
        sin_pitch = -1.0
    pitch = math.asin(sin_pitch)

    sin_yaw_cos_pitch = 2.0 * (w * z + x * y)
    cos_yaw_cos_pitch = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(sin_yaw_cos_pitch, cos_yaw_cos_pitch)

    radians_to_degrees = 180.0 / math.pi

    return (
        roll * radians_to_degrees,
        pitch * radians_to_degrees,
        yaw * radians_to_degrees,
    )


def rotate_vector(quaternion, vector):
    x, y, z, w = quaternion_normalize(quaternion)
    vx, vy, vz = vector

    # Rotation matrix produced directly from q * v * conjugate(q).
    return (
        (1.0 - 2.0 * (y * y + z * z)) * vx
        + 2.0 * (x * y - z * w) * vy
        + 2.0 * (x * z + y * w) * vz,
        2.0 * (x * y + z * w) * vx
        + (1.0 - 2.0 * (x * x + z * z)) * vy
        + 2.0 * (y * z - x * w) * vz,
        2.0 * (x * z - y * w) * vx
        + 2.0 * (y * z + x * w) * vy
        + (1.0 - 2.0 * (x * x + y * y)) * vz,
    )


def quaternion_from_message(message):
    return quaternion_normalize((
        float(message["qx"]),
        float(message["qy"]),
        float(message["qz"]),
        float(message["qw"]),
    ))


def reset_orientation_reference(device_id, reason):
    JETSON_LIMB_CALIBRATION_EPOCH[device_id] = (
        JETSON_LIMB_CALIBRATION_EPOCH.get(device_id, 0) + 1
    )
    if device_id in orientation_references:
        del orientation_references[device_id]

    print(
        "[CALIBRATION RESET] device={} reason={}".format(
            device_id,
            reason,
        )
    )


def reset_all_orientation_references(reason):
    global JETSON_CALIBRATION_EPOCH
    JETSON_CALIBRATION_EPOCH += 1
    orientation_references.clear()
    print("[CALIBRATION RESET] all limbs reason={}".format(reason))


def poll_calibration_button(now_ms):
    """Debounce GP16 and reset every limb reference once per button press."""
    global calibration_button_last_raw
    global calibration_button_stable
    global calibration_button_changed_ms

    raw = calibration_button.value()

    if raw != calibration_button_last_raw:
        calibration_button_last_raw = raw
        calibration_button_changed_ms = now_ms
        return

    if raw == calibration_button_stable:
        return

    if (
        time.ticks_diff(now_ms, calibration_button_changed_ms)
        < CALIBRATION_BUTTON_DEBOUNCE_MS
    ):
        return

    calibration_button_stable = raw

    if raw == CALIBRATION_BUTTON_ACTIVE_LEVEL:
        print("[CALIBRATION BUTTON] GP16 pressed")
        reset_all_orientation_references("gpio16_button")
        print(
            "[CALIBRATION] waiting for the next IMU packet "
            "from each limb"
        )


def chest_imu_is_fresh(now_ms):
    if latest_chest_imu is None:
        return False
    age = time.ticks_diff(now_ms, latest_chest_imu["sampled_at_ms"])
    return 0 <= age <= CHEST_IMU_STALE_MS


def add_relative_orientation(message, now_ms):
    """Copy raw chest reference; all relative math runs on Jetson."""
    device_id = message["device_id"]
    for key in list(message):
        if (key.startswith("relative_q") or key.startswith("change_")
                or key.startswith("direction_")
                or key in ("neutral_calibrated_now", "orientation_error")):
            del message[key]
    message["jetson_relay_version"] = 1
    message["calculation_mode"] = "jetson"
    message["gateway_boot_id"] = CHEST_BOOT_ID
    message["gateway_calibration_epoch"] = JETSON_CALIBRATION_EPOCH
    message["limb_calibration_epoch"] = JETSON_LIMB_CALIBRATION_EPOCH.get(device_id, 0)
    message["raw_orientation_valid"] = 1
    # A legacy bridge must not mistake an uncomputed orientation for a result.
    message["orientation_valid"] = 0
    message["orientation_frame"] = "sensor_raw"
    reference = {"valid": 0}
    message["chest_reference"] = reference
    if latest_chest_imu is None:
        reference["reason"] = "chest_imu_unavailable"
        return False
    age_ms = time.ticks_diff(now_ms, latest_chest_imu["sampled_at_ms"])
    reference["sample_age_ms"] = age_ms
    if age_ms < 0 or age_ms > CHEST_IMU_STALE_MS:
        reference["reason"] = "chest_imu_stale"
        return False
    q = latest_chest_imu["quaternion"]
    reference["qx"], reference["qy"], reference["qz"], reference["qw"] = q
    reference["valid"] = 1
    return True


# ---------------------------------------------------------------------------
# Chest BNO085
# ---------------------------------------------------------------------------
def initialize_chest_bno085():
    stage("bno_init")
    feed_watchdog()
    i2c = I2C(CHEST_I2C_ID, sda=Pin(CHEST_BNO085_SDA_PIN),
              scl=Pin(CHEST_BNO085_SCL_PIN), freq=CHEST_BNO085_I2C_FREQ,
              timeout=CHEST_BNO085_I2C_TIMEOUT_US)
    addresses = i2c.scan()
    print("[CHEST I2C] found:", [hex(a) for a in addresses])
    if not any(a in addresses for a in (0x4A, 0x4B)):
        raise RuntimeError("chest BNO085 not found at 0x4A or 0x4B")
    bno = BNO08X(i2c, debug=False)
    feed_watchdog()
    for report, rate in ((BNO_REPORT_ACCELEROMETER, CHEST_ACCEL_REPORT_HZ),
                         (BNO_REPORT_GYROSCOPE, CHEST_GYRO_REPORT_HZ),
                         (BNO_REPORT_GAME_ROTATION_VECTOR, CHEST_QUATERNION_REPORT_HZ)):
        bno.enable_feature(report, rate)
        feed_watchdog()
    bno.set_quaternion_euler_vector(BNO_REPORT_GAME_ROTATION_VECTOR)
    print("[CHEST BNO085] bounded driver initialized")
    return bno


def read_chest_imu(bno, sampled_at_ms):
    bno.poll(max_packets=8, max_time_ms=25)
    q, received_ms = bno.sample(BNO_REPORT_GAME_ROTATION_VECTOR, CHEST_IMU_STALE_MS)
    gyro, _ = bno.sample(BNO_REPORT_GYROSCOPE, CHEST_IMU_STALE_MS)
    acc, _ = bno.sample(BNO_REPORT_ACCELEROMETER, CHEST_IMU_STALE_MS)
    quaternion = quaternion_normalize(q)
    values = tuple(quaternion) + tuple(gyro) + tuple(acc)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Non-finite chest IMU")
    result = {"quaternion": quaternion, "sampled_at_ms": received_ms}
    for name, value in zip(("qx", "qy", "qz", "qw", "gx", "gy", "gz", "ax", "ay", "az"), values):
        result[name] = rounded(value)
    return result


def ensure_chest_bno(now_ms):
    global chest_bno
    global last_chest_imu_attempt_ms
    global chest_imu_errors

    if chest_bno is not None:
        return True

    if last_chest_imu_attempt_ms is not None:
        age_ms = time.ticks_diff(now_ms, last_chest_imu_attempt_ms)
        if age_ms < CHEST_IMU_RETRY_MS:
            return False

    last_chest_imu_attempt_ms = now_ms

    try:
        chest_bno = initialize_chest_bno085()
        chest_imu_errors = 0
        reset_all_orientation_references("chest_imu_initialized")
        return True

    except Exception as error:
        chest_bno = None
        print("[CHEST BNO085] initialization error:", error)
        return False


def build_chest_imu_message(imu):
    message = {
        "schema_version": SCHEMA_VERSION,
        "type": "sensor_data",
        "topic": CHEST_IMU_TOPIC,
        "device_id": GATEWAY_ID,
        "boot_id": CHEST_BOOT_ID,
        "sequence": next_chest_sequence(),
        "timestamp_ms": imu["sampled_at_ms"],
        "frame_id": "chest_imu_link",
        "qx": imu["qx"],
        "qy": imu["qy"],
        "qz": imu["qz"],
        "qw": imu["qw"],
        "gx": imu["gx"],
        "gy": imu["gy"],
        "gz": imu["gz"],
        "ax": imu["ax"],
        "ay": imu["ay"],
        "az": imu["az"],
    }

    message["jetson_relay_version"] = 1
    message["raw_orientation_valid"] = 1
    message["gateway_boot_id"] = CHEST_BOOT_ID
    message["gateway_calibration_epoch"] = JETSON_CALIBRATION_EPOCH
    return message


def poll_chest_imu(now_ms):
    global chest_bno
    global latest_chest_imu
    global last_chest_imu_read_ms
    global last_chest_imu_publish_ms
    global chest_imu_errors

    if not ensure_chest_bno(now_ms):
        return

    if (
        time.ticks_diff(now_ms, last_chest_imu_read_ms)
        < CHEST_IMU_READ_INTERVAL_MS
    ):
        return

    now_ms = time.ticks_ms()
    last_chest_imu_read_ms = now_ms

    try:
        latest_chest_imu = read_chest_imu(chest_bno, now_ms)
        chest_imu_errors = 0

    except Exception as error:
        chest_imu_errors += 1
        print(
            "[CHEST BNO085] read error {}/{}: {}".format(
                chest_imu_errors,
                CHEST_IMU_MAX_CONSECUTIVE_ERRORS,
                error,
            )
        )

        if chest_imu_errors >= CHEST_IMU_MAX_CONSECUTIVE_ERRORS:
            chest_bno = None
            latest_chest_imu = None
            reset_all_orientation_references("chest_imu_read_failure")

        return

    now_ms = time.ticks_ms()
    if (
        time.ticks_diff(now_ms, last_chest_imu_publish_ms)
        < CHEST_IMU_PUBLISH_INTERVAL_MS
    ):
        return

    last_chest_imu_publish_ms = now_ms
    message = build_chest_imu_message(latest_chest_imu)
    add_to_buffer(GATEWAY_ID, CHEST_IMU_TOPIC, message)
    print_chest_imu(message)


# ---------------------------------------------------------------------------
# Wi-Fi and MQTT
# ---------------------------------------------------------------------------
def connect_wifi():
    global cached_broker_ip, last_dns_attempt_ms
    stage("wifi_connect")
    credentials = connect_wifi_forever(wlan, idle_callback=local_tasks)
    cached_broker_ip = None
    last_dns_attempt_ms = None
    address = wlan.ifconfig()
    print("[WIFI] ready ssid={} ip={} gateway={}".format(
        credentials["ssid"], address[0], address[2]))
    set_led(True)
    feed_watchdog()


def mqtt_status_payload(status):
    message = {
        "schema_version": SCHEMA_VERSION,
        "type": "status",
        "topic": "device/{}/status".format(GATEWAY_ID),
        "device_id": GATEWAY_ID,
        "boot_id": CHEST_BOOT_ID,
        "status": status,
        "timestamp_ms": time.ticks_ms(),
        "imu_online": 1 if chest_bno is not None else 0,
    }

    return ujson.dumps(message).encode("utf-8")


def reset_mqtt():
    global mqtt_client
    client, mqtt_client = mqtt_client, None
    # Never write DISCONNECT or an offline PUBLISH over a failed connection.
    # The broker's existing Last Will reports ungraceful disconnection.
    if client is not None:
        client.close()


def connect_mqtt():
    global mqtt_client, last_mqtt_ping_ms, cached_broker_ip
    global last_dns_attempt_ms, network_failure_count
    now = time.ticks_ms()
    if cached_broker_ip is None:
        if (last_dns_attempt_ms is not None and
                time.ticks_diff(now, last_dns_attempt_ms) < MQTT_RESOLVE_RETRY_MS):
            return False
        last_dns_attempt_ms = now
        stage("mqtt_dns")
        print("[MQTT DNS] resolving", JETSON_HOST)
        feed_watchdog()
        cached_broker_ip = socket.getaddrinfo(
            JETSON_HOST, MQTT_PORT, 0, socket.SOCK_STREAM)[0][-1][0]
    client = MQTTClient(b"chest_pico-" + ubinascii.hexlify(machine.unique_id()),
                        cached_broker_ip, port=MQTT_PORT, user=MQTT_USER,
                        password=MQTT_PASSWORD, keepalive=MQTT_KEEPALIVE)
    status_topic = "device/{}/status".format(GATEWAY_ID).encode()
    client.set_last_will(status_topic, mqtt_status_payload("offline"), retain=True, qos=1)
    try:
        stage("mqtt_connect")
        print("[MQTT CONNECT] {}:{}".format(cached_broker_ip, MQTT_PORT))
        client.connect(clean_session=True, timeout=MQTT_CONNECT_TIMEOUT_SECONDS)
        feed_watchdog()
        client.set_timeout(MQTT_SOCKET_TIMEOUT_SECONDS)
        stage("mqtt_online_ack")
        client.publish(status_topic, mqtt_status_payload("online"), retain=True, qos=1)
    except BaseException:
        client.close()
        cached_broker_ip = None
        raise
    mqtt_client = client
    last_mqtt_ping_ms = time.ticks_ms()
    network_failure_count = 0
    print("[MQTT] connected {}:{}".format(cached_broker_ip, MQTT_PORT))
    feed_watchdog()
    return True


def ensure_mqtt():
    global last_mqtt_attempt_ms, network_failure_count
    if mqtt_client is not None:
        return True
    if not wlan.isconnected():
        return False
    now = time.ticks_ms()
    if (last_mqtt_attempt_ms is not None and
            time.ticks_diff(now, last_mqtt_attempt_ms) < MQTT_RETRY_MS):
        return False
    last_mqtt_attempt_ms = now
    try:
        return connect_mqtt()
    except Exception as error:
        network_failure_count += 1
        report_error("MQTT connection", error)
        reset_mqtt()
        return False


def maintain_mqtt():
    global last_mqtt_ping_ms, network_failure_count
    if not ensure_mqtt():
        return
    try:
        stage("mqtt_receive")
        # A finite number of inbound packets per loop.
        for _ in range(4):
            if mqtt_client.check_msg() is None:
                break
            feed_watchdog()
        now = time.ticks_ms()
        pending = mqtt_client.ping_outstanding_ms
        if pending is not None:
            if time.ticks_diff(now, pending) > int(MQTT_SOCKET_TIMEOUT_SECONDS * 1000):
                raise OSError(110, "No MQTT PINGRESP")
        elif time.ticks_diff(now, last_mqtt_ping_ms) >= MQTT_PING_MS:
            stage("mqtt_ping")
            mqtt_client.ping()
            last_mqtt_ping_ms = time.ticks_ms()
    except Exception as error:
        network_failure_count += 1
        report_error("MQTT maintenance", error)
        reset_mqtt()


def mqtt_options(topic):
    if topic.endswith("/shock"):
        return 1, False

    if topic.endswith("/status"):
        return 1, True

    return 0, False


# ---------------------------------------------------------------------------
# Per-sensor FIFO buffers
# ---------------------------------------------------------------------------
def add_to_buffer(device_id, topic, message):
    qos, retain = mqtt_options(topic)
    pending = buffers[device_id]
    stats = buffer_stats[device_id]
    now = time.ticks_ms()
    for index in range(len(pending) - 1, -1, -1):
        if expired_imu(pending[index], now):
            pending.pop(index)
            stats["expired"] += 1
    # IMU/heartbeat/status describe current state; keep their newest value.
    # Shock events remain a FIFO and are never coalesced.
    if not topic.endswith("/shock"):
        for index in range(len(pending) - 1, -1, -1):
            if pending[index]["topic"] == topic:
                pending.pop(index)
                stats["superseded"] += 1
    if len(pending) >= BUFFER_LIMIT:
        index = None
        for candidate in range(len(pending)):
            if not pending[candidate]["topic"].endswith("/shock"):
                index = candidate
                break
        if index is None and not topic.endswith("/shock"):
            stats["overflow"] += 1
            print("[QUEUE OVERFLOW] {} rejected={}".format(device_id, topic))
            return
        if index is None:
            index = 0
        removed = pending.pop(index)
        stats["overflow"] += 1
        print("[QUEUE OVERFLOW] {} removed={}".format(device_id, removed["topic"]))
    pending.append({"topic": topic, "message": dict(message), "qos": qos,
                    "retain": retain, "queued_at_ms": now, "sent": False})


def flush_buffers():
    global flush_cursor, network_failure_count
    if mqtt_client is None:
        return
    sent_count = 0
    checked = 0
    while sent_count < MQTT_FLUSH_PER_LOOP and checked < len(SENSOR_DEVICES):
        device_id = SENSOR_DEVICES[flush_cursor]
        flush_cursor = (flush_cursor + 1) % len(SENSOR_DEVICES)
        checked += 1
        pending = buffers[device_id]
        now = time.ticks_ms()
        for index in range(len(pending) - 1, -1, -1):
            if expired_imu(pending[index], now):
                pending.pop(index)
                buffer_stats[device_id]["expired"] += 1
        if not pending:
            continue
        item = pending[0]
        try:
            message = item["message"]
            if item["topic"].endswith("/imu"):
                message["gateway_queue_age_ms"] = time.ticks_diff(
                    time.ticks_ms(), item["queued_at_ms"])
            payload = ujson.dumps(message).encode()
            stage("mqtt_publish:{}:qos{}".format(item["topic"], item["qos"]))
            mqtt_client.publish(item["topic"].encode(), payload,
                                retain=item["retain"], qos=item["qos"])
        except Exception as error:
            network_failure_count += 1
            report_error("MQTT publish", error)
            reset_mqtt()
            return
        pending.pop(0)
        buffer_stats[device_id]["sent"] += 1
        sent_count += 1
        print("[MQTT TX] {} {}".format(device_id, item["topic"]))
        feed_watchdog()


# ---------------------------------------------------------------------------
# UDP discovery and limb data
# ---------------------------------------------------------------------------
def open_udp(port):
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        udp_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )
    except Exception:
        pass

    udp_socket.bind(("0.0.0.0", port))
    udp_socket.setblocking(False)
    return udp_socket


def read_json(raw, source):
    try:
        message = ujson.loads(raw.decode("utf-8"))
        if not isinstance(message, dict):
            raise ValueError("UDP JSON must be an object")
        return message
    except Exception as error:
        print("[DROP] invalid JSON from {}: {}".format(source[0], error))
        return None


def valid_topic(device_id, topic):
    return topic in (
        "device/{}/status".format(device_id),
        "device/{}/heartbeat".format(device_id),
        "sensor/{}/imu".format(device_id),
        "sensor/{}/shock".format(device_id),
    )


def handle_discovery(raw, source, discovery_udp):
    message = read_json(raw, source)

    if message is None:
        return

    if message.get("schema_version") != SCHEMA_VERSION:
        return

    if message.get("type") != "discover_chest":
        return

    device_id = message.get("device_id")

    if device_id not in REMOTE_DEVICES:
        print("[DROP] unknown discovery device={}".format(device_id))
        return

    offer = {
        "schema_version": SCHEMA_VERSION,
        "type": "chest_offer",
        "gateway_id": GATEWAY_ID,
        "data_port": DATA_PORT,
    }

    discovery_udp.sendto(
        ujson.dumps(offer).encode("utf-8"),
        source,
    )
    print("[DISCOVERY] {} at {}".format(device_id, source[0]))


def update_device_boot_id(device_id, message, info):
    boot_id = message.get("boot_id")

    if boot_id is None:
        return

    previous_boot_id = info.get("boot_id")

    if previous_boot_id is not None and previous_boot_id != boot_id:
        reset_orientation_reference(device_id, "limb_rebooted")

    info["boot_id"] = boot_id


def print_chest_imu(message):
    print(
        "[CHEST IMU] seq={} "
        "q=({:.4f},{:.4f},{:.4f},{:.4f}) "
        "gyro=({:.3f},{:.3f},{:.3f}) "
        "acc=({:.3f},{:.3f},{:.3f}) "
        "buffer={}/{}".format(
            message["sequence"],
            message["qx"],
            message["qy"],
            message["qz"],
            message["qw"],
            message["gx"],
            message["gy"],
            message["gz"],
            message["ax"],
            message["ay"],
            message["az"],
            len(buffers[GATEWAY_ID]),
            BUFFER_LIMIT,
        )
    )


def print_limb_imu(message):
    print("[JETSON RELAY] device={} seq={} chest_reference_valid={} epoch={}".format(
        message["device_id"], message.get("sequence", "-"),
        message.get("chest_reference", {}).get("valid", 0),
        message.get("gateway_calibration_epoch", 0),
    ))


def handle_data(raw, source):
    message = read_json(raw, source)

    if message is None:
        return

    if message.get("schema_version") != SCHEMA_VERSION:
        print("[DROP] schema mismatch")
        return

    device_id = message.get("device_id")
    topic = message.get("topic")

    if device_id not in REMOTE_DEVICES:
        print("[DROP] unknown device={}".format(device_id))
        return

    if not valid_topic(device_id, topic):
        print("[DROP] invalid topic={}".format(topic))
        return

    now_ms = time.ticks_ms()
    info = devices.get(device_id)

    if info is None:
        info = {
            "online": False,
            "ip": source[0],
            "last_seen_ms": now_ms,
            "packets": 0,
            "boot_id": None,
        }
        devices[device_id] = info

    update_device_boot_id(device_id, message, info)

    if not info["online"]:
        print("[ONLINE] {} ip={}".format(device_id, source[0]))

    info["online"] = True
    info["ip"] = source[0]
    info["last_seen_ms"] = now_ms
    info["packets"] += 1

    if topic == "sensor/{}/imu".format(device_id):
        add_relative_orientation(message, now_ms)
        add_to_buffer(device_id, topic, message)
        print_limb_imu(message)
    else:
        add_to_buffer(device_id, topic, message)
        print(
            "[RX] device={} topic={} seq={} buffer={}/{}".format(
                device_id,
                topic,
                message.get("sequence", "-"),
                len(buffers[device_id]),
                BUFFER_LIMIT,
            )
        )


def drain_udp(udp_socket, handler, extra=None):
    for _ in range(20):
        try:
            raw, source = udp_socket.recvfrom(2048)
        except OSError as error:
            if error.args and error.args[0] in (11, 35, 110, 116, 119):
                break
            raise
        try:
            if extra is None:
                handler(raw, source)
            else:
                handler(raw, source, extra)
        except (ValueError, TypeError, KeyError) as error:
            print("[DROP] malformed UDP from {}: {}".format(source[0], error))


def update_offline(now_ms):
    for device_id in REMOTE_DEVICES:
        info = devices.get(device_id)

        if info is None or not info["online"]:
            continue

        elapsed_ms = time.ticks_diff(now_ms, info["last_seen_ms"])

        if elapsed_ms >= DEVICE_OFFLINE_MS:
            info["online"] = False
            print("[OFFLINE] {}".format(device_id))


def print_summary(now_ms):
    mqtt_state = "disconnected"
    rx_age = -1
    if mqtt_client is not None:
        mqtt_state = "awaiting_pingresp" if mqtt_client.ping_outstanding_ms is not None else "connected"
        if mqtt_client.last_rx_ms is not None:
            rx_age = time.ticks_diff(now_ms, mqtt_client.last_rx_ms)
    chest_state = "online" if chest_imu_is_fresh(now_ms) else "offline"
    print("[SUMMARY] mqtt={} mqtt_rx_age_ms={} chest_imu={} free={} stage={}".format(
        mqtt_state, rx_age, chest_state, gc.mem_free(), last_stage))
    for device_id in SENSOR_DEVICES:
        if device_id == GATEWAY_ID:
            state, calibrated = chest_state, "reference"
        else:
            info = devices.get(device_id)
            state = "not_seen" if info is None else ("online" if info["online"] else "offline")
            calibrated = "jetson"
        stats = buffer_stats[device_id]
        print("  {}: {} pending={}/{} sent={} expired={} superseded={} overflow={} calibrated={}".format(
            device_id, state, len(buffers[device_id]), BUFFER_LIMIT,
            stats["sent"], stats["expired"], stats["superseded"],
            stats["overflow"], calibrated))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run():
    global watchdog
    discovery_udp = None
    data_udp = None
    last_summary = time.ticks_ms()
    print("=== CareCall chest relay: bounded recovery 2026-09-14 ===")
    print("boot_id={} reset_cause={}".format(CHEST_BOOT_ID, machine.reset_cause()))
    print("MQTT={} BNO_patch={}".format(CARECALL_MQTT_VERSION, CARECALL_BNO_VERSION))
    print("Jetson={}:{} IDs={} GP4/GP5 IMU, GP16 calibration".format(JETSON_HOST, MQTT_PORT, SENSOR_DEVICES))
    if WATCHDOG_ENABLED:
        watchdog = machine.WDT(timeout=WATCHDOG_TIMEOUT_MS)
        print("[WDT] enabled timeout_ms={}".format(WATCHDOG_TIMEOUT_MS))
    try:
        while True:
            try:
                stage("local_tasks")
                poll_calibration_button(time.ticks_ms())
                stage("bno_poll")
                poll_chest_imu(time.ticks_ms())
                feed_watchdog()
                stage("wifi_check")
                if not wlan.isconnected():
                    set_led(False)
                    reset_mqtt()
                    close_udp(discovery_udp)
                    close_udp(data_udp)
                    discovery_udp = data_udp = None
                    connect_wifi()
                if discovery_udp is None:
                    stage("udp_open")
                    discovery_udp = open_udp(DISCOVERY_PORT)
                    data_udp = open_udp(DATA_PORT)
                    print("[UDP] listening {}, {}".format(DISCOVERY_PORT, DATA_PORT))
                stage("udp_discovery")
                drain_udp(discovery_udp, handle_discovery, discovery_udp)
                stage("udp_data")
                drain_udp(data_udp, handle_data)
                feed_watchdog()
                maintain_mqtt()
                flush_buffers()
                maybe_reset_radio()
                now = time.ticks_ms()
                update_offline(now)
                if time.ticks_diff(now, last_summary) >= SUMMARY_INTERVAL_MS:
                    print_summary(now)
                    gc.collect()
                    last_summary = now
                feed_watchdog()
                time.sleep_ms(20)
            except Exception as error:
                # Print the original exception BEFORE any cleanup.
                report_error("main loop", error)
                reset_mqtt()
                close_udp(discovery_udp)
                close_udp(data_udp)
                discovery_udp = data_udp = None
                gc.collect()
                feed_watchdog()
                time.sleep_ms(100)
    except KeyboardInterrupt:
        print("[CHEST] stopped; an already-running WDT will reset the Pico")
    finally:
        reset_mqtt()
        close_udp(discovery_udp)
        close_udp(data_udp)
        set_led(False)


run()


