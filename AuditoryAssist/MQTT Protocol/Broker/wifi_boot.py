#!/usr/bin/env python3
# wifi_boot.py  - Raspberry Pi MQTT 브로커용 Wi-Fi/AP 설정 포털
#
# 동작 요약
# 1) /home/mqtt/MQTTpr/wifi_config.json 에서 ssid/password 읽어서
#    nmcli 로 Wi-Fi 연결 시도
# 2) config가 없거나, 연결에 실패하면
#    - AP(핫스팟) 모드로 전환 (SSID=MQTTBroker_Setup)
#    - 포트 80 웹 서버로 SSID/PW 설정 폼 제공
#    - 저장 후 3초 뒤 재부팅
#
# 개선(수정) 포인트
# - SSID가 부팅 직후 늦게 잡히는 환경 대비:
#   1) SSID가 스캔에 나타날 때까지 일정 시간 대기(재스캔)
#   2) 연결 시도 재시도(횟수 지정)
# - NetworkManager RUNNING 체크를 정확 비교로 수정(오탐 방지)
# - 매번 connection 프로파일 삭제하지 않음(자동 재연결/재시도 기회 유지)
#
# 총 대기시간 대략:
#   NM_READY_TIMEOUT + SSID_WAIT_TIMEOUT + (CONNECT_RETRIES * CONNECT_WAIT_EACH)
#   (nmcli 실행 오버헤드는 약간 추가)

import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

CONFIG_PATH = "/home/mqtt/MQTTpr/wifi_config.json"

# ---------------- 튜닝 값(여기만 조절하면 됨) ----------------
NM_READY_TIMEOUT   = 45   # NetworkManager 준비 최대 대기(초)
SSID_WAIT_TIMEOUT  = 120  # SSID 스캔 대기(초)
CONNECT_RETRIES    = 3    # 연결 시도 횟수
CONNECT_WAIT_EACH  = 25   # 각 시도 후 connected 확인 시간(초)
SCAN_INTERVAL      = 5    # 스캔/체크 간격(초)

# AP 모드용
AP_SSID    = "MQTTBroker_Setup"   # 폰에서 보이는 AP 이름
AP_PW      = "12345678"
WIFI_IFACE = "wlan0"              # 라즈베리파이 Wi-Fi 인터페이스 이름
HTTP_PORT  = 80                   # AP에 붙은 폰 브라우저에서 http://10.42.0.1

FORM_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>MQTT Broker Wi-Fi 설정</title>
</head>
<body>
<h2>MQTT Broker Wi-Fi / MQTT 설정</h2>
<form method="POST" action="/save">
  <p>
    SSID: <input name="ssid" />
  </p>
  <p>
    PW: <input name="pw" type="password" />
  </p>
  <p>
    <button type="submit">저장</button>
  </p>
</form>
<p>저장 후 브로커가 재부팅되며, 설정한 SSID/PW로 Wi-Fi 연결을 시도합니다.</p>
</body>
</html>
"""

SAVED_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>저장 완료</title></head>
<body>
<p>설정이 저장되었습니다. 3초 후 브로커가 재부팅됩니다.</p>
</body>
</html>
"""


# ---------------- 공용 유틸 ----------------

def run(cmd):
    """subprocess.run 래퍼 (로그 출력용)"""
    print("+", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)


def wait_nmcli_ready(timeout_sec=NM_READY_TIMEOUT):
    """
    NetworkManager 가 올라올 때까지 기다림.
    부팅 초기에 너무 일찍 nmcli를 호출하면 실패할 수 있어서 추가.

    NOTE: "running" 문자열 포함 여부로 판단하면
          "not running" 같은 출력에서 오탐 가능 → 정확 비교로 수정.
    """
    start = time.time()
    while time.time() - start < timeout_sec:
        res = run(["nmcli", "-t", "-f", "RUNNING", "general"])
        out = (res.stdout or "").strip().lower()
        if out == "running":
            print("✅ NetworkManager RUNNING")
            return True
        print("⏳ NetworkManager 대기 중...")
        time.sleep(2)
    print("⚠️ NetworkManager 가 준비되지 않았지만 계속 진행합니다.")
    return False


def is_wifi_connected():
    """
    wlan0 이 'connected' 상태인지 검사.
    """
    res = run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device"])
    out = res.stdout or ""
    print("nmcli device 상태:\n", out)
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[0] == WIFI_IFACE:
            state = parts[1]
            if state.startswith("connected"):
                print("✅ wlan0 상태: connected")
                return True
    print("❌ wlan0 상태: connected 아님")
    return False


def wait_for_ssid(target_ssid, timeout_sec=SSID_WAIT_TIMEOUT):
    """
    SSID가 스캔 결과에 나타날 때까지 기다림(재스캔 포함).
    SSID가 늦게 잡히는 환경에서 connect를 너무 빨리 때려 실패하는 문제를 줄임.
    """
    start = time.time()
    while time.time() - start < timeout_sec:
        # 재스캔
        run(["nmcli", "dev", "wifi", "rescan", "ifname", WIFI_IFACE])

        # SSID 목록 조회
        res = run(["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "ifname", WIFI_IFACE])
        ssids = {line.strip() for line in (res.stdout or "").splitlines() if line.strip()}

        if target_ssid in ssids:
            print(f"✅ SSID 발견: {target_ssid}")
            return True

        elapsed = int(time.time() - start)
        print(f"⏳ SSID 스캔 대기 중... {elapsed}/{timeout_sec}초")
        time.sleep(SCAN_INTERVAL)

    print(f"❌ SSID 미발견(타임아웃): {target_ssid}")
    return False


# ---------------- config 관련 ----------------

def load_config():
    """wifi_config.json 읽기 (없으면 None)"""
    if not os.path.exists(CONFIG_PATH):
        print("⚠️ wifi_config.json 없음")
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        ssid = (cfg.get("ssid") or "").strip()
        pw   = (cfg.get("password") or "").strip()
        if not ssid or not pw:
            print("⚠️ config에 ssid/password 값이 비어 있음")
            return None
        print(f"📄 config 읽음: ssid={ssid}")
        return {"ssid": ssid, "password": pw}
    except Exception as e:
        print("❌ config 읽기 오류:", e)
        return None


# ---------------- Wi-Fi 연결 시도 ----------------

def try_connect_wifi_from_config():
    """
    wifi_config.json 기반으로 nmcli Wi-Fi 연결 시도.
    - 성공: True
    - 실패: False  -> AP 모드로 전환
    """
    cfg = load_config()
    if not cfg:
        return False

    ssid = cfg["ssid"]
    pw   = cfg["password"]

    # NetworkManager 준비 대기
    wait_nmcli_ready()

    # Wi-Fi 라디오 켜기
    run(["nmcli", "radio", "wifi", "on"])

    # 1) SSID가 늦게 잡히는 경우 대비: SSID 스캔 대기
    print(f"🔎 SSID 스캔 대기 시작: {ssid} (최대 {SSID_WAIT_TIMEOUT}초)")
    if not wait_for_ssid(ssid, timeout_sec=SSID_WAIT_TIMEOUT):
        print("❌ SSID가 안 보여서 연결 시도 불가 → AP 모드로 전환 예정")
        return False

    # 2) 연결 재시도
    for attempt in range(1, CONNECT_RETRIES + 1):
        print(f"📡 Wi-Fi 연결 시도 {attempt}/{CONNECT_RETRIES}: SSID={ssid}")

        # NOTE: 예전 코드의 'nmcli connection delete ssid' 는 제거
        #       (자동 재연결/프로파일 유지에 유리)
        res = run([
            "nmcli", "dev", "wifi", "connect", ssid,
            "password", pw,
            "ifname", WIFI_IFACE
        ])
        print(res.stdout)

        # 연결 상태 확인 루프
        waited = 0
        while waited < CONNECT_WAIT_EACH:
            if is_wifi_connected():
                print("✅ Wi-Fi 연결 성공 (config 기반)")
                return True
            time.sleep(SCAN_INTERVAL)
            waited += SCAN_INTERVAL
            print(f"⏳ Wi-Fi 연결 확인 대기 중... {waited}/{CONNECT_WAIT_EACH}초")

        # 다음 재시도 전에 재스캔 한 번
        run(["nmcli", "dev", "wifi", "rescan", "ifname", WIFI_IFACE])

    print("❌ Wi-Fi 연결 실패 (재시도 소진) → AP 모드로 전환 예정")
    return False


# ---------------- AP / 포털 ----------------

def start_hotspot():
    """AP / 핫스팟 모드 시작"""
    print("⚠️ Wi-Fi 연결 실패 → AP(핫스팟) 모드로 전환")

    wait_nmcli_ready()

    # 혹시 모를 기존 연결 정리
    run(["nmcli", "radio", "wifi", "on"])

    # nmcli 핫스팟 시작
    res = run([
        "nmcli", "dev", "wifi", "hotspot",
        "ifname", WIFI_IFACE,
        "ssid", AP_SSID,
        "password", AP_PW
    ])
    print(res.stdout)
    print("📶 AP SSID =", AP_SSID)
    print("📌 폰/노트북에서 이 AP에 접속한 뒤, 브라우저에서 http://10.42.0.1 접속")
    print(f"🔗 웹 포털 포트: {HTTP_PORT}")


class WifiConfigHandler(BaseHTTPRequestHandler):
    def _send_html(self, body: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        # 어떤 경로로 와도 설정 폼
        self._send_html(FORM_HTML)

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        data = parse_qs(raw)

        ssid = (data.get("ssid", [""])[0]).strip()
        pw   = (data.get("pw",   [""])[0]).strip()

        if not ssid or not pw:
            self._send_html("<p>SSID와 PW를 모두 입력해주세요.</p>" + FORM_HTML)
            return

        cfg = {"ssid": ssid, "password": pw}
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            print("✅ 새 Wi-Fi 설정 저장:", {"ssid": ssid, "password": "***"})
        except Exception as e:
            print("❌ 설정 저장 실패:", e)
            self._send_html("<p>설정 저장에 실패했습니다.</p>" + FORM_HTML)
            return

        self._send_html(SAVED_HTML)

        # 3초 후 재부팅
        print("♻️ 3초 후 재부팅 예정...")
        os.system("sleep 3 && reboot &")


def run_config_portal():
    """AP 모드를 켠 뒤, HTTP 포털 실행 (무한 대기)"""
    start_hotspot()
    server_addr = ("0.0.0.0", HTTP_PORT)
    httpd = HTTPServer(server_addr, WifiConfigHandler)
    print(f"🌐 설정 포털 시작: http://0.0.0.0:{HTTP_PORT}")
    httpd.serve_forever()


# ---------------- main ----------------

def main():
    # 1) config가 있고 Wi-Fi 연결에 성공하면 → 그냥 종료
    if try_connect_wifi_from_config():
        # 여기서 스크립트가 끝나면 systemd 서비스는 "running 끝 / success" 상태
        # 이후 다른 서비스(decision, mosquitto 등)들이 평소처럼 동작
        return

    # 2) config가 없거나, 연결에 실패하면 → AP + 설정 포털
    run_config_portal()


if __name__ == "__main__":
    main()
