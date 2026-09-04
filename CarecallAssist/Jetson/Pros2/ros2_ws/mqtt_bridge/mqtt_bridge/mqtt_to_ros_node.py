#!/usr/bin/env python3

import json
import socket
import threading
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
    IMU_DEVICES = {
        'right_arm': '/sensor/imu/right_arm',
        'left_arm': '/sensor/imu/left_arm',
        'right_leg': '/sensor/imu/right_leg',
        'left_leg': '/sensor/imu/left_leg',
    }

    # Jetson이 고정 IP 없이 mDNS 이름으로 찾을 장치 목록
    EXPECTED_DEVICES = {
        'raspberry_pi_5': 'carecall-pi.local',
        'right_arm': 'carecall-right-arm-pico.local',
        'left_arm': 'carecall-left-arm-pico.local',
        'right_leg': 'carecall-right-leg-pico.local',
        'left_leg': 'carecall-left-leg-pico.local',
        'zero2w': 'carecall-zero2w.local',
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

        # ==================================================
        # ROS2 Publisher
        # ==================================================
        self.raw_pub = self.create_publisher(
            String,
            '/mqtt/raw',
            10
        )

        self.button_pub = self.create_publisher(
            UInt8,
            '/sensor/button',
            10
        )

        self.shock_pub = self.create_publisher(
            UInt8,
            '/sensor/shock',
            10
        )

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
            'sensor/+/button'
        )

        client.subscribe(
            'sensor/+/shock'
        )

        client.subscribe(
            'sensor/+/imu'
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

            else:
                self.get_logger().warning(
                    f'Unsupported sensor type: '
                    f'{sensor_type}'
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

        self.button_pub.publish(
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

        self.shock_pub.publish(
            message
        )

        self.get_logger().info(
            f'Shock from {device_name}: '
            f'{message.data}'
        )

    # ==================================================
    # IMU 처리
    # ==================================================
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

        # Quaternion
        message.orientation.x = float(
            data.get('qx', 0.0)
        )

        message.orientation.y = float(
            data.get('qy', 0.0)
        )

        message.orientation.z = float(
            data.get('qz', 0.0)
        )

        message.orientation.w = float(
            data.get('qw', 1.0)
        )

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

        self.get_logger().info(
            f'IMU from {device_name}: '
            f'orientation=('
            f'{message.orientation.x}, '
            f'{message.orientation.y}, '
            f'{message.orientation.z}, '
            f'{message.orientation.w}), '
            f'acceleration=('
            f'{message.linear_acceleration.x}, '
            f'{message.linear_acceleration.y}, '
            f'{message.linear_acceleration.z})'
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
