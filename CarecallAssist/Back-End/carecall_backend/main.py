from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal, get_db, init_db
from groq_service import (
    CareCallChatConfigurationError,
    CareCallChatRateLimitError,
    CareCallChatUnavailableError,
    generate_carecall_answer,
)
from repositories import (
    acknowledge_event as acknowledge_event_in_db,
    create_event,
    get_latest_status as get_latest_status_from_db,
    list_events,
    save_latest_status,
    upsert_fcm_token,
)
from seed import seed_database


app = FastAPI(title="CareCall Mock API", version="0.1.0")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def initialize_database() -> None:
    init_db()

    with SessionLocal() as db:
        seed_database(db)


initialize_database()


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
def get_latest_status(db: Session = Depends(get_db)):
    status = get_latest_status_from_db(db)

    if status is None:
        raise HTTPException(status_code=404, detail="최신 상태를 찾을 수 없습니다.")

    return status


@app.get("/api/v1/events")
def get_events(db: Session = Depends(get_db)):
    return {"events": list_events(db)}


@app.patch("/api/v1/events/{event_id}/ack")
def acknowledge_event(
    event_id: str,
    request: AckRequest,
    db: Session = Depends(get_db),
):
    acknowledged_at = now_iso()
    event = acknowledge_event_in_db(
        db,
        event_id=event_id,
        guardian_id=request.guardian_id,
        acknowledged=request.acknowledged,
        acknowledged_at=acknowledged_at,
    )

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="해당 이벤트를 찾을 수 없습니다.",
        )

    return {
        "event_id": event_id,
        "acknowledged": event["acknowledged"],
        "acknowledged_by": event["acknowledged_by"],
        "acknowledged_at": event["acknowledged_at"],
    }


@app.post("/api/v1/devices/fcm-token")
def register_fcm_token(
    request: FcmTokenRequest,
    db: Session = Depends(get_db),
):
    upsert_fcm_token(
        db,
        guardian_id=request.guardian_id,
        user_id=request.user_id,
        platform=request.platform,
        fcm_token=request.fcm_token,
        now=now_iso(),
    )

    return {
        "registered": True,
        "guardian_id": request.guardian_id,
        "user_id": request.user_id,
        "platform": request.platform,
    }


@app.post("/api/v1/chat")
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=422, detail="질문을 입력해 주세요.")

    latest_status = get_latest_status_from_db(db, user_id=request.user_id) or {}
    events = list_events(db, user_id=request.user_id)

    try:
        answer = generate_carecall_answer(
            question=question,
            user_id=request.user_id,
            latest_status=latest_status,
            events=events,
        )
    except CareCallChatRateLimitError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except CareCallChatConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CareCallChatUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return {"answer": answer}


@app.post("/api/v1/edge/motion")
def receive_edge_motion(
    request: EdgeMotionRequest,
    db: Session = Depends(get_db),
):
    occurred_at = request.occurred_at or now_iso()
    previous_status = get_latest_status_from_db(db, user_id=request.user_id) or {}

    save_latest_status(
        db,
        {
            "user_id": request.user_id,
            "room": request.room,
            "posture": request.posture,
            "motion_state": request.motion_state,
            "fall_risk": request.fall_risk,
            "last_event_type": "posture_updated",
            "body_part": previous_status.get("body_part"),
            "last_impact_at": previous_status.get("last_impact_at"),
            "updated_at": occurred_at,
            "camera_stream_url": request.stream_url or "",
            "snapshot_url": request.snapshot_url or "",
            "online": True,
        },
    )

    event_created = False

    if request.fall_risk == "high" or request.posture == "fallen":
        event_created = True
        create_event(
            db,
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
def create_test_help_request(db: Session = Depends(get_db)):
    occurred_at = now_iso()
    event = create_event(
        db,
        {
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
            "occurred_at": occurred_at,
            "acknowledged": False,
        },
    )

    previous_status = get_latest_status_from_db(db, user_id="user_01") or {}
    save_latest_status(
        db,
        {
            "user_id": "user_01",
            "room": previous_status.get("room", "거실"),
            "posture": previous_status.get("posture", "unknown"),
            "motion_state": previous_status.get("motion_state", "unknown"),
            "fall_risk": previous_status.get("fall_risk", "unknown"),
            "last_event_type": "help_request",
            "body_part": previous_status.get("body_part"),
            "last_impact_at": previous_status.get("last_impact_at"),
            "updated_at": occurred_at,
            "camera_stream_url": previous_status.get("camera_stream_url", ""),
            "snapshot_url": previous_status.get("snapshot_url", ""),
            "online": previous_status.get("online", True),
        },
    )

    return {"created": True, "event": event}
