"""CareCall chest UDP-to-MQTT gateway for Raspberry Pi Pico 2 W.

This version:
  * reads the chest BNO085 on I2C0 (SDA GP4, SCL GP5),
  * receives right/left arm and leg IMU packets over UDP,
  * calculates each limb orientation relative to the chest,
  * calibrates the first valid relative orientation as the neutral pose,
  * recalibrates every limb when the GP16 button is pressed,
  * calculates roll/pitch/yaw change and a unit direction vector,
  * keeps at most 20 packets per sensor in RAM,
  * forwards the enriched packets to jetson.local by MQTT.

It intentionally does not calculate hand/foot positions or body-length-based
coordinates.
"""

import gc
import machine
import math
import network
import socket
import time
import ubinascii
import ujson

from machine import I2C, Pin
from umqtt.simple import MQTTClient

from bno08x import (
    BNO08X,
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
CHEST_BNO085_I2C_TIMEOUT_US = 100_000

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
    try:
        random_part = ubinascii.hexlify(machine.unique_id()).decode("utf-8")
    except Exception:
        random_part = "pico"

    return "{}-{:08x}".format(
        random_part,
        time.ticks_ms() & 0xFFFFFFFF,
    )


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
    if device_id in orientation_references:
        del orientation_references[device_id]

    print(
        "[CALIBRATION RESET] device={} reason={}".format(
            device_id,
            reason,
        )
    )


def reset_all_orientation_references(reason):
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

    age_ms = time.ticks_diff(
        now_ms,
        latest_chest_imu["sampled_at_ms"],
    )

    return age_ms <= CHEST_IMU_STALE_MS


def add_relative_orientation(message, now_ms):
    """Add chest-relative direction and pose-change fields to a limb packet."""
    device_id = message["device_id"]

    if not chest_imu_is_fresh(now_ms):
        message["orientation_valid"] = 0
        message["orientation_error"] = "chest_imu_unavailable"
        return False

    try:
        chest_quaternion = latest_chest_imu["quaternion"]
        limb_quaternion = quaternion_from_message(message)

        # Limb orientation expressed in the current chest coordinate frame.
        relative_quaternion = quaternion_multiply(
            quaternion_conjugate(chest_quaternion),
            limb_quaternion,
        )

        reference = orientation_references.get(device_id)
        calibrated_now = False

        if reference is None:
            reference = relative_quaternion
            orientation_references[device_id] = reference
            calibrated_now = True

        # Change from the automatically captured neutral pose.
        change_quaternion = quaternion_multiply(
            relative_quaternion,
            quaternion_conjugate(reference),
        )

        roll_deg, pitch_deg, yaw_deg = quaternion_to_euler_degrees(
            change_quaternion
        )

        direction_x, direction_y, direction_z = rotate_vector(
            change_quaternion,
            NEUTRAL_LIMB_DIRECTION,
        )

        message["orientation_valid"] = 1
        message["orientation_frame"] = "chest"
        message["neutral_calibrated_now"] = 1 if calibrated_now else 0

        message["relative_qx"] = rounded(relative_quaternion[0])
        message["relative_qy"] = rounded(relative_quaternion[1])
        message["relative_qz"] = rounded(relative_quaternion[2])
        message["relative_qw"] = rounded(relative_quaternion[3])

        message["change_roll_deg"] = rounded(roll_deg)
        message["change_pitch_deg"] = rounded(pitch_deg)
        message["change_yaw_deg"] = rounded(yaw_deg)

        # Unit vector in the chest frame. It indicates direction only and is
        # not a hand/foot position or distance.
        message["direction_x"] = rounded(direction_x)
        message["direction_y"] = rounded(direction_y)
        message["direction_z"] = rounded(direction_z)

        if "orientation_error" in message:
            del message["orientation_error"]

        if calibrated_now:
            print(
                "[CALIBRATION] {} neutral pose captured".format(
                    device_id
                )
            )

        return True

    except Exception as error:
        message["orientation_valid"] = 0
        message["orientation_error"] = str(error)
        print(
            "[ORIENTATION] device={} error={}".format(
                device_id,
                error,
            )
        )
        return False


# ---------------------------------------------------------------------------
# Chest BNO085
# ---------------------------------------------------------------------------
def initialize_chest_bno085():
    i2c = I2C(
        CHEST_I2C_ID,
        sda=Pin(CHEST_BNO085_SDA_PIN),
        scl=Pin(CHEST_BNO085_SCL_PIN),
        freq=CHEST_BNO085_I2C_FREQ,
        timeout=CHEST_BNO085_I2C_TIMEOUT_US,
    )

    addresses = i2c.scan()
    print("[CHEST I2C] found:", [hex(address) for address in addresses])

    if 0x4A not in addresses and 0x4B not in addresses:
        raise RuntimeError("chest BNO085 not found at 0x4A or 0x4B")

    bno = BNO08X(i2c, debug=False)
    bno.enable_feature(
        BNO_REPORT_ACCELEROMETER,
        CHEST_ACCEL_REPORT_HZ,
    )
    bno.enable_feature(
        BNO_REPORT_GYROSCOPE,
        CHEST_GYRO_REPORT_HZ,
    )
    bno.enable_feature(
        BNO_REPORT_GAME_ROTATION_VECTOR,
        CHEST_QUATERNION_REPORT_HZ,
    )
    bno.set_quaternion_euler_vector(
        BNO_REPORT_GAME_ROTATION_VECTOR
    )

    print("[CHEST BNO085] initialized")
    return bno


def read_chest_imu(bno, sampled_at_ms):
    # The supplied driver returns quaternion in x, y, z, w order.
    quaternion = quaternion_normalize(bno.quaternion)
    gx, gy, gz = bno.gyro
    ax, ay, az = bno.acc

    return {
        "quaternion": quaternion,
        "qx": rounded(quaternion[0]),
        "qy": rounded(quaternion[1]),
        "qz": rounded(quaternion[2]),
        "qw": rounded(quaternion[3]),
        "gx": rounded(gx),
        "gy": rounded(gy),
        "gz": rounded(gz),
        "ax": rounded(ax),
        "ay": rounded(ay),
        "az": rounded(az),
        "sampled_at_ms": sampled_at_ms,
    }


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
    return {
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
    credentials = connect_wifi_forever(wlan)
    network_config = wlan.ifconfig()
    print(
        "[WIFI] ready ssid={} ip={} gateway={}".format(
            credentials["ssid"],
            network_config[0],
            network_config[2],
        )
    )
    set_led(True)


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

    if mqtt_client is not None:
        try:
            mqtt_client.disconnect()
        except Exception:
            pass

    mqtt_client = None


def connect_mqtt():
    global mqtt_client
    global last_mqtt_ping_ms

    address = socket.getaddrinfo(
        JETSON_HOST,
        MQTT_PORT,
        0,
        socket.SOCK_STREAM,
    )[0][-1]

    broker_ip = address[0]
    client_id = (
        b"chest_pico-"
        + ubinascii.hexlify(machine.unique_id())
    )
    status_topic = "device/{}/status".format(GATEWAY_ID).encode("utf-8")

    client = MQTTClient(
        client_id,
        broker_ip,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASSWORD,
        keepalive=MQTT_KEEPALIVE,
    )

    client.set_last_will(
        status_topic,
        mqtt_status_payload("offline"),
        retain=True,
        qos=1,
    )
    client.connect(clean_session=True)
    client.publish(
        status_topic,
        mqtt_status_payload("online"),
        retain=True,
        qos=1,
    )

    mqtt_client = client
    last_mqtt_ping_ms = time.ticks_ms()
    print("[MQTT] connected {}:{}".format(broker_ip, MQTT_PORT))


def ensure_mqtt():
    global last_mqtt_attempt_ms

    if mqtt_client is not None:
        return True

    now_ms = time.ticks_ms()

    if last_mqtt_attempt_ms is not None:
        elapsed_ms = time.ticks_diff(now_ms, last_mqtt_attempt_ms)
        if elapsed_ms < MQTT_RETRY_MS:
            return False

    last_mqtt_attempt_ms = now_ms

    try:
        connect_mqtt()
        return True
    except Exception as error:
        print("[MQTT] connection failed: {}".format(error))
        reset_mqtt()
        return False


def maintain_mqtt():
    global last_mqtt_ping_ms

    if not ensure_mqtt():
        return

    now_ms = time.ticks_ms()
    elapsed_ms = time.ticks_diff(now_ms, last_mqtt_ping_ms)

    if elapsed_ms < MQTT_PING_MS:
        return

    try:
        mqtt_client.ping()
        last_mqtt_ping_ms = now_ms
    except Exception as error:
        print("[MQTT] ping failed: {}".format(error))
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
    item = {
        "topic": topic,
        "payload": ujson.dumps(message).encode("utf-8"),
        "qos": qos,
        "retain": retain,
        "sent": False,
    }

    device_buffer = buffers[device_id]
    device_buffer.append(item)

    if len(device_buffer) > BUFFER_LIMIT:
        dropped = device_buffer.pop(0)
        dropped_state = "sent" if dropped["sent"] else "unsent"
        print(
            "[BUFFER DROP] device={} oldest={} state={}".format(
                device_id,
                dropped["topic"],
                dropped_state,
            )
        )


def flush_buffers():
    if not ensure_mqtt():
        return

    sent_count = 0

    for device_id in SENSOR_DEVICES:
        for item in buffers[device_id]:
            if item["sent"]:
                continue

            if sent_count >= MQTT_FLUSH_PER_LOOP:
                return

            try:
                mqtt_client.publish(
                    item["topic"].encode("utf-8"),
                    item["payload"],
                    retain=item["retain"],
                    qos=item["qos"],
                )
            except Exception as error:
                print("[MQTT] publish failed: {}".format(error))
                reset_mqtt()
                return

            item["sent"] = True
            sent_count += 1
            print("[MQTT TX] {} {}".format(device_id, item["topic"]))


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
        return ujson.loads(raw.decode("utf-8"))
    except Exception as error:
        print("[DROP] bad JSON from {}: {}".format(source[0], error))
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
    device_id = message["device_id"]

    print(
        "[RX IMU] device={} seq={} "
        "q=({:.4f},{:.4f},{:.4f},{:.4f}) "
        "gyro=({:.3f},{:.3f},{:.3f}) "
        "acc=({:.3f},{:.3f},{:.3f}) shock={}".format(
            device_id,
            message.get("sequence", -1),
            float(message.get("qx", 0.0)),
            float(message.get("qy", 0.0)),
            float(message.get("qz", 0.0)),
            float(message.get("qw", 0.0)),
            float(message.get("gx", 0.0)),
            float(message.get("gy", 0.0)),
            float(message.get("gz", 0.0)),
            float(message.get("ax", 0.0)),
            float(message.get("ay", 0.0)),
            float(message.get("az", 0.0)),
            int(message.get("shock", 0)),
        )
    )

    if message.get("orientation_valid") == 1:
        print(
            "  chest_relative_q=({:.4f},{:.4f},{:.4f},{:.4f}) "
            "change_deg=(roll={:.2f},pitch={:.2f},yaw={:.2f})".format(
                message["relative_qx"],
                message["relative_qy"],
                message["relative_qz"],
                message["relative_qw"],
                message["change_roll_deg"],
                message["change_pitch_deg"],
                message["change_yaw_deg"],
            )
        )
        print(
            "  direction=({:.4f},{:.4f},{:.4f}) "
            "buffer={}/{}".format(
                message["direction_x"],
                message["direction_y"],
                message["direction_z"],
                len(buffers[device_id]),
                BUFFER_LIMIT,
            )
        )
    else:
        print(
            "  orientation unavailable: {} buffer={}/{}".format(
                message.get("orientation_error", "unknown"),
                len(buffers[device_id]),
                BUFFER_LIMIT,
            )
        )


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
        except OSError:
            break

        if extra is None:
            handler(raw, source)
        else:
            handler(raw, source, extra)


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
    mqtt_state = "connected" if mqtt_client is not None else "disconnected"
    chest_imu_state = "online" if chest_imu_is_fresh(now_ms) else "offline"

    print(
        "[SUMMARY] mqtt={} chest_imu={}".format(
            mqtt_state,
            chest_imu_state,
        )
    )

    for device_id in SENSOR_DEVICES:
        pending = 0
        for item in buffers[device_id]:
            if not item["sent"]:
                pending += 1

        if device_id == GATEWAY_ID:
            state = chest_imu_state
            calibrated = "reference"
        else:
            info = devices.get(device_id)

            if info is None:
                state = "not_seen"
            elif info["online"]:
                state = "online"
            else:
                state = "offline"

            calibrated = (
                "yes" if device_id in orientation_references else "no"
            )

        print(
            "  {}: {} buffer={}/{} pending={} calibrated={}".format(
                device_id,
                state,
                len(buffers[device_id]),
                BUFFER_LIMIT,
                pending,
                calibrated,
            )
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run():
    global mqtt_client

    discovery_udp = None
    data_udp = None
    last_summary_ms = time.ticks_ms()

    print("=== CareCall chest relative-orientation gateway ===")
    print("boot_id={}".format(CHEST_BOOT_ID))
    print("Jetson MQTT={}:{}".format(JETSON_HOST, MQTT_PORT))
    print("Per-sensor buffer limit={}".format(BUFFER_LIMIT))
    print("Chest BNO085 I2C0 SDA=GP4 SCL=GP5")
    print("Calibration button GP16 active-low with internal pull-up")
    print("Put chest and limbs in the neutral pose before first IMU data.")

    try:
        while True:
            now_ms = time.ticks_ms()

            poll_calibration_button(now_ms)

            # Chest IMU remains active even while Jetson is turned off.
            poll_chest_imu(now_ms)

            if not wlan.isconnected():
                set_led(False)
                reset_mqtt()
                close_udp(discovery_udp)
                close_udp(data_udp)
                discovery_udp = None
                data_udp = None
                connect_wifi()

            if discovery_udp is None:
                discovery_udp = open_udp(DISCOVERY_PORT)
                data_udp = open_udp(DATA_PORT)
                print(
                    "[UDP] listening {}, {}".format(
                        DISCOVERY_PORT,
                        DATA_PORT,
                    )
                )

            drain_udp(discovery_udp, handle_discovery, discovery_udp)
            drain_udp(data_udp, handle_data)

            maintain_mqtt()
            flush_buffers()

            now_ms = time.ticks_ms()
            update_offline(now_ms)

            if (
                time.ticks_diff(now_ms, last_summary_ms)
                >= SUMMARY_INTERVAL_MS
            ):
                print_summary(now_ms)
                last_summary_ms = now_ms
                gc.collect()

            time.sleep_ms(20)

    except KeyboardInterrupt:
        print("[CHEST] stopped by user")

    finally:
        if mqtt_client is not None:
            try:
                status_topic = "device/{}/status".format(GATEWAY_ID)
                mqtt_client.publish(
                    status_topic.encode("utf-8"),
                    mqtt_status_payload("offline"),
                    retain=True,
                    qos=1,
                )
            except Exception:
                pass

        reset_mqtt()
        set_led(False)
        close_udp(discovery_udp)
        close_udp(data_udp)


run()
