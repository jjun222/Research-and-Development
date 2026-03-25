#!/usr/bin/env python3
import os
import json
import time
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

# ===== 설정 =====
CONFIG_PATH = "/opt/nodered_wifi_portal/wifi_config.json"

AP_SSID      = "NodeRED-Setup"
AP_PASSWORD  = "123456789"      # 8자 이상 (원하면 바꿔도 됨)
WIFI_IFACE   = "wlan0"          # ← nmcli dev 로 확인한 Wi-Fi 인터페이스 이름
HTTP_PORT    = 80               # AP에서 http://10.42.0.1 로 접속

# ===== 튜닝 값(여기만 바꾸면 됨) =====
NM_READY_TIMEOUT   = 45    # NetworkManager 준비 최대 대기(초)
SSID_WAIT_TIMEOUT  = 120   # SSID 스캔 대기(초) - SSID가 늦게 뜨는 환경 대비
CONNECT_RETRIES    = 3     # 연결 시도 횟수
CONNECT_WAIT_EACH  = 25    # 각 연결 시도 후 connected 확인 시간(초)
SCAN_INTERVAL      = 5     # 재스캔/체크 간격(초)

# Hotspot(=AP) 연결 이름을 고정해서 stop_ap() 안정화
HOTSPOT_CONN_NAME  = "NodeRED-Hotspot"

# ===== 공용 함수 =====
def run_cmd(cmd, check=False):
    """subprocess.run 래퍼 (리스트로 넘기는 걸 추천)"""
    print("[CMD]", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )
        if result.stdout:
            print("[OUT]", result.stdout.strip())
        if result.stderr:
            print("[ERR]", result.stderr.strip())
        return result
    except Exception as e:
        print("[ERR] run_cmd 예외:", e)
        return None

def wait_nm_ready(timeout_sec=NM_READY_TIMEOUT):
    """
    NetworkManager가 올라올 때까지 대기.
    부팅 직후 nmcli를 너무 빨리 치면 실패하는 경우가 있어서 추가.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        res = run_cmd(["nmcli", "-t", "-f", "RUNNING", "general"])
        out = (res.stdout or "").strip().lower() if res else ""
        # "running" 문자열 포함 여부가 아니라 정확히 비교(오탐 방지)
        if out == "running":
            print("✅ NetworkManager RUNNING")
            return True
        print("⏳ NetworkManager 대기 중...")
        time.sleep(2)
    print("⚠️ NetworkManager 준비 타임아웃. 계속 진행합니다.")
    return False

def get_wifi_state_and_conn():
    """
    WIFI_IFACE 상태/연결 이름을 가져옴.
    반환: (state, conn_name) / 실패 시 (None, None)
    """
    result = run_cmd(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "dev"])
    if not result or not result.stdout:
        return (None, None)

    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        # DEVICE:STATE:CONNECTION (CONNECTION은 비어있을 수도 있음)
        if len(parts) < 2:
            continue
        dev = parts[0]
        state = parts[1]
        conn = parts[2] if len(parts) >= 3 else ""
        if dev == WIFI_IFACE:
            return (state.strip(), (conn or "").strip())
    return (None, None)

def is_wifi_connected():
    """
    nmcli 로 WIFI_IFACE 가 'connected' 인지 확인하되,
    AP(Hotspot) 연결에 'connected'인 경우는 "정상 Wi-Fi 연결"로 보지 않음.
    """
    state, conn = get_wifi_state_and_conn()
    if not state:
        return False

    # connected / connected (externally) 같은 케이스 대비
    if not state.startswith("connected"):
        return False

    # Hotspot 연결이면 "외부 Wi-Fi 연결 성공"으로 판단하면 안 됨
    if conn in ("Hotspot", HOTSPOT_CONN_NAME) or conn == AP_SSID:
        return False

    return True

def wait_for_ssid(target_ssid, timeout_sec=SSID_WAIT_TIMEOUT):
    """
    SSID가 스캔 결과에 나타날 때까지 기다림(재스캔 포함).
    SSID가 부팅 직후 늦게 잡히는 경우를 대비.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        # 재스캔
        run_cmd(["nmcli", "dev", "wifi", "rescan", "ifname", WIFI_IFACE])

        # SSID 목록 조회
        res = run_cmd(["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "ifname", WIFI_IFACE])
        ssids = set()
        if res and res.stdout:
            for line in res.stdout.splitlines():
                s = line.strip()
                if s:
                    ssids.add(s)

        if target_ssid in ssids:
            print(f"✅ SSID 발견: {target_ssid}")
            return True

        elapsed = int(time.time() - t0)
        print(f"⏳ SSID 스캔 대기 중... {elapsed}/{timeout_sec}초")
        time.sleep(SCAN_INTERVAL)

    print(f"❌ SSID 미발견(타임아웃): {target_ssid}")
    return False

# ===== Wi-Fi 연결 로직 =====
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        return cfg
    except Exception as e:
        print("⚠️ wifi_config.json 로드 실패:", e)
        return None

def try_connect_from_config(timeout_sec=CONNECT_WAIT_EACH):
    """
    wifi_config.json 에서 ssid / password 읽어 Wi-Fi 접속 시도.
    이미 연결돼 있으면 True.
    실패하면 False.
    """
    wait_nm_ready()

    # 이미 "외부 Wi-Fi" 연결이면 OK
    if is_wifi_connected():
        print("✅ 이미 Wi-Fi 연결 상태입니다.")
        return True

    cfg = load_config()
    if not cfg:
        print("⚠️ wifi_config.json 없음 또는 파싱 실패")
        return False

    ssid = cfg.get("ssid", "").strip()
    pw   = cfg.get("password", "").strip()

    if not ssid or not pw:
        print("⚠️ config에 ssid/password 없음")
        return False

    # Wi-Fi 라디오 ON
    run_cmd(["nmcli", "radio", "wifi", "on"])

    # 혹시 핫스팟이 살아있으면 내려둠 (연결 시도 전에)
    stop_ap()

    # SSID가 늦게 뜨는 환경 대비: 스캔 대기
    print(f"🔎 SSID 스캔 대기 시작: {ssid} (최대 {SSID_WAIT_TIMEOUT}초)")
    if not wait_for_ssid(ssid, timeout_sec=SSID_WAIT_TIMEOUT):
        print("❌ SSID가 안 보여서 연결 시도 불가")
        return False

    # 연결 재시도 루프
    for attempt in range(1, CONNECT_RETRIES + 1):
        print(f"📡 Wi-Fi 연결 시도 {attempt}/{CONNECT_RETRIES} → SSID='{ssid}'")

        # 기존 연결 끊고 다시 연결
        run_cmd(["nmcli", "dev", "disconnect", WIFI_IFACE])

        # 연결 시도
        res = run_cmd([
            "nmcli", "dev", "wifi", "connect", ssid,
            "password", pw,
            "ifname", WIFI_IFACE,
        ])

        # 즉시 실패해도 (SSID가 떴다/사라졌다 등) timeout 동안 connected 확인
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            if is_wifi_connected():
                print("✅ Wi-Fi 연결 성공")
                return True
            time.sleep(SCAN_INTERVAL)

        # 다음 재시도 전에 재스캔 한 번
        run_cmd(["nmcli", "dev", "wifi", "rescan", "ifname", WIFI_IFACE])

        # nmcli 결과가 명확히 실패였다면 로그 참고용 출력
        if res and res.returncode != 0:
            print("❌ nmcli connect 실패(returncode != 0). 다음 재시도 진행...")

    print("❌ Wi-Fi 연결 실패 (재시도 소진)")
    return False

# ===== AP 모드 / 웹 포털 =====
def start_ap():
    """
    NodeRED-Setup AP 시작 (10.42.0.1 로 뜨는 것이 일반적)
    """
    wait_nm_ready()

    print("📶 AP 모드 시작 시도...")
    # 혹시 기존 연결 있으면 끊기
    run_cmd(["nmcli", "dev", "disconnect", WIFI_IFACE])

    # Wi-Fi 라디오 ON
    run_cmd(["nmcli", "radio", "wifi", "on"])

    # NetworkManager hotspot 모드 (con-name 고정)
    run_cmd([
        "nmcli", "dev", "wifi", "hotspot",
        "ifname", WIFI_IFACE,
        "con-name", HOTSPOT_CONN_NAME,
        "ssid", AP_SSID,
        "password", AP_PASSWORD
    ])

    print("📶 AP 모드 시작 완료 (SSID: {}, PW: {})".format(AP_SSID, AP_PASSWORD))
    print("➡ 폰/노트북에서 Wi-Fi '{}' 접속 후, 브라우저에서 http://10.42.0.1 열기".format(AP_SSID))

def stop_ap():
    """
    AP 모드 종료 (선택: 어차피 재부팅할 거라 무시해도 상관 없음)
    """
    print("📶 AP 모드 종료 시도...")
    # con-name 고정한 이름으로 내려보기
    run_cmd(["nmcli", "connection", "down", HOTSPOT_CONN_NAME])
    # 혹시 기본 이름(Hotspot)으로 떠있는 경우도 같이 처리
    run_cmd(["nmcli", "connection", "down", "Hotspot"])

class WifiPortalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/save"):
            # GET /save 는 폼으로 리다이렉트
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # 설정 폼 출력
        html = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Node-RED Wi-Fi 설정</title></head>
<body>
<h2>Node-RED 서버 Wi-Fi 설정</h2>
<p>현재 AP SSID: <b>{AP_SSID}</b></p>
<form method="POST" action="/save">
  <label>SSID: <input name="ssid"></label><br><br>
  <label>비밀번호: <input name="pw" type="password"></label><br><br>
  <button type="submit">저장 &amp; 재부팅</button>
</form>
</body>
</html>
"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404, "Not Found")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        data = self.rfile.read(length).decode("utf-8")
        form = parse_qs(data)

        ssid = (form.get("ssid") or [""])[0].strip()
        pw   = (form.get("pw")   or [""])[0].strip()

        if not ssid or not pw:
            html = """\
<html><body>
<p>SSID / 비밀번호를 모두 입력해주세요.</p>
<a href="/">뒤로</a>
</body></html>
"""
            body = html.encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 설정 저장
        cfg = {
            "ssid": ssid,
            "password": pw
        }
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        except FileExistsError:
            pass

        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)

        # 민감정보 로그는 최소화(SSID만 출력)
        print("✅ Wi-Fi 설정 저장:", {"ssid": ssid, "password": "***"})

        # 응답 보내고, 몇 초 뒤 리부트
        html = "<html><body><p>저장되었습니다. 3초 후 재부팅합니다.</p></body></html>"
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        def reboot_later():
            time.sleep(3)
            stop_ap()
            os.system("reboot")

        threading.Thread(target=reboot_later, daemon=True).start()

def run_portal_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), WifiPortalHandler)
    print(f"🌐 Wi-Fi 설정 포털 실행 중: 0.0.0.0:{HTTP_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

def main():
    # 1) 먼저 기존 config 기반으로 Wi-Fi 연결 시도
    if try_connect_from_config():
        print("✅ Wi-Fi 연결 OK, 포털 모드로 가지 않고 종료합니다.")
        return

    # 2) 실패 → AP 모드 + 포털 웹 서버
    start_ap()
    run_portal_server()

if __name__ == "__main__":
    main()
