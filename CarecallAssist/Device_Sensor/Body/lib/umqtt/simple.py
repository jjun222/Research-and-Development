"""Bounded MQTT 3.1.1 client for the CareCall chest Pico.

QoS 0/1, retained messages, Last Will and subscriptions are supported.
One deadline covers each operation, including a complete QoS 1 PUBACK.
DNS/firmware stalls still require the hardware watchdog in main.py.
"""
import socket
import struct
import time
try:
    import select
except ImportError:
    import uselect as select

CARECALL_MQTT_VERSION = "2026-09-14.1"


class MQTTException(Exception):
    pass


def _bytes(value):
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _string(value):
    value = _bytes(value)
    if len(value) > 65535:
        raise ValueError("MQTT string too long")
    return struct.pack("!H", len(value)) + value


def _length(value):
    if value < 0 or value > 268435455:
        raise ValueError("Invalid MQTT remaining length")
    result = bytearray()
    while True:
        digit = value & 127
        value >>= 7
        result.append(digit | (128 if value else 0))
        if not value:
            return result


class MQTTClient:
    def __init__(self, client_id, server, port=0, user=None, password=None,
                 keepalive=0, ssl=None, ssl_params=None):
        self.client_id = _bytes(client_id)
        self.server = server
        self.port = port or (8883 if ssl else 1883)
        self.user = user
        self.pswd = password
        self.keepalive = keepalive
        self.ssl = ssl
        self.ssl_params = ssl_params or {}
        self.sock = None
        self.pid = 0
        self.cb = None
        self.lw_topic = None
        self.lw_msg = None
        self.lw_qos = 0
        self.lw_retain = False
        self.timeout = 2.0
        self.max_packet_size = 8192
        self.last_rx_ms = None
        self.last_pingresp_ms = None
        self.ping_outstanding_ms = None
        self._ack = None

    def set_timeout(self, seconds):
        if seconds is None or not 0 < float(seconds) <= 5:
            raise ValueError("MQTT timeout must be > 0 and <= 5 seconds")
        self.timeout = float(seconds)

    def _deadline(self, seconds=None):
        seconds = self.timeout if seconds is None else float(seconds)
        if not 0 < seconds <= 5:
            raise ValueError("MQTT timeout must be > 0 and <= 5 seconds")
        return time.ticks_add(time.ticks_ms(), max(1, int(seconds * 1000)))

    def _remaining(self, deadline):
        remaining = time.ticks_diff(deadline, time.ticks_ms())
        if remaining <= 0:
            raise OSError(110, "MQTT operation timed out")
        return remaining / 1000.0

    def _read(self, size, deadline):
        result = bytearray()
        while len(result) < size:
            self.sock.settimeout(self._remaining(deadline))
            part = self.sock.read(size - len(result))
            if part == b"":
                raise OSError(104, "MQTT peer closed connection")
            if part is None:
                time.sleep_ms(1)
                continue
            result.extend(part)
        self._remaining(deadline)
        return result

    def _write(self, data, deadline):
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            self.sock.settimeout(self._remaining(deadline))
            count = self.sock.write(view[offset:offset + 512])
            if count is None:
                time.sleep_ms(1)
                continue
            if count <= 0:
                raise OSError(104, "MQTT write failed")
            offset += count
        self._remaining(deadline)

    def _send(self, first, payload, deadline):
        self._write(bytes((first,)) + _length(len(payload)), deadline)
        self._write(payload, deadline)

    def _packet(self, deadline):
        first = self._read(1, deadline)[0]
        length = 0
        multiplier = 1
        for _ in range(4):
            digit = self._read(1, deadline)[0]
            length += (digit & 127) * multiplier
            if not digit & 128:
                break
            multiplier *= 128
        else:
            raise MQTTException("Invalid remaining length")
        if length > self.max_packet_size:
            raise MQTTException("Incoming MQTT packet too large")
        payload = self._read(length, deadline) if length else bytearray()
        self.last_rx_ms = time.ticks_ms()
        return first, payload

    def _next_pid(self):
        self.pid = (self.pid % 65535) + 1
        return self.pid

    def set_callback(self, callback):
        self.cb = callback

    def set_last_will(self, topic, msg, retain=False, qos=0):
        if not topic or qos not in (0, 1):
            raise ValueError("Last Will requires a topic and QoS 0 or 1")
        self.lw_topic = _bytes(topic)
        self.lw_msg = _bytes(msg)
        self.lw_qos = qos
        self.lw_retain = bool(retain)

    def close(self):
        sock, self.sock = self.sock, None
        self.ping_outstanding_ms = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def connect(self, clean_session=True, timeout=None):
        self.close()
        if timeout is not None:
            self.set_timeout(timeout)
        deadline = self._deadline()
        try:
            host = self.server
            if isinstance(host, bytes):
                host = host.decode()
            fields = host.split(".")
            numeric = (len(fields) == 4 and all(
                field.isdigit() and 0 <= int(field) <= 255 for field in fields))
            # Numeric IPv4 tuples are supported by the Pico RP2 port.
            address = ((host, self.port) if numeric else
                       socket.getaddrinfo(host, self.port, 0,
                                          socket.SOCK_STREAM)[0][-1])
            self.sock = socket.socket()
            self.sock.settimeout(self._remaining(deadline))
            self.sock.connect(address)
            if self.ssl:
                self.sock.settimeout(self._remaining(deadline))
                if self.ssl is True:
                    import ssl
                    self.sock = ssl.wrap_socket(self.sock, **self.ssl_params)
                else:
                    self.sock = self.ssl.wrap_socket(
                        self.sock, server_hostname=host)
            if not 0 <= self.keepalive <= 65535:
                raise ValueError("Invalid keepalive")
            flags = 2 if clean_session else 0
            body = _string(self.client_id)
            if self.lw_topic is not None:
                flags |= 4 | (self.lw_qos << 3) | (int(self.lw_retain) << 5)
                body += _string(self.lw_topic) + _string(self.lw_msg)
            if self.user is not None:
                flags |= 128
                body += _string(self.user)
                if self.pswd is not None:
                    flags |= 64
                    body += _string(self.pswd)
            header = b"\x00\x04MQTT\x04" + bytes((flags,))
            header += struct.pack("!H", self.keepalive)
            self._send(0x10, header + body, deadline)
            first, response = self._packet(deadline)
            if first != 0x20 or len(response) != 2 or response[0] & 0xFE:
                raise MQTTException("Invalid CONNACK")
            if response[1]:
                raise MQTTException(response[1])
            if clean_session and response[0]:
                raise MQTTException("Unexpected session-present flag")
            self.last_pingresp_ms = None
            return response[0] & 1
        except BaseException:
            self.close()
            raise

    def disconnect(self):
        try:
            if self.sock is not None:
                self._send(0xE0, b"", self._deadline())
        finally:
            self.close()

    def ping(self):
        if self.ping_outstanding_ms is not None:
            return
        self._send(0xC0, b"", self._deadline())
        self.ping_outstanding_ms = time.ticks_ms()

    def _receive(self, deadline):
        first, payload = self._packet(deadline)
        if first == 0xD0:
            if payload:
                raise MQTTException("Invalid PINGRESP")
            self.last_pingresp_ms = time.ticks_ms()
            self.ping_outstanding_ms = None
        elif first in (0x40, 0x90, 0xB0):
            required = 3 if first == 0x90 else 2
            if len(payload) != required:
                raise MQTTException("Invalid acknowledgement")
            self._ack = (first, (payload[0] << 8) | payload[1], payload[2:])
        elif first & 0xF0 == 0x30:
            qos = (first >> 1) & 3
            if qos not in (0, 1) or len(payload) < 2:
                raise MQTTException("Unsupported PUBLISH")
            size = (payload[0] << 8) | payload[1]
            end = 2 + size
            if not size or len(payload) < end + (2 if qos else 0):
                raise MQTTException("Truncated PUBLISH")
            topic = bytes(payload[2:end])
            if qos:
                pid = bytes(payload[end:end + 2])
                end += 2
            if self.cb is None:
                raise MQTTException("PUBLISH without callback")
            self.cb(topic, bytes(payload[end:]))
            if qos:
                self._send(0x40, pid, deadline)
        else:
            raise MQTTException("Unexpected MQTT packet: {}".format(first))
        return first

    def _wait_ack(self, first, pid, deadline):
        while True:
            self._remaining(deadline)
            self._receive(deadline)
            if self._ack and self._ack[:2] == (first, pid):
                result = self._ack[2]
                self._ack = None
                return result

    def publish(self, topic, msg, retain=False, qos=0):
        if not topic or qos not in (0, 1):
            raise ValueError("PUBLISH requires a topic and QoS 0 or 1")
        deadline = self._deadline()
        body = _string(topic)
        if qos:
            pid = self._next_pid()
            self._ack = None
            body += struct.pack("!H", pid)
        message = _bytes(msg)
        self._write(bytes((0x30 | (qos << 1) | int(bool(retain)),))
                    + _length(len(body) + len(message)), deadline)
        self._write(body, deadline)
        self._write(message, deadline)
        if qos:
            self._wait_ack(0x40, pid, deadline)

    def subscribe(self, topic, qos=0):
        if self.cb is None or qos not in (0, 1):
            raise ValueError("Set a callback and use QoS 0 or 1")
        deadline = self._deadline()
        pid = self._next_pid()
        self._ack = None
        self._send(0x82, struct.pack("!H", pid) + _string(topic)
                   + bytes((qos,)), deadline)
        result = self._wait_ack(0x90, pid, deadline)
        if result[0] not in (0, 1):
            raise MQTTException("Subscription rejected")

    def unsubscribe(self, topic):
        deadline = self._deadline()
        pid = self._next_pid()
        self._ack = None
        self._send(0xA2, struct.pack("!H", pid) + _string(topic), deadline)
        self._wait_ack(0xB0, pid, deadline)

    def wait_msg(self):
        return self._receive(self._deadline())

    def check_msg(self):
        poller = select.poll()
        poller.register(self.sock, select.POLLIN)
        try:
            events = poller.poll(0)
        finally:
            poller.unregister(self.sock)
        if not events:
            return None
        return self.wait_msg()


 
