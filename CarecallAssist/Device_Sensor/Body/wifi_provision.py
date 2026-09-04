"""Jetson-to-chest Wi-Fi provisioning for MicroPython.

The module uses only MicroPython built-ins. It connects to the Jetson setup AP,
downloads the saved uplink credentials through the authenticated device API,
stores them on Pico flash, acknowledges the save, and retries forever when the
Jetson or target router is not ready yet.
"""

import network
import socket
import time
import ujson

try:
    import os
except ImportError:
    import uos as os

from config import (
    DEVICE_PROVISION_TOKEN,
    GATEWAY_ID,
    PROVISION_HTTP_TIMEOUT_SECONDS,
    PROVISION_MAX_RESPONSE_BYTES,
    PROVISION_POLL_MS,
    SETUP_AP_CONNECT_TIMEOUT_MS,
    SETUP_AP_IP,
    SETUP_AP_PASSWORD,
    SETUP_AP_PORT,
    SETUP_AP_RETRY_MS,
    SETUP_AP_SSID,
    WIFI_CONNECT_TIMEOUT_MS,
    WIFI_CREDENTIALS_BACKUP_FILE,
    WIFI_CREDENTIALS_FILE,
)


TOKEN_PLACEHOLDER = "***가림***"


def _remove_if_exists(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _file_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _validate_credentials(credentials):
    if not isinstance(credentials, dict):
        raise ValueError("Wi-Fi credentials must be a JSON object")

    ssid = credentials.get("ssid")
    password = credentials.get("password", "")
    security = credentials.get("security", "wpa-psk")
    hidden = bool(credentials.get("hidden", False))

    if not isinstance(ssid, str):
        raise ValueError("SSID is missing")
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("SSID must be 1 to 32 UTF-8 bytes")
    if "\x00" in ssid or "\n" in ssid or "\r" in ssid:
        raise ValueError("SSID contains an invalid character")

    if security not in ("open", "wpa-psk"):
        raise ValueError("unsupported Wi-Fi security")
    if not isinstance(password, str):
        raise ValueError("Wi-Fi password must be text")

    if security == "open":
        password = ""
    else:
        password_bytes = password.encode("utf-8")
        is_hex_psk = len(password) == 64 and all(
            character in "0123456789abcdefABCDEF"
            for character in password
        )
        if not (8 <= len(password_bytes) <= 63 or is_hex_psk):
            raise ValueError("WPA/WPA2 password must be 8 to 63 bytes")
        if "\x00" in password or "\n" in password or "\r" in password:
            raise ValueError("Wi-Fi password contains an invalid character")

    return {
        "schema_version": 1,
        "ssid": ssid,
        "password": password,
        "security": security,
        "hidden": hidden,
    }


def _read_credentials_file(path):
    with open(path, "r") as handle:
        return _validate_credentials(ujson.loads(handle.read()))


def load_saved_credentials():
    for path in (WIFI_CREDENTIALS_FILE, WIFI_CREDENTIALS_BACKUP_FILE):
        try:
            credentials = _read_credentials_file(path)
            if path == WIFI_CREDENTIALS_BACKUP_FILE:
                print("[WIFI CONFIG] recovered backup credentials")
            else:
                print("[WIFI CONFIG] saved credentials loaded")
            return credentials
        except OSError:
            continue
        except Exception as error:
            print("[WIFI CONFIG] invalid {}: {}".format(path, error))
    return None


def save_credentials(credentials):
    credentials = _validate_credentials(credentials)
    temporary_path = WIFI_CREDENTIALS_FILE + ".tmp"

    _remove_if_exists(temporary_path)
    with open(temporary_path, "w") as handle:
        handle.write(ujson.dumps(credentials))
        try:
            handle.flush()
        except Exception:
            pass

    # Verify the complete temporary file before replacing the active file.
    _read_credentials_file(temporary_path)

    _remove_if_exists(WIFI_CREDENTIALS_BACKUP_FILE)
    moved_previous = False

    if _file_exists(WIFI_CREDENTIALS_FILE):
        os.rename(
            WIFI_CREDENTIALS_FILE,
            WIFI_CREDENTIALS_BACKUP_FILE,
        )
        moved_previous = True

    try:
        os.rename(temporary_path, WIFI_CREDENTIALS_FILE)
    except Exception:
        if moved_previous and not _file_exists(WIFI_CREDENTIALS_FILE):
            os.rename(
                WIFI_CREDENTIALS_BACKUP_FILE,
                WIFI_CREDENTIALS_FILE,
            )
        raise

    _remove_if_exists(WIFI_CREDENTIALS_BACKUP_FILE)
    print("[WIFI CONFIG] credentials saved to Pico flash")
    return credentials


def _disconnect(wlan):
    try:
        wlan.disconnect()
    except Exception:
        pass
    time.sleep_ms(200)


def _connect(wlan, ssid, password, timeout_ms, label):
    wlan.active(True)
    _disconnect(wlan)

    print("[WIFI] connecting {} ssid={}".format(label, ssid))
    if password:
        wlan.connect(ssid, password)
    else:
        wlan.connect(ssid)

    started_ms = time.ticks_ms()

    while not wlan.isconnected():
        status = wlan.status()
        if status < 0:
            print("[WIFI] {} failed status={}".format(label, status))
            return False

        if time.ticks_diff(time.ticks_ms(), started_ms) >= timeout_ms:
            print("[WIFI] {} timeout after {} ms".format(label, timeout_ms))
            return False

        time.sleep_ms(250)

    network_config = wlan.ifconfig()
    print(
        "[WIFI] {} connected ip={} gateway={}".format(
            label,
            network_config[0],
            network_config[2],
        )
    )
    return True


def _send_all(sock, data):
    offset = 0
    while offset < len(data):
        sent = sock.send(data[offset:])
        if sent is None:
            sent = 0
        if sent <= 0:
            raise OSError("HTTP socket closed while sending")
        offset += sent


def _parse_http_response(raw):
    header_end = raw.find(b"\r\n\r\n")
    if header_end < 0:
        raise ValueError("invalid HTTP response")

    header = raw[:header_end].decode("utf-8")
    body = raw[header_end + 4:]
    status_line = header.split("\r\n", 1)[0]
    status_parts = status_line.split(" ", 2)

    if len(status_parts) < 2:
        raise ValueError("invalid HTTP status line")

    return int(status_parts[1]), body


def _http_request(method, path, body=None):
    if body is None:
        body = b""
    elif isinstance(body, str):
        body = body.encode("utf-8")

    address = socket.getaddrinfo(
        SETUP_AP_IP,
        SETUP_AP_PORT,
        0,
        socket.SOCK_STREAM,
    )[0][-1]

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(PROVISION_HTTP_TIMEOUT_SECONDS)
        sock.connect(address)

        headers = [
            "{} {} HTTP/1.1".format(method, path),
            "Host: {}".format(SETUP_AP_IP),
            "X-CareCall-Provision-Token: {}".format(
                DEVICE_PROVISION_TOKEN
            ),
            "Connection: close",
            "Content-Length: {}".format(len(body)),
        ]
        if method == "POST":
            headers.append(
                "Content-Type: application/x-www-form-urlencoded"
            )

        request = ("\r\n".join(headers) + "\r\n\r\n").encode(
            "utf-8"
        ) + body
        _send_all(sock, request)

        chunks = []
        received = 0
        while True:
            chunk = sock.recv(512)
            if not chunk:
                break
            received += len(chunk)
            if received > PROVISION_MAX_RESPONSE_BYTES:
                raise ValueError("HTTP response is too large")
            chunks.append(chunk)

        return _parse_http_response(b"".join(chunks))
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _fetch_credentials():
    path = "/api/device/wifi?device_id={}".format(GATEWAY_ID)
    status_code, body = _http_request("GET", path)

    if status_code == 403:
        raise RuntimeError("Jetson provisioning token rejected")
    if status_code != 200:
        raise RuntimeError("Jetson API returned HTTP {}".format(status_code))

    response = ujson.loads(body.decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError("Jetson API returned an error")
    if not response.get("available"):
        return None
    if response.get("device_id") != GATEWAY_ID:
        raise ValueError("credential device_id mismatch")

    credentials = _validate_credentials(response)
    provision_id = response.get("provision_id")
    if not isinstance(provision_id, str) or not provision_id:
        raise ValueError("provision_id is missing")

    return credentials, provision_id


def _send_saved_ack(provision_id):
    body = (
        "device_id={}&provision_id={}&status=saved".format(
            GATEWAY_ID,
            provision_id,
        )
    )
    status_code, response_body = _http_request("POST", "/api/device/ack", body)

    if status_code != 200:
        return False

    try:
        response = ujson.loads(response_body.decode("utf-8"))
        return bool(response.get("ok"))
    except Exception:
        return False


def _token_is_configured():
    return (
        isinstance(DEVICE_PROVISION_TOKEN, str)
        and len(DEVICE_PROVISION_TOKEN) >= 24
        and DEVICE_PROVISION_TOKEN != TOKEN_PLACEHOLDER
        and "\n" not in DEVICE_PROVISION_TOKEN
        and "\r" not in DEVICE_PROVISION_TOKEN
    )


def connect_wifi_forever(wlan=None):
    """Return only after the Pico is connected to its saved target router."""
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    wlan.active(True)

    while True:
        credentials_received = False
        credentials = load_saved_credentials()

        if credentials is not None:
            if _connect(
                wlan,
                credentials["ssid"],
                credentials["password"],
                WIFI_CONNECT_TIMEOUT_MS,
                "saved uplink",
            ):
                return credentials

        if not _token_is_configured():
            raise RuntimeError(
                "Set DEVICE_PROVISION_TOKEN in config.py before provisioning"
            )

        if _connect(
            wlan,
            SETUP_AP_SSID,
            SETUP_AP_PASSWORD,
            SETUP_AP_CONNECT_TIMEOUT_MS,
            "Jetson setup AP",
        ):
            print("[PROVISION] waiting for Jetson Wi-Fi credentials")

            while wlan.isconnected():
                try:
                    result = _fetch_credentials()
                    if result is None:
                        print("[PROVISION] credentials not available yet")
                    else:
                        new_credentials, provision_id = result
                        save_credentials(new_credentials)
                        credentials_received = True

                        while wlan.isconnected():
                            try:
                                if _send_saved_ack(provision_id):
                                    print("[PROVISION] saved ACK sent to Jetson")
                                    break
                                print("[PROVISION] ACK rejected; retrying")
                            except Exception as error:
                                print("[PROVISION] ACK retry: {}".format(error))
                            time.sleep_ms(PROVISION_POLL_MS)

                        _disconnect(wlan)
                        # The Jetson closes its AP after sending the ACK reply
                        # and then reconnects to the same target router.
                        time.sleep_ms(2000)
                        break

                except Exception as error:
                    print("[PROVISION] fetch retry: {}".format(error))

                time.sleep_ms(PROVISION_POLL_MS)

        if credentials_received:
            continue

        _disconnect(wlan)
        print(
            "[PROVISION] retrying saved Wi-Fi/setup AP in {} ms".format(
                SETUP_AP_RETRY_MS
            )
        )
        time.sleep_ms(SETUP_AP_RETRY_MS)
