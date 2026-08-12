from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="CareCall Mock API", version="0.1.0")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


latest_status = {
    "user_id": "user_01",
    "room": "거실",
    "posture": "sitting",
    "motion_state": "stable",
    "fall_risk": "low",
    "last_event_type": "none",
    "body_part": None,
    "last_impact_at": None,
    "updated_at": now_iso(),
    "camera_stream_url": "",
    "snapshot_url": "",
    "online": True,
}

events = [
    {
        "event_id": "event_001",
        "user_id": "user_01",
        "title": "도움 요청 테스트",
        "body": "거실 호출 버튼이 눌렸습니다.",
        "location": "거실",
        "device_id": "button_livingroom_01",
        "event_type": "help_request",
        "severity": "warning",
        "body_part": None,
        "posture": None,
        "confidence": None,
        "image_url": None,
        "stream_url": None,
        "occurred_at": now_iso(),
        "acknowledged": False,
    }
]

fcm_tokens = []


class AckRequest(BaseModel):
    guardian_id: str
    acknowledged: bool = True


class FcmTokenRequest(BaseModel):
    guardian_id: str
    user_id: str
    platform: str
    fcm_token: str


class ChatRequest(BaseModel):
    guardian_id: str
    user_id: str
    question: str


class EdgeMotionRequest(BaseModel):
    device_id: str
    user_id: str = "user_01"
    room: str
    posture: str
    motion_state: str = "unknown"
    fall_risk: str = "unknown"
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    person_detected: bool = True
    snapshot_url: Optional[str] = ""
    stream_url: Optional[str] = ""
    occurred_at: Optional[str] = None


@app.get("/api/v1/health")
def health_check():
    return {
        "status": "ok",
        "service": "carecall-mock-api",
        "version": "0.1.0",
    }


@app.get("/api/v1/status/latest")
def get_latest_status():
    return latest_status


@app.get("/api/v1/events")
def get_events():
    return {
        "events": sorted(
            events,
            key=lambda item: item["occurred_at"],
            reverse=True,
        )
    }


@app.patch("/api/v1/events/{event_id}/ack")
def acknowledge_event(event_id: str, request: AckRequest):
    for event in events:
        if event["event_id"] == event_id:
            event["acknowledged"] = request.acknowledged
            event["acknowledged_by"] = request.guardian_id
            event["acknowledged_at"] = now_iso()

            return {
                "event_id": event_id,
                "acknowledged": event["acknowledged"],
                "acknowledged_by": request.guardian_id,
                "acknowledged_at": event["acknowledged_at"],
            }

    return {
        "event_id": event_id,
        "acknowledged": False,
        "message": "해당 이벤트를 찾을 수 없습니다.",
    }


@app.post("/api/v1/devices/fcm-token")
def register_fcm_token(request: FcmTokenRequest):
    fcm_tokens.append(
        {
            "guardian_id": request.guardian_id,
            "user_id": request.user_id,
            "platform": request.platform,
            "fcm_token": request.fcm_token,
            "registered_at": now_iso(),
        }
    )

    return {
        "registered": True,
        "guardian_id": request.guardian_id,
        "user_id": request.user_id,
        "platform": request.platform,
    }


@app.post("/api/v1/chat")
def chat(request: ChatRequest):
    question = request.question.strip()

    if "어디" in question or "위치" in question:
        answer = f"현재 보호 대상자는 {latest_status['room']}에 있는 것으로 기록되어 있습니다."
    elif "충격" in question:
        answer = "현재 Mock 데이터 기준으로 최근 충격 발생 정보는 없습니다."
    elif "상태" in question or "자세" in question:
        answer = (
            f"현재 행동 상태는 {latest_status['posture']}이고, "
            f"낙상 위험도는 {latest_status['fall_risk']}로 기록되어 있습니다."
        )
    else:
        answer = (
            f"현재 보호 대상자는 {latest_status['room']}에 있으며, "
            f"행동 상태는 {latest_status['posture']}입니다. "
            f"낙상 위험도는 {latest_status['fall_risk']}입니다."
        )

    return {
        "answer": answer,
    }


@app.post("/api/v1/edge/motion")
def receive_edge_motion(request: EdgeMotionRequest):
    occurred_at = request.occurred_at or now_iso()

    latest_status.update(
        {
            "user_id": request.user_id,
            "room": request.room,
            "posture": request.posture,
            "motion_state": request.motion_state,
            "fall_risk": request.fall_risk,
            "last_event_type": "posture_updated",
            "updated_at": occurred_at,
            "camera_stream_url": request.stream_url or "",
            "snapshot_url": request.snapshot_url or "",
            "online": True,
        }
    )

    event_created = False

    if request.fall_risk == "high" or request.posture == "fallen":
        event_created = True

        events.insert(
            0,
            {
                "event_id": f"event_{uuid4().hex[:8]}",
                "user_id": request.user_id,
                "title": "낙상 의심",
                "body": f"{request.room}에서 낙상 의심 상태가 감지되었습니다.",
                "location": request.room,
                "device_id": request.device_id,
                "event_type": "fall_suspected",
                "severity": "critical",
                "body_part": None,
                "posture": request.posture,
                "confidence": request.confidence,
                "image_url": request.snapshot_url,
                "stream_url": request.stream_url,
                "occurred_at": occurred_at,
                "acknowledged": False,
            },
        )

    return {
        "saved": True,
        "event_created": event_created,
        "latest_status_updated": True,
    }


@app.post("/api/v1/dev/test/help-request")
def create_test_help_request():
    event = {
        "event_id": f"event_{uuid4().hex[:8]}",
        "user_id": "user_01",
        "title": "도움 요청",
        "body": "개발자 테스트로 생성한 도움 요청 이벤트입니다.",
        "location": "거실",
        "device_id": "dev_tool",
        "event_type": "help_request",
        "severity": "warning",
        "body_part": None,
        "posture": None,
        "confidence": None,
        "image_url": None,
        "stream_url": None,
        "occurred_at": now_iso(),
        "acknowledged": False,
    }

    events.insert(0, event)
    latest_status["last_event_type"] = "help_request"
    latest_status["updated_at"] = event["occurred_at"]

    return {
        "created": True,
        "event": event,
    }
