# shz flame sensor - final stable for SR test
import machine, time, network, ujson, gc
import socket, os
from simple import MQTTClient

FIRE_SENSOR_PIN = 15
fire_sensor = machine.Pin(FIRE_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_UP)  # 0이면 감지

LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)

try:
    onboard_led = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    onboard_led = None

WIFI_SSID = ""
WIFI_PASSWORD = ""

CONFIG_PATH       = "wifi_config.json"
DEFAULT_BROKER_IP = "192.168.0.33"

MQTT_BROKER    = DEFAULT_BROKER_IP
MQTT_TOPIC     = "shz/sensor"
MQTT_CLIENT_ID = "shz_sensor_pico"
STATUS_TOPIC   = "interfaceui/status/publisher/" + MQTT_CLIENT_ID

TEST_GROUP   = "SR"
TEST_ID      = "SR-01C"
SCENARIO_ID  = "shz_to_broker"
TRIAL_NO     = 1
PRIORITY_EMERGENCY = "emergency"
PRIORITY_ROUTINE   = "routine"
SEQ = 0

KEEPALIVE_SEC        = 60
PING_INTERVAL_MS     = 30000
HEARTBEAT_MS         = 10000
STATUS_HEARTBEAT_MS  = 60000
PUBLISH_GAP_MS       = 120

WIFI_RETRY_MAX       = 15
MQTT_RECONNECT_MAX   = 10
MAX_RECOVERY_FAILS   = 5

BOOT_SETTLE_MS       = 2000
SAMPLE_INTERVAL_MS   = 20
DETECT_STABLE_COUNT  = 5
CLEAR_STABLE_COUNT   = 5
MIN_EVENT_GAP_MS     = 1000
LED_BLINK_MS         = 500

GC_INTERVAL_MS       = 20000
WDT_TIMEOUT_MS       = 8000
SOCKET_TIMEOUT_SEC   = 3
DEBUG_PUBLISH        = True
ENABLE_STATUS_HEARTBEAT = False
FORCE_ROTATE_AFTER_PUBLISHES = 40

wlan   = None
client = None
wdt    = None
recovery_fail_count = 0
publish_success_count = 0
BOOT_TICKS_MS = time.ticks_ms()

AP_SSID = "shz_sensor_setup"
AP_PW   = "123456789"

HTML_FORM = """\
HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>WiFi 설정</title></head>
<body>
<h2>Wi-Fi / MQTT 설정</h2>
<form method="POST" action="/save">
  SSID: <input name="ssid"><br>
  PW:   <input name="pw" type="password"><br>
  Broker IP: <input name="broker" value="%s"><br>
  <button type="submit">저장</button>
</form>
</body>
</html>
""" % DEFAULT_BROKER_IP

HTML_SAVED = """\
HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body>
<p>저장되었습니다. 3초 후 재부팅합니다.</p>
</body></html>
"""

def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

def uptime_ms():
    return time.ticks_diff(time.ticks_ms(), BOOT_TICKS_MS)

def next_seq():
    global SEQ
    SEQ += 1
    return SEQ

def reset_cause_name():
    cause = machine.reset_cause()
    mapping = {}
    if hasattr(machine, "PWRON_RESET"):
        mapping[machine.PWRON_RESET] = "PWRON_RESET"
    if hasattr(machine, "HARD_RESET"):
        mapping[machine.HARD_RESET] = "HARD_RESET"
    if hasattr(machine, "WDT_RESET"):
        mapping[machine.WDT_RESET] = "WDT_RESET"
    if hasattr(machine, "DEEPSLEEP_RESET"):
        mapping[machine.DEEPSLEEP_RESET] = "DEEPSLEEP_RESET"
    if hasattr(machine, "SOFT_RESET"):
        mapping[machine.SOFT_RESET] = "SOFT_RESET"
    return mapping.get(cause, str(cause))

def start_watchdog():
    global wdt
    try:
        wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
        print("🛡️ WDT 시작:", WDT_TIMEOUT_MS, "ms")
    except Exception as e:
        wdt = None
        print("⚠️ WDT 시작 실패:", e)

def feed_wdt():
    try:
        if wdt is not None:
            wdt.feed()
    except Exception:
        pass

def maybe_gc(last_gc_ms):
    now = time.ticks_ms()
    if time.ticks_diff(now, last_gc_ms) >= GC_INTERVAL_MS:
        gc.collect()
        return now
    return last_gc_ms

def short_gap(ms=PUBLISH_GAP_MS):
    feed_wdt()
    gc.collect()
    time.sleep_ms(ms)
    feed_wdt()

def build_test_fields(priority_class):
    return {
        "test_group": TEST_GROUP,
        "test_id": TEST_ID,
        "scenario_id": SCENARIO_ID,
        "trial_no": TRIAL_NO,
        "seq": next_seq(),
        "t_sent_ms": int(uptime_ms()),
        "publisher_clock": "monotonic_uptime_ms",
        "publisher_reset_cause": reset_cause_name(),
        "priority_class": priority_class,
        "source_path": "shz_sensor->broker",
        "expected_inputs": 1,
        "received_inputs": 1,
        "expected_devices": 1,
        "activated_devices": 0
    }

def set_led(v):
    val = 1 if v else 0
    led.value(val)
    if onboard_led is not None:
        onboard_led.value(val)

def blink_once(on_ms=80, off_ms=80):
    set_led(True)
    time.sleep_ms(on_ms)
    set_led(False)
    time.sleep_ms(off_ms)

def blink_n(n, on_ms=80, off_ms=80):
    for _ in range(n):
        blink_once(on_ms, off_ms)

def get_ip():
    try:
        if wlan is not None and wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return ""

def load_wifi_config():
    if CONFIG_PATH not in os.listdir():
        return None
    try:
        with open(CONFIG_PATH, "r") as f:
            return ujson.loads(f.read())
    except Exception as e:
        print("⚠️ config load 실패:", e)
        return None

def save_wifi_config(ssid, pw, broker_ip=None):
    cfg = {"ssid": ssid, "password": pw}
    if broker_ip:
        cfg["broker"] = broker_ip
    try:
        with open(CONFIG_PATH, "w") as f:
            f.write(ujson.dumps(cfg))
        print("✅ Wi-Fi 설정 저장 완료:", cfg)
    except Exception as e:
        print("❌ config 저장 실패:", e)

def url_decode(s):
    res = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == '+':
            res += ' '
        elif c == '%' and i + 2 < len(s):
            try:
                res += chr(int(s[i + 1:i + 3], 16))
                i += 2
            except Exception:
                res += c
        else:
            res += c
        i += 1
    return res

def parse_form(body):
    out = {}
    parts = body.split('&')
    for p in parts:
        if '=' in p:
            k, v = p.split('=', 1)
            out[k] = url_decode(v)
    return out

def reset_wifi_interface():
    global wlan
    try:
        if wlan is None:
            wlan = network.WLAN(network.STA_IF)
        wlan.active(False)
        time.sleep_ms(300)
        wlan.active(True)
        time.sleep_ms(300)
    except Exception as e:
        print("⚠️ Wi-Fi 인터페이스 리셋 실패:", e)

def try_connect_wifi(ssid, pw):
    global wlan
    if not ssid or not pw:
        print("⚠️ SSID 또는 PW 없음")
        return False

    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)

    try:
        wlan.disconnect()
    except Exception:
        pass

    reset_wifi_interface()

    print("📡 Wi-Fi 연결 시도:", ssid)
    wlan.connect(ssid, pw)

    attempt = 0
    while not wlan.isconnected() and attempt < WIFI_RETRY_MAX:
        feed_wdt()
        attempt += 1
        time.sleep(0.5)

    if not wlan.isconnected():
        print("❌ Wi-Fi 연결 실패")
        return False

    print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
    blink_n(3)
    return True

def connect_wifi_from_config():
    global MQTT_BROKER
    cfg = load_wifi_config()
    if cfg:
        ssid = cfg.get("ssid")
        pw = cfg.get("password")
        if ssid and pw:
            if try_connect_wifi(ssid, pw):
                MQTT_BROKER = cfg.get("broker") or DEFAULT_BROKER_IP
                print("🌐 config로 Wi-Fi 연결 OK, broker =", MQTT_BROKER)
                return True

    if WIFI_SSID and WIFI_PASSWORD:
        if try_connect_wifi(WIFI_SSID, WIFI_PASSWORD):
            MQTT_BROKER = DEFAULT_BROKER_IP
            return True

    return False

def wifi_connect():
    return connect_wifi_from_config()

def wifi_ensure():
    if wlan is None or (not wlan.isconnected()):
        print("⚠️ Wi-Fi 미연결 감지 → 재연결")
        return wifi_connect()
    return True

def start_config_portal():
    sta = network.WLAN(network.STA_IF)
    sta.active(False)
    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PW)
    ap.active(True)
    print("📶 AP 모드 시작:", ap.ifconfig())

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    s.bind(addr)
    s.listen(1)

    while True:
        cl, addr = s.accept()
        print("새 접속:", addr)
        req = cl.recv(1024)
        try:
            req_str = req.decode()
        except Exception:
            req_str = ""

        if "POST /save" in req_str:
            parts = req_str.split("\r\n\r\n", 1)
            body = parts[1] if len(parts) > 1 else ""
            form = parse_form(body)
            ssid = form.get("ssid", "").strip()
            pw = form.get("pw", "").strip()
            broker = form.get("broker", "").strip()
            if ssid and pw:
                save_wifi_config(ssid, pw, broker or None)
                cl.send(HTML_SAVED.encode())
                cl.close()
                time.sleep(3)
                machine.reset()
            else:
                cl.send(HTML_FORM.encode())
                cl.close()
        else:
            cl.send(HTML_FORM.encode())
            cl.close()

def startup_wifi_or_portal():
    if wifi_connect():
        return True
    print("⚠️ Wi-Fi 접속 실패 → AP 모드 진입")
    start_config_portal()
    return False

def close_mqtt_client():
    global client
    try:
        if client is not None:
            try:
                if hasattr(client, "sock") and client.sock is not None:
                    try:
                        client.sock.close()
                    except Exception:
                        pass
                    client.sock = None
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
    except Exception:
        pass
    client = None
    gc.collect()

def apply_socket_timeout():
    global client
    try:
        if client is not None and hasattr(client, "sock") and client.sock is not None:
            client.sock.settimeout(SOCKET_TIMEOUT_SEC)
            print("⏱️ MQTT socket timeout =", SOCKET_TIMEOUT_SEC, "sec")
    except Exception as e:
        print("⚠️ socket timeout 설정 실패:", e)

def mqtt_connect():
    global client, publish_success_count
    try:
        close_mqtt_client()
        gc.collect()
        client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
        client.connect()
        apply_socket_timeout()
        publish_success_count = 0

        print("✅ MQTT 연결 완료 (broker =", MQTT_BROKER, ")")
        blink_n(5)
        publish_device_status(state="online", value=None, reason="mqtt_connected")
        return True
    except Exception as e:
        print("❌ MQTT 연결 실패:", e)
        close_mqtt_client()
        return False

def mqtt_ping():
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception:
        return False

def mqtt_reconnect_with_backoff():
    backoff = 0.5
    for attempt in range(MQTT_RECONNECT_MAX):
        feed_wdt()
        print("🔁 MQTT 재연결 시도", attempt + 1, "(free mem =", gc.mem_free(), ")")
        close_mqtt_client()
        gc.collect()
        reset_wifi_interface()

        ok_wifi = wifi_ensure()
        if not ok_wifi:
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            continue
        try:
            if mqtt_connect():
                print("✅ MQTT 재연결 성공")
                return True
        except Exception as e:
            print("❌ MQTT 재연결 중 예외:", e)
        gc.collect()
        time.sleep(backoff)
        backoff = min(backoff * 2, 5)
    print("🚫 MQTT 재연결 포기")
    return False

def maybe_rotate_mqtt():
    global publish_success_count
    if publish_success_count >= FORCE_ROTATE_AFTER_PUBLISHES:
        print("♻️ 정기 MQTT 회전 실행 (publish count =", publish_success_count, ")")
        mqtt_reconnect_with_backoff()
        publish_success_count = 0

def hard_recover(reason="unknown"):
    print("♻️ 하드 복구 실행:", reason)
    blink_n(4, 120, 120)
    close_mqtt_client()
    reset_wifi_interface()
    time.sleep(2)
    machine.reset()

def publish_json(topic, obj):
    global publish_success_count
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5
    for attempt in range(4):
        feed_wdt()

        if client is None:
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue

        try:
            gc.collect()
            if DEBUG_PUBLISH:
                print("➡️ publish start:", topic, "try=", attempt + 1, "len=", len(msg), "free=", gc.mem_free())
            client.publish(topic, msg)
            if DEBUG_PUBLISH:
                print("✅ publish done :", topic)
            publish_success_count += 1
            maybe_rotate_mqtt()
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt + 1), e, "(free=", gc.mem_free(), ")")
            close_mqtt_client()
            gc.collect()
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)

    return False

def publish_device_status(state="normal", value=None, reason="heartbeat"):
    payload = {
        "id": MQTT_CLIENT_ID,
        "type": "publisher",
        "device_type": "flame_sensor",
        "online": True,
        "wifi": (wlan is not None and wlan.isconnected()),
        "mqtt": (client is not None),
        "ip": get_ip(),
        "state": state,
        "value": value,
        "reason": reason,
        "ts": now_str(),
        "publisher_uptime_ms": int(uptime_ms()),
        "publisher_reset_cause": reset_cause_name()
    }
    return publish_json(STATUS_TOPIC, payload)

def send_status(is_detected, reason="heartbeat"):
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "shz_detected",
        "status": "불꽃 감지!" if is_detected else "정상",
        "value": 1 if is_detected else 0,
        "raw_active_low": 0 if is_detected else 1,
        "reason": reason,
        "timestamp": now_str(),
        "publisher_uptime_ms": int(uptime_ms()),
        "publisher_reset_cause": reset_cause_name()
    }
    payload.update(build_test_fields(PRIORITY_EMERGENCY if is_detected else PRIORITY_ROUTINE))

    print("📤 상태 전송:", "🔥 불꽃 감지!" if is_detected else "✅ 정상",
          "(reason=%s, seq=%d)" % (reason, payload["seq"]))

    blink_once(40, 40)
    ok = publish_json(MQTT_TOPIC, payload)
    if not ok:
        print("❌ 상태 publish 최종 실패")
    return ok

def read_sensor_active():
    return fire_sensor.value() == 0

def main():
    global recovery_fail_count

    print("====================================")
    print("🚀 BOOT START")
    print("🧾 reset cause =", reset_cause_name())
    print("🧠 free mem before gc =", gc.mem_free())
    gc.collect()
    print("🧠 free mem after  gc =", gc.mem_free())
    print("====================================")

    set_led(False)
    blink_n(2)
    time.sleep_ms(BOOT_SETTLE_MS)

    startup_wifi_or_portal()
    while not mqtt_connect():
        print("❌ 초기 MQTT 연결 실패, 5초 후 재시도")
        time.sleep(5)

    start_watchdog()

    print("📍 SHZ 센서 모니터링 시작")

    t_ping = time.ticks_ms()
    t_last_heartbeat = time.ticks_ms()
    t_last_status = time.ticks_ms()
    t_last_sample = time.ticks_ms()
    t_last_gc = time.ticks_ms()
    last_event_ms = 0
    last_led_ms = time.ticks_ms()

    detect_count = 0
    clear_count = 0
    stable_detected = False
    last_sent_state = None

    while True:
        feed_wdt()
        now = time.ticks_ms()
        t_last_gc = maybe_gc(t_last_gc)

        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            print("🔎 keepalive check")
            ok_wifi = wifi_ensure()
            ok_mqtt = mqtt_ping() if ok_wifi else False
            if (not ok_wifi) or (not ok_mqtt):
                if not mqtt_reconnect_with_backoff():
                    recovery_fail_count += 1
                    print("⚠️ 복구 실패 누적:", recovery_fail_count)
                    blink_once(200, 200)
                    if recovery_fail_count >= MAX_RECOVERY_FAILS:
                        hard_recover("shz wifi/mqtt stuck")
                else:
                    recovery_fail_count = 0
            else:
                recovery_fail_count = 0
            t_ping = now

        if time.ticks_diff(now, t_last_sample) < SAMPLE_INTERVAL_MS:
            time.sleep_ms(5)
            continue
        t_last_sample = now

        active = read_sensor_active()

        if active:
            detect_count += 1
            clear_count = 0
        else:
            clear_count += 1
            detect_count = 0

        if (not stable_detected) and detect_count >= DETECT_STABLE_COUNT:
            if time.ticks_diff(now, last_event_ms) >= MIN_EVENT_GAP_MS:
                stable_detected = True
                last_event_ms = now

                ok1 = send_status(True, "state_change")
                short_gap()
                ok2 = publish_device_status(state="alarm", value=1, reason="state_change")
                if not ok1:
                    print("⚠️ state_change sensor publish 실패")
                if not ok2:
                    print("⚠️ state_change status publish 실패")

                last_sent_state = True
                t_last_heartbeat = now
                t_last_status = now
                print("🔥 SHZ 안정 감지 전환")

        elif stable_detected and clear_count >= CLEAR_STABLE_COUNT:
            if time.ticks_diff(now, last_event_ms) >= MIN_EVENT_GAP_MS:
                stable_detected = False
                last_event_ms = now

                ok1 = send_status(False, "state_change")
                short_gap()
                ok2 = publish_device_status(state="normal", value=0, reason="state_change")
                if not ok1:
                    print("⚠️ state_change sensor publish 실패")
                if not ok2:
                    print("⚠️ state_change status publish 실패")

                last_sent_state = False
                t_last_heartbeat = now
                t_last_status = now
                print("🔄 SHZ 정상 복귀")

        if time.ticks_diff(now, t_last_heartbeat) >= HEARTBEAT_MS:
            if last_sent_state is None:
                ok = send_status(stable_detected, "startup_sync")
                last_sent_state = stable_detected
            else:
                ok = send_status(stable_detected, "heartbeat")
            if not ok:
                print("⚠️ heartbeat sensor publish 실패")
            t_last_heartbeat = now

        if ENABLE_STATUS_HEARTBEAT and time.ticks_diff(now, t_last_status) >= STATUS_HEARTBEAT_MS:
            short_gap()
            publish_device_status(state="alarm" if stable_detected else "normal",
                                  value=1 if stable_detected else 0,
                                  reason="heartbeat")
            t_last_status = now

        if stable_detected:
            set_led(True)
        else:
            if time.ticks_diff(now, last_led_ms) >= LED_BLINK_MS:
                last_led_ms = now
                set_led(not led.value())

        time.sleep_ms(5)

main()
