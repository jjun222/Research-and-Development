from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import EventRecord, FcmTokenRecord, LatestStatusRecord


STATUS_FIELDS = (
    "user_id",
    "room",
    "posture",
    "motion_state",
    "fall_risk",
    "last_event_type",
    "body_part",
    "last_impact_at",
    "updated_at",
    "camera_stream_url",
    "snapshot_url",
    "online",
)

EVENT_FIELDS = (
    "event_id",
    "user_id",
    "title",
    "body",
    "location",
    "device_id",
    "event_type",
    "severity",
    "body_part",
    "posture",
    "confidence",
    "image_url",
    "stream_url",
    "occurred_at",
    "acknowledged",
)


def status_to_dict(record: LatestStatusRecord) -> dict[str, Any]:
    return {field: getattr(record, field) for field in STATUS_FIELDS}


def event_to_dict(record: EventRecord) -> dict[str, Any]:
    data = {field: getattr(record, field) for field in EVENT_FIELDS}

    if record.acknowledged_by is not None:
        data["acknowledged_by"] = record.acknowledged_by

    if record.acknowledged_at is not None:
        data["acknowledged_at"] = record.acknowledged_at

    return data


def get_latest_status(
    db: Session,
    user_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if user_id is not None:
        record = db.get(LatestStatusRecord, user_id)
    else:
        record = db.scalars(
            select(LatestStatusRecord)
            .order_by(func.datetime(LatestStatusRecord.updated_at).desc())
            .limit(1)
        ).first()

    if record is None:
        return None

    return status_to_dict(record)


def save_latest_status(
    db: Session,
    data: dict[str, Any],
) -> dict[str, Any]:
    record = db.get(LatestStatusRecord, data["user_id"])

    if record is None:
        record = LatestStatusRecord(**data)
        db.add(record)
    else:
        for field in STATUS_FIELDS:
            if field in data:
                setattr(record, field, data[field])

    db.commit()
    db.refresh(record)
    return status_to_dict(record)


def list_events(
    db: Session,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    statement = select(EventRecord)

    if user_id is not None:
        statement = statement.where(EventRecord.user_id == user_id)

    records = db.scalars(
        statement.order_by(func.datetime(EventRecord.occurred_at).desc())
    ).all()
    return [event_to_dict(record) for record in records]


def create_event(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    record = EventRecord(**data)
    db.add(record)
    db.commit()
    db.refresh(record)
    return event_to_dict(record)


def acknowledge_event(
    db: Session,
    event_id: str,
    guardian_id: str,
    acknowledged: bool,
    acknowledged_at: str,
) -> Optional[dict[str, Any]]:
    record = db.get(EventRecord, event_id)

    if record is None:
        return None

    record.acknowledged = acknowledged
    record.acknowledged_by = guardian_id
    record.acknowledged_at = acknowledged_at
    db.commit()
    db.refresh(record)
    return event_to_dict(record)


def upsert_fcm_token(
    db: Session,
    *,
    guardian_id: str,
    user_id: str,
    platform: str,
    fcm_token: str,
    now: str,
) -> FcmTokenRecord:
    record = db.scalar(
        select(FcmTokenRecord).where(FcmTokenRecord.fcm_token == fcm_token)
    )

    if record is None:
        record = FcmTokenRecord(
            guardian_id=guardian_id,
            user_id=user_id,
            platform=platform,
            fcm_token=fcm_token,
            registered_at=now,
            updated_at=now,
            active=True,
        )
        db.add(record)
    else:
        record.guardian_id = guardian_id
        record.user_id = user_id
        record.platform = platform
        record.updated_at = now
        record.active = True

    db.commit()
    db.refresh(record)
    return record
