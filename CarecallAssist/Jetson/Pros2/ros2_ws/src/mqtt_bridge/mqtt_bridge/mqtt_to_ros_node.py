#!/usr/bin/env python3

import json
import math
import socket
import threading
import time
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, Imu
from std_msgs.msg import String, UInt8

import paho.mqtt.client as mqtt


class MqttToRosBridge(Node):
    SENSOR_DEVICES = (
        'chest',
        'right_arm',
        'left_arm',
        'right_leg',
        'left_leg',
    )

    IMU_DEVICES = {
        'chest': '/sensor/imu/chest',
        'right_arm': '/sensor/imu/right_arm',
        'left_arm': '/sensor/imu/left_arm',
        'right_leg': '/sensor/imu/right_leg',
        'left_leg': '/sensor/imu/left_leg',
    }

    RELATIVE_MOTION_DEVICES = {
        'right_arm': '/sensor/relative_motion/right_arm',
        'left_arm': '/sensor/relative_motion/left_arm',
        'right_leg': '/sensor/relative_motion/right_leg',
        'left_leg': '/sensor/relative_motion/left_leg',
    }

    # 카메라 장치만 Jetson이 mDNS로 직접 확인한다.
    # 가슴/팔다리 Pico 상태는 MQTT 수신 시간으로 판단한다.
    EXPECTED_DEVICES = {
        'raspberry_pi_5': 'carecall-pi.local',
    }

    def __init__(self):
        super().__init__('mqtt_to_ros_bridge')

        # ==================================================
        # ROS2 파라미터
        # ==================================================
        self.declare_parameter(
            'camera_url',
            'http://carecall-pi.local:8080/stream.mjpg'
        )

        self.declare_parameter(
            'camera_topic',
            '/camera/raspberry_pi/image_raw'
        )

        self.declare_parameter(
            'camera_frame_id',
            'raspberry_pi_camera_optical_frame'
        )

        self.declare_parameter(
            'camera_publish_fps',
            10.0
        )

        self.declare_parameter(
            'camera_reconnect_delay_sec',
            2.0
        )

        self.declare_parameter(
            'camera_offline_check_sec',
            5.0
        )

        # 장치 이름 확인 주기
        self.declare_parameter(
            'device_discovery_interval_sec',
            5.0
        )

        # 가슴 gateway의 DEVICE_OFFLINE_MS=15000과 동일한 기준
        self.declare_parameter(
            'sensor_offline_timeout_sec',
            15.0
        )

        self.camera_url = (
            self.get_parameter('camera_url').value
        )

        self.camera_topic = (
            self.get_parameter('camera_topic').value
        )

        self.camera_frame_id = (
            self.get_parameter('camera_frame_id').value
        )

        self.camera_publish_fps = max(
            float(
                self.get_parameter(
                    'camera_publish_fps'
                ).value
            ),
            0.1
        )

        self.camera_reconnect_delay_sec = max(
            float(
                self.get_parameter(
                    'camera_reconnect_delay_sec'
                ).value
            ),
            0.1
        )

        self.camera_offline_check_sec = max(
            float(
                self.get_parameter(
                    'camera_offline_check_sec'
                ).value
            ),
            1.0
        )

        self.device_discovery_interval_sec = max(
            float(
                self.get_parameter(
                    'device_discovery_interval_sec'
                ).value
            ),
            1.0
        )

        self.sensor_offline_timeout_sec = max(
            float(
                self.get_parameter(
                    'sensor_offline_timeout_sec'
                ).value
            ),
            1.0
        )

        # ==================================================
        # ROS2 Publisher
        # ==================================================
        self.raw_pub = self.create_publisher(
            String,
            '/mqtt/raw',
            10
        )

        # MQTT의 장치명이 ROS2 토픽에서도 사라지지 않도록
        # 가슴/팔다리별 Publisher를 각각 만든다.
        self.button_publishers = {
            name: self.create_publisher(
                UInt8,
                f'/sensor/button/{name}',
                10
            )
            for name in self.SENSOR_DEVICES
        }

        self.shock_publishers = {
            name: self.create_publisher(
                UInt8,
                f'/sensor/shock/{name}',
                10
            )
            for name in self.SENSOR_DEVICES
        }

        # 장치 검색 상태 발행
        self.device_status_pub = self.create_publisher(
            String,
            '/device/status',
            10
        )

        # 신체 부위별 IMU Publisher
        self.imu_publishers = {
            name: self.create_publisher(
                Imu,
                topic,
                qos_profile_sensor_data
            )
            for name, topic in self.IMU_DEVICES.items()
        }

        # sensor_msgs/Imu에 들어가지 않는 가슴 기준 방향 벡터와
        # roll/pitch/yaw 변화량을 JSON String으로 보존한다.
        self.relative_motion_publishers = {
            name: self.create_publisher(
                String,
                topic,
                qos_profile_sensor_data
            )
            for name, topic in (
                self.RELATIVE_MOTION_DEVICES.items()
            )
        }

        # Raspberry Pi 5 카메라 Publisher
        self.camera_pub = self.create_publisher(
            Image,
            self.camera_topic,
            qos_profile_sensor_data
        )

        # ==================================================
        # 카메라 공유 상태
        # ==================================================
        self.cv_bridge = CvBridge()

        self._camera_lock = threading.Lock()
        self._latest_camera_frame = None
        self._latest_camera_sequence = 0
        self._published_camera_sequence = 0

        self._camera_stop_event = threading.Event()
        self._camera_response = None

        # ==================================================
        # 장치 검색 공유 상태
        # ==================================================
        self._device_discovery_stop_event = (
            threading.Event()
        )

        self._device_state_lock = threading.Lock()

        self._device_states = {
            name: {
                'online': False,
                'ip': None,
                'initialized': False,
            }
            for name in self.EXPECTED_DEVICES
        }

        # 가슴 및 팔다리 논리 센서 상태는 MQTT 메시지 수신을
        # 기준으로 별도 관리한다.
        self._sensor_state_lock = threading.Lock()

        self._sensor_states = {
            name: {
                'online': False,
                'last_seen_monotonic': None,
                'initialized': False,
            }
            for name in self.SENSOR_DEVICES
        }

        # ==================================================
        # 카메라 수신 스레드
        # ==================================================
        self._camera_thread = threading.Thread(
            target=self._camera_receive_loop,
            name='raspberry-pi-camera-receiver',
            daemon=True
        )

        self._camera_thread.start()

        # ==================================================
        # 장치 자동 검색 스레드
        # ==================================================
        self._device_discovery_thread = (
            threading.Thread(
                target=self._device_discovery_loop,
                name='carecall-device-discovery',
                daemon=True
            )
        )

        self._device_discovery_thread.start()

        # 카메라 프레임 ROS2 발행 타이머
        self.camera_timer = self.create_timer(
            1.0 / self.camera_publish_fps,
            self._publish_latest_camera_frame
        )

        self.sensor_status_timer = self.create_timer(
            1.0,
            self._check_sensor_timeouts
        )

        # ==================================================
        # MQTT Client
        # ==================================================
        self.mqtt_client = mqtt.Client()

        self.mqtt_client.on_connect = (
            self.on_connect
        )

        self.mqtt_client.on_message = (
            self.on_message
        )

        # Mosquitto Broker는 Jetson 내부에서 실행
        self.mqtt_client.connect(
            'localhost',
            1883,
            60
        )

        self.mqtt_client.loop_start()

        # ==================================================
        # 초기 로그
        # ==================================================
        self.get_logger().info(
            'MQTT to ROS2 bridge started.'
        )

        self.get_logger().info(
            f'Raspberry Pi camera URL: '
            f'{self.camera_url}'
        )

        self.get_logger().info(
            f'Camera ROS2 topic: '
            f'{self.camera_topic}'
        )

        for name, topic in self.IMU_DEVICES.items():
            self.get_logger().info(
                f'IMU mapping: '
                f'sensor/{name}/imu -> {topic}'
            )

        for name, topic in (
            self.RELATIVE_MOTION_DEVICES.items()
        ):
            self.get_logger().info(
                f'Relative motion mapping: '
                f'sensor/{name}/imu -> {topic}'
            )

        for name in self.SENSOR_DEVICES:
            self.get_logger().info(
                f'Button mapping: '
                f'sensor/{name}/button -> '
                f'/sensor/button/{name}'
            )

            self.get_logger().info(
                f'Shock mapping: '
                f'sensor/{name}/shock -> '
                f'/sensor/shock/{name}'
            )

        for name, hostname in (
            self.EXPECTED_DEVICES.items()
        ):
            self.get_logger().info(
                f'Device target: '
                f'{name} -> {hostname}'
            )

    # ==================================================
    # MQTT 연결 처리
    # ==================================================
    def on_connect(
        self,
        client,
        userdata,
        flags,
        rc
    ):
        if rc != 0:
            self.get_logger().error(
                f'MQTT connection failed. '
                f'result code={rc}'
            )
            return

        client.subscribe(
            'sensor/+/button',
            qos=1
        )

        client.subscribe(
            'sensor/+/shock',
            qos=1
        )

        client.subscribe(
            'sensor/+/imu',
            qos=1
        )

        self.get_logger().info(
            'MQTT connected. result code=0'
        )

        self.get_logger().info(
            'Subscribed to sensor/+/button, '
            'sensor/+/shock, sensor/+/imu'
        )

    # ==================================================
    # MQTT 메시지 처리
    # ==================================================
    def on_message(
        self,
        client,
        userdata,
        mqtt_message
    ):
        topic = mqtt_message.topic

        try:
            payload_text = (
                mqtt_message.payload.decode(
                    'utf-8'
                )
            )

        except UnicodeDecodeError:
            self.get_logger().warning(
                f'Invalid UTF-8 payload '
                f'on {topic}'
            )
            return

        # MQTT 원본 데이터를 ROS2로 발행
        raw_message = String()

        raw_message.data = (
            f'{topic} {payload_text}'
        )

        self.raw_pub.publish(
            raw_message
        )

        try:
            data = json.loads(
                payload_text
            )

        except json.JSONDecodeError:
            self.get_logger().warning(
                f'Invalid JSON payload on '
                f'{topic}: {payload_text}'
            )
            return

        topic_parts = topic.split('/')

        if (
            len(topic_parts) != 3
            or topic_parts[0] != 'sensor'
        ):
            self.get_logger().warning(
                f'Unsupported MQTT topic '
                f'format: {topic}'
            )
            return

        device_name = topic_parts[1]
        sensor_type = topic_parts[2]

        if device_name not in self.SENSOR_DEVICES:
            supported_devices = ', '.join(
                self.SENSOR_DEVICES
            )

            self.get_logger().warning(
                f'Unknown sensor device '
                f'"{device_name}" on {topic}. '
                f'Supported devices: '
                f'{supported_devices}'
            )
            return

        if sensor_type not in (
            'button',
            'shock',
            'imu'
        ):
            self.get_logger().warning(
                f'Unsupported sensor type: '
                f'{sensor_type}'
            )
            return

        if isinstance(data, dict):
            payload_device = data.get(
                'device_id',
                data.get('device')
            )

            if (
                payload_device is not None
                and str(payload_device)
                != device_name
            ):
                self.get_logger().warning(
                    f'Device name mismatch on '
                    f'{topic}: payload='
                    f'{payload_device!r}'
                )
                return

        self._mark_sensor_seen(
            device_name,
            topic
        )

        try:
            if sensor_type == 'button':
                self.publish_button(
                    data,
                    device_name
                )

            elif sensor_type == 'shock':
                self.publish_shock(
                    data,
                    device_name
                )

            elif sensor_type == 'imu':
                self.publish_imu(
                    data,
                    device_name
                )

        except (
            TypeError,
            ValueError
        ) as error:
            self.get_logger().warning(
                f'Invalid sensor value on '
                f'{topic}: {error}'
            )

        except Exception as error:
            self.get_logger().error(
                f'Unexpected MQTT message '
                f'error on {topic}: {error}'
            )

    # ==================================================
    # 0 또는 1 값 변환
    # ==================================================
    @staticmethod
    def parse_binary_value(
        data,
        key
    ):
        if isinstance(data, dict):
            if key not in data:
                raise ValueError(
                    f'Missing "{key}" field'
                )

            value = data.get(key)

        else:
            value = data

        if isinstance(value, bool):
            return int(value)

        if isinstance(
            value,
            (int, float)
        ):
            numeric_value = int(value)

            if numeric_value in (0, 1):
                return numeric_value

        if isinstance(value, str):
            normalized_value = (
                value.strip().lower()
            )

            if normalized_value in (
                '1',
                'true',
                'on',
                'high',
                'pressed'
            ):
                return 1

            if normalized_value in (
                '0',
                'false',
                'off',
                'low',
                'released'
            ):
                return 0

        raise ValueError(
            f'Unsupported binary value: '
            f'{value!r}'
        )

    # ==================================================
    # 버튼 처리
    # ==================================================
    def publish_button(
        self,
        data,
        device_name
    ):
        message = UInt8()

        message.data = (
            self.parse_binary_value(
                data,
                'pressed'
            )
        )

        publisher = self.button_publishers[
            device_name
        ]

        publisher.publish(
            message
        )

        self.get_logger().info(
            f'Button from {device_name}: '
            f'{message.data}'
        )

    # ==================================================
    # 충격 센서 처리
    # ==================================================
    def publish_shock(
        self,
        data,
        device_name
    ):
        message = UInt8()

        message.data = (
            self.parse_binary_value(
                data,
                'shock'
            )
        )

        publisher = self.shock_publishers[
            device_name
        ]

        publisher.publish(
            message
        )

        self.get_logger().info(
            f'Shock from {device_name}: '
            f'{message.data}'
        )

    # ==================================================
    # IMU 처리
    # ==================================================
    @staticmethod
    def _parse_boolean(
        value,
        default=True
    ):
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        if isinstance(value, str):
            normalized_value = value.strip().lower()

            if normalized_value in (
                '1',
                'true',
                'yes',
                'on',
                'valid'
            ):
                return True

            if normalized_value in (
                '0',
                'false',
                'no',
                'off',
                'invalid'
            ):
                return False

        raise ValueError(
            f'Unsupported boolean value: '
            f'{value!r}'
        )

    @staticmethod
    def _normalized_quaternion(
        x,
        y,
        z,
        w
    ):
        quaternion = (
            float(x),
            float(y),
            float(z),
            float(w),
        )

        norm = math.sqrt(
            sum(
                component * component
                for component in quaternion
            )
        )

        if norm < 1.0e-9:
            raise ValueError(
                'Quaternion length is zero'
            )

        return tuple(
            component / norm
            for component in quaternion
        )

    def _publish_relative_motion(
        self,
        data,
        device_name,
        orientation_valid,
        relative_quaternion
    ):
        publisher = (
            self.relative_motion_publishers.get(
                device_name
            )
        )

        if publisher is None:
            return

        message_data = {
            'schema_version': data.get(
                'schema_version',
                1
            ),
            'device_id': device_name,
            'orientation_frame': data.get(
                'orientation_frame',
                'chest'
            ),
            'orientation_valid': (
                orientation_valid
            ),
        }

        if relative_quaternion is not None:
            (
                message_data['relative_qx'],
                message_data['relative_qy'],
                message_data['relative_qz'],
                message_data['relative_qw'],
            ) = relative_quaternion

        numeric_fields = (
            'direction_x',
            'direction_y',
            'direction_z',
            'change_roll_deg',
            'change_pitch_deg',
            'change_yaw_deg',
        )

        for field_name in numeric_fields:
            if field_name in data:
                message_data[field_name] = float(
                    data[field_name]
                )

        if 'neutral_calibrated_now' in data:
            message_data[
                'neutral_calibrated_now'
            ] = self._parse_boolean(
                data['neutral_calibrated_now'],
                default=False
            )

        message = String()
        message.data = json.dumps(
            message_data,
            ensure_ascii=False,
            separators=(',', ':')
        )

        publisher.publish(message)

    def publish_imu(
        self,
        data,
        device_name
    ):
        if not isinstance(data, dict):
            raise ValueError(
                'IMU payload must be '
                'a JSON object'
            )

        publisher = (
            self.imu_publishers.get(
                device_name
            )
        )

        if publisher is None:
            supported_devices = ', '.join(
                self.IMU_DEVICES
            )

            self.get_logger().warning(
                f'Unknown IMU device '
                f'"{device_name}". '
                f'Supported devices: '
                f'{supported_devices}'
            )
            return

        message = Imu()

        message.header.stamp = (
            self.get_clock()
            .now()
            .to_msg()
        )

        message.header.frame_id = (
            data.get(
                'frame_id',
                f'{device_name}_imu_link'
            )
        )

        orientation_valid = self._parse_boolean(
            data.get('orientation_valid'),
            default=True
        )

        relative_keys = (
            'relative_qx',
            'relative_qy',
            'relative_qz',
            'relative_qw',
        )

        has_relative_quaternion = (
            device_name != 'chest'
            and all(
                key in data
                for key in relative_keys
            )
        )

        relative_quaternion = None

        if has_relative_quaternion:
            try:
                relative_quaternion = (
                    self._normalized_quaternion(
                        data['relative_qx'],
                        data['relative_qy'],
                        data['relative_qz'],
                        data['relative_qw']
                    )
                )

            except ValueError:
                if orientation_valid:
                    raise

        if relative_quaternion is not None:
            orientation = relative_quaternion
            orientation_source = 'chest_relative'

        else:
            try:
                orientation = (
                    self._normalized_quaternion(
                        data.get('qx', 0.0),
                        data.get('qy', 0.0),
                        data.get('qz', 0.0),
                        data.get('qw', 1.0)
                    )
                )

            except ValueError:
                if orientation_valid:
                    raise

                orientation = (
                    0.0,
                    0.0,
                    0.0,
                    1.0
                )

            orientation_source = 'sensor_raw'

        (
            message.orientation.x,
            message.orientation.y,
            message.orientation.z,
            message.orientation.w,
        ) = orientation

        if not orientation_valid:
            # sensor_msgs/Imu 규칙: orientation을 사용할 수 없으면
            # 첫 covariance 값을 -1로 표시한다.
            message.orientation_covariance[0] = -1.0

        # Angular velocity
        message.angular_velocity.x = float(
            data.get('gx', 0.0)
        )

        message.angular_velocity.y = float(
            data.get('gy', 0.0)
        )

        message.angular_velocity.z = float(
            data.get('gz', 0.0)
        )

        # Linear acceleration
        message.linear_acceleration.x = float(
            data.get('ax', 0.0)
        )

        message.linear_acceleration.y = float(
            data.get('ay', 0.0)
        )

        message.linear_acceleration.z = float(
            data.get('az', 0.0)
        )

        publisher.publish(
            message
        )

        self._publish_relative_motion(
            data,
            device_name,
            orientation_valid,
            relative_quaternion
        )

        direction_text = ''

        if all(
            key in data
            for key in (
                'direction_x',
                'direction_y',
                'direction_z'
            )
        ):
            direction_text = (
                f', direction=('
                f'{float(data["direction_x"]):.4f}, '
                f'{float(data["direction_y"]):.4f}, '
                f'{float(data["direction_z"]):.4f})'
            )

        change_text = ''

        if all(
            key in data
            for key in (
                'change_roll_deg',
                'change_pitch_deg',
                'change_yaw_deg'
            )
        ):
            change_text = (
                f', change_deg=('
                f'{float(data["change_roll_deg"]):.2f}, '
                f'{float(data["change_pitch_deg"]):.2f}, '
                f'{float(data["change_yaw_deg"]):.2f})'
            )

        self.get_logger().info(
            f'IMU from {device_name}: '
            f'orientation_source='
            f'{orientation_source}, '
            f'orientation_valid='
            f'{orientation_valid}, '
            f'orientation=('
            f'{message.orientation.x}, '
            f'{message.orientation.y}, '
            f'{message.orientation.z}, '
            f'{message.orientation.w}), '
            f'acceleration=('
            f'{message.linear_acceleration.x}, '
            f'{message.linear_acceleration.y}, '
            f'{message.linear_acceleration.z})'
            f'{direction_text}'
            f'{change_text}'
        )

    # ==================================================
    # MQTT 수신 기반 가슴/팔다리 온라인 상태 처리
    # ==================================================
    def _publish_sensor_status(
        self,
        device_name,
        online,
        source_topic=None,
        silence_sec=None
    ):
        status_data = {
            'device': device_name,
            'online': 1 if online else 0,
            'source': 'mqtt',
        }

        if source_topic is not None:
            status_data['source_topic'] = (
                source_topic
            )

        if silence_sec is not None:
            status_data['silence_sec'] = round(
                silence_sec,
                3
            )

        message = String()
        message.data = json.dumps(
            status_data,
            ensure_ascii=False,
            separators=(',', ':')
        )

        self.device_status_pub.publish(message)

    def _mark_sensor_seen(
        self,
        device_name,
        source_topic
    ):
        now = time.monotonic()

        with self._sensor_state_lock:
            state = self._sensor_states[
                device_name
            ]

            became_online = not state['online']

            state['online'] = True
            state['last_seen_monotonic'] = now
            state['initialized'] = True

        if not became_online:
            return

        self.get_logger().info(
            f'[SENSOR ONLINE] '
            f'{device_name}: {source_topic}'
        )

        self._publish_sensor_status(
            device_name,
            True,
            source_topic=source_topic
        )

    def _check_sensor_timeouts(
        self
    ):
        now = time.monotonic()
        offline_devices = []

        with self._sensor_state_lock:
            for (
                device_name,
                state
            ) in self._sensor_states.items():
                last_seen = state[
                    'last_seen_monotonic'
                ]

                if (
                    not state['initialized']
                    or not state['online']
                    or last_seen is None
                ):
                    continue

                silence_sec = now - last_seen

                if (
                    silence_sec
                    <= self.sensor_offline_timeout_sec
                ):
                    continue

                state['online'] = False

                offline_devices.append(
                    (device_name, silence_sec)
                )

        for device_name, silence_sec in (
            offline_devices
        ):
            self.get_logger().warning(
                f'[SENSOR OFFLINE] '
                f'{device_name}: no MQTT data '
                f'for {silence_sec:.1f} seconds'
            )

            self._publish_sensor_status(
                device_name,
                False,
                silence_sec=silence_sec
            )

    # ==================================================
    # mDNS 이름을 IPv4 주소로 변환
    # ==================================================
    @staticmethod
    def _resolve_ipv4_address(
        hostname
    ):
        try:
            results = socket.getaddrinfo(
                hostname,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM
            )

        except (
            socket.gaierror,
            OSError
        ):
            return None

        for result in results:
            ip_address = result[4][0]

            if ip_address:
                return ip_address

        return None

    # ==================================================
    # 장치 상태를 ROS2로 발행
    # ==================================================
    def _publish_device_status(
        self,
        device_name,
        hostname,
        online,
        ip_address
    ):
        message = String()

        message.data = json.dumps(
            {
                'device': device_name,
                'hostname': hostname,
                'ip': ip_address,
                'online': (
                    1 if online else 0
                ),
            },
            ensure_ascii=False
        )

        self.device_status_pub.publish(
            message
        )

    # ==================================================
    # 장치 상태 변경 처리
    # ==================================================
    def _update_device_state(
        self,
        device_name,
        hostname,
        online,
        ip_address
    ):
        with self._device_state_lock:
            state = self._device_states[
                device_name
            ]

            initialized = (
                state['initialized']
            )

            state_changed = (
                not initialized
                or state['online'] != online
                or (
                    online
                    and state['ip']
                    != ip_address
                )
            )

            state['online'] = online
            state['ip'] = ip_address
            state['initialized'] = True

        if not state_changed:
            return

        # 처음부터 꺼져 있는 장치는
        # 반복 로그를 만들지 않는다.
        if (
            not initialized
            and not online
        ):
            return

        if online:
            self.get_logger().info(
                f'[DEVICE ONLINE] '
                f'{device_name}: '
                f'{hostname} -> '
                f'{ip_address}'
            )

        else:
            self.get_logger().warning(
                f'[DEVICE OFFLINE] '
                f'{device_name}: '
                f'{hostname}'
            )

        self._publish_device_status(
            device_name,
            hostname,
            online,
            (
                ip_address
                if online
                else None
            )
        )

    # ==================================================
    # 장치 자동 검색 스레드
    # ==================================================
    def _device_discovery_loop(
        self
    ):
        while not (
            self._device_discovery_stop_event
            .is_set()
        ):
            for (
                device_name,
                hostname
            ) in self.EXPECTED_DEVICES.items():

                if (
                    self._device_discovery_stop_event
                    .is_set()
                ):
                    break

                ip_address = (
                    self._resolve_ipv4_address(
                        hostname
                    )
                )

                online = (
                    ip_address is not None
                )

                self._update_device_state(
                    device_name,
                    hostname,
                    online,
                    ip_address
                )

            self._device_discovery_stop_event.wait(
                self.device_discovery_interval_sec
            )

    # ==================================================
    # 카메라 URL의 호스트와 포트 추출
    # ==================================================
    def _get_camera_endpoint(
        self
    ):
        try:
            parsed_url = urlparse(
                self.camera_url
            )

            hostname = (
                parsed_url.hostname
            )

            if hostname is None:
                return None, None

            if parsed_url.port is not None:
                port = parsed_url.port

            elif parsed_url.scheme == 'https':
                port = 443

            else:
                port = 80

            return hostname, port

        except ValueError:
            return None, None

    # ==================================================
    # Raspberry Pi 5 카메라 서버 확인
    # ==================================================
    def _is_camera_endpoint_reachable(
        self
    ):
        hostname, port = (
            self._get_camera_endpoint()
        )

        if (
            hostname is None
            or port is None
        ):
            return False

        try:
            with socket.create_connection(
                (hostname, port),
                timeout=1.5
            ):
                return True

        except (
            socket.gaierror,
            socket.timeout,
            TimeoutError,
            OSError
        ):
            return False

    # ==================================================
    # 이전 카메라 프레임 제거
    # ==================================================
    def _clear_latest_camera_frame(
        self
    ):
        with self._camera_lock:
            self._latest_camera_frame = None

            self._published_camera_sequence = (
                self._latest_camera_sequence
            )

    # ==================================================
    # HTTP 카메라 연결 종료
    # ==================================================
    def _close_camera_response(
        self
    ):
        if self._camera_response is None:
            return

        try:
            self._camera_response.close()

        except Exception:
            pass

        finally:
            self._camera_response = None

    # ==================================================
    # Raspberry Pi 5 MJPEG 수신
    # ==================================================
    def _camera_receive_loop(
        self
    ):
        while not (
            self._camera_stop_event.is_set()
        ):
            # Pi 5 또는 카메라 서버가 없으면
            # 오류 로그 없이 5초마다 확인
            if not (
                self._is_camera_endpoint_reachable()
            ):
                self._clear_latest_camera_frame()

                self._camera_stop_event.wait(
                    self.camera_offline_check_sec
                )

                continue

            stream_connected = False

            try:
                self.get_logger().info(
                    f'Connecting to camera '
                    f'stream: {self.camera_url}'
                )

                response = requests.get(
                    self.camera_url,
                    stream=True,
                    timeout=(5.0, 10.0)
                )

                response.raise_for_status()

                self._camera_response = (
                    response
                )

                stream_connected = True

                self.get_logger().info(
                    'Raspberry Pi camera '
                    'stream connected.'
                )

                receive_buffer = bytearray()

                for chunk in (
                    response.iter_content(
                        chunk_size=4096
                    )
                ):
                    if (
                        self._camera_stop_event
                        .is_set()
                    ):
                        break

                    if not chunk:
                        continue

                    receive_buffer.extend(
                        chunk
                    )

                    while True:
                        jpeg_start = (
                            receive_buffer.find(
                                b'\xff\xd8'
                            )
                        )

                        if jpeg_start < 0:
                            if (
                                len(receive_buffer)
                                > 4_000_000
                            ):
                                receive_buffer.clear()

                            break

                        jpeg_end = (
                            receive_buffer.find(
                                b'\xff\xd9',
                                jpeg_start + 2
                            )
                        )

                        if jpeg_end < 0:
                            if jpeg_start > 0:
                                del receive_buffer[
                                    :jpeg_start
                                ]

                            break

                        jpeg_bytes = bytes(
                            receive_buffer[
                                jpeg_start:
                                jpeg_end + 2
                            ]
                        )

                        del receive_buffer[
                            :jpeg_end + 2
                        ]

                        encoded_image = (
                            np.frombuffer(
                                jpeg_bytes,
                                dtype=np.uint8
                            )
                        )

                        frame = cv2.imdecode(
                            encoded_image,
                            cv2.IMREAD_COLOR
                        )

                        if frame is None:
                            self.get_logger().warning(
                                'Failed to decode '
                                'an MJPEG frame.'
                            )
                            continue

                        with self._camera_lock:
                            self._latest_camera_frame = (
                                frame
                            )

                            self._latest_camera_sequence += 1

            except (
                requests.RequestException
            ) as error:
                if (
                    not self._camera_stop_event
                    .is_set()
                    and self
                    ._is_camera_endpoint_reachable()
                ):
                    self.get_logger().warning(
                        f'Camera HTTP connection '
                        f'error: {error}. '
                        f'Retrying in '
                        f'{self.camera_reconnect_delay_sec:.1f} '
                        f'seconds.'
                    )

                else:
                    self._clear_latest_camera_frame()

            except Exception as error:
                if (
                    not self._camera_stop_event
                    .is_set()
                    and self
                    ._is_camera_endpoint_reachable()
                ):
                    self.get_logger().error(
                        f'Unexpected camera '
                        f'receiver error: {error}. '
                        f'Retrying in '
                        f'{self.camera_reconnect_delay_sec:.1f} '
                        f'seconds.'
                    )

                else:
                    self._clear_latest_camera_frame()

            finally:
                self._close_camera_response()

            if (
                self._camera_stop_event.is_set()
            ):
                break

            if (
                self._is_camera_endpoint_reachable()
            ):
                if stream_connected:
                    self.get_logger().warning(
                        f'Camera stream ended. '
                        f'Retrying in '
                        f'{self.camera_reconnect_delay_sec:.1f} '
                        f'seconds.'
                    )

                self._camera_stop_event.wait(
                    self.camera_reconnect_delay_sec
                )

            else:
                self._clear_latest_camera_frame()

                self._camera_stop_event.wait(
                    self.camera_offline_check_sec
                )

    # ==================================================
    # 최신 카메라 프레임 ROS2 발행
    # ==================================================
    def _publish_latest_camera_frame(
        self
    ):
        with self._camera_lock:
            if (
                self._latest_camera_frame
                is None
            ):
                return

            if (
                self._latest_camera_sequence
                == self._published_camera_sequence
            ):
                return

            frame = (
                self._latest_camera_frame.copy()
            )

            frame_sequence = (
                self._latest_camera_sequence
            )

        try:
            image_message = (
                self.cv_bridge.cv2_to_imgmsg(
                    frame,
                    encoding='bgr8'
                )
            )

            image_message.header.stamp = (
                self.get_clock()
                .now()
                .to_msg()
            )

            image_message.header.frame_id = (
                self.camera_frame_id
            )

            self.camera_pub.publish(
                image_message
            )

            self._published_camera_sequence = (
                frame_sequence
            )

        except Exception as error:
            self.get_logger().warning(
                f'Failed to publish camera '
                f'frame: {error}'
            )

    # ==================================================
    # 종료 처리
    # ==================================================
    def stop(
        self
    ):
        self._camera_stop_event.set()

        self._device_discovery_stop_event.set()

        self._close_camera_response()

        if self._camera_thread.is_alive():
            self._camera_thread.join(
                timeout=2.0
            )

        if (
            self._device_discovery_thread
            .is_alive()
        ):
            self._device_discovery_thread.join(
                timeout=2.0
            )

        self.mqtt_client.loop_stop()

        self.mqtt_client.disconnect()


def main(args=None):
    rclpy.init(args=args)

    node = MqttToRosBridge()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.stop()
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
