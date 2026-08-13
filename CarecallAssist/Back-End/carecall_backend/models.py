from typing import Optional

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LatestStatusRecord(Base):
    __tablename__ = "latest_statuses"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    room: Mapped[str] = mapped_column(String(100))
    posture: Mapped[str] = mapped_column(String(100))
    motion_state: Mapped[str] = mapped_column(String(100))
    fall_risk: Mapped[str] = mapped_column(String(50))
    last_event_type: Mapped[str] = mapped_column(String(100))
    body_part: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_impact_at: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    updated_at: Mapped[str] = mapped_column(String(50), index=True)
    camera_stream_url: Mapped[str] = mapped_column(Text, default="")
    snapshot_url: Mapped[str] = mapped_column(Text, default="")
    online: Mapped[bool] = mapped_column(Boolean, default=True)


class EventRecord(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(100))
    device_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(50))
    body_part: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    posture: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stream_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[str] = mapped_column(String(50), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    acknowledged_at: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )


class FcmTokenRecord(Base):
    __tablename__ = "fcm_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guardian_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    platform: Mapped[str] = mapped_column(String(50))
    fcm_token: Mapped[str] = mapped_column(Text, unique=True)
    registered_at: Mapped[str] = mapped_column(String(50))
    updated_at: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
