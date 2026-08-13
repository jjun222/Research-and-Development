from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import EventRecord, LatestStatusRecord


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def seed_database(db: Session) -> None:
    seed_time = now_iso()

    if db.get(LatestStatusRecord, "user_01") is None:
        db.add(
            LatestStatusRecord(
                user_id="user_01",
                room="거실",
                posture="sitting",
                motion_state="stable",
                fall_risk="low",
                last_event_type="none",
                body_part=None,
                last_impact_at=None,
                updated_at=seed_time,
                camera_stream_url="",
                snapshot_url="",
                online=True,
            )
        )

    if db.get(EventRecord, "event_001") is None:
        db.add(
            EventRecord(
                event_id="event_001",
                user_id="user_01",
                title="도움 요청 테스트",
                body="거실 호출 버튼이 눌렸습니다.",
                location="거실",
                device_id="button_livingroom_01",
                event_type="help_request",
                severity="warning",
                body_part=None,
                posture=None,
                confidence=None,
                image_url=None,
                stream_url=None,
                occurred_at=seed_time,
                acknowledged=False,
            )
        )

    db.commit()
