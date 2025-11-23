# firebase/firebase_utils.py
import os, json
import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin import exceptions as fae
from google.auth.exceptions import RefreshError  # ✔ 인증 토큰 발급 실패 캐치

# 경로 상수 (환경변수 우선, 없으면 기본값)
TOKENS_PATH = os.environ.get("FCM_TOKENS_PATH", "/home/mqtt/MQTTpr/firebase/tokens.txt")
KEY_PATH    = os.environ.get("FIREBASE_KEY_PATH", "/home/mqtt/MQTTpr/firebase/pushalret-firebase-adminsdk-fbsvc-627f134ad9.json")

# 안드로이드 알림 채널(앱과 동일해야 함)
ANDROID_CHANNEL_ID = "alerts"

# 키 메타 로깅용
_key_info_cache = None

def _tail(s: str, n: int = 10) -> str:
    try:
        return s[-n:] if s and len(s) > n else (s or "")
    except Exception:
        return ""

def initialize_firebase():
    """Firebase Admin 1회 초기화 + 키 메타 출력"""
    global _key_info_cache
    if firebase_admin._apps:
        return
    if not os.path.exists(KEY_PATH):
        raise FileNotFoundError(f"❌ Firebase 키 파일 없음: {KEY_PATH}")

    # 키 메타를 읽어 프로젝트/클라이언트 확인
    with open(KEY_PATH, "r", encoding="utf-8") as f:
        _key_info_cache = json.load(f)

    cred = credentials.Certificate(_key_info_cache)
    firebase_admin.initialize_app(cred)
    print("✅ Firebase Admin 초기화 완료 | "
          f"project_id={_key_info_cache.get('project_id')} "
          f"client_email={_key_info_cache.get('client_email')} "
          f"key_id={_key_info_cache.get('private_key_id')}")

def load_fcm_tokens(file_path: str = TOKENS_PATH):
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def save_fcm_token(token: str, file_path: str = TOKENS_PATH):
    tokens = load_fcm_tokens(file_path)
    if token not in tokens:
        # 디렉터리 없으면 생성
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(token + "\n")
        print(f"✅ FCM 토큰 저장: ...{_tail(token)}")
    else:
        print(f"ℹ️ 이미 등록된 토큰: ...{_tail(token)}")

def remove_fcm_token(bad_token: str, file_path: str = TOKENS_PATH):
    """유효하지 않은(만료/등록해제) 토큰을 파일에서 제거"""
    if not os.path.exists(file_path):
        return
    tokens = load_fcm_tokens(file_path)
    new_tokens = [t for t in tokens if t != bad_token]
    if len(new_tokens) != len(tokens):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_tokens) + ("\n" if new_tokens else ""))
        print(f"🧹 무효 토큰 제거: ...{_tail(bad_token)}")

def send_fcm_messages(title: str, body: str, token_file: str = TOKENS_PATH):
    """
    안드로이드 채널/우선순위/사운드 + data 포함 전송
    - 백그라운드: 시스템 알림 표시
    - 포그라운드: onMessageReceived() → 로컬 알림 처리 가능
    - 무효 토큰은 자동 정리
    """
    initialize_firebase()
    tokens = load_fcm_tokens(token_file)
    if not tokens:
        print("⚠️ 전송할 토큰이 없습니다.")
        return

    android_cfg = messaging.AndroidConfig(
        priority='high',
        notification=messaging.AndroidNotification(
            channel_id=ANDROID_CHANNEL_ID,
            sound='default',
        ),
        ttl=3600,  # 1시간
    )

    common_data = {
        "via": "mqtt_server",
        "title": title,
        "body": body,
        # 필요 시 "type": "ai_fire|shz|mq5|mq7|all_true|water|doorbell" 등 추가
    }

    for token in tokens:
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            android=android_cfg,
            data=common_data,
            token=token,
        )
        try:
            resp = messaging.send(msg)
            print(f"📤 FCM 전송 성공: ...{_tail(token)} → {resp}")

        except RefreshError as e:
            # ➜ 서비스계정 키/프로젝트/서버시간 문제일 확률 99%
            print(
                "❌ FCM 인증 실패(RefreshError). 대부분 '서비스계정 키 문제' 또는 '서버 시간 오차'입니다.\n"
                f"   detail={e}\n"
                f"   key.project_id={_key_info_cache.get('project_id') if _key_info_cache else '?'}\n"
                "   ▶ 조치: 1) Firebase 콘솔에서 새 서비스계정 키 생성/교체\n"
                "           2) timedatectl 로 서버 시간 동기화\n"
                "           3) 키 파일 손상/권한/경로 확인"
            )
            break

        except fae.FirebaseError as e:
            # 대표적인 만료/등록해제 케이스 제거
            code_s = getattr(e, "code", None)
            msg_s  = str(e)
            if (code_s and str(code_s).upper() in ("UNREGISTERED", "INVALID_ARGUMENT")) \
               or ("not registered" in msg_s.lower()) \
               or ("unregistered" in msg_s.lower()):
                print(f"❌ 무효/만료 토큰: ...{_tail(token)} → 자동 제거")
                remove_fcm_token(token, token_file)
            else:
                print(f"❌ FCM 전송 실패(...{_tail(token)}): {e}")

        except Exception as e:
            print(f"❌ FCM 전송 실패(...{_tail(token)}): {e}")
