import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select


TEST_EDGE_DEVICE_API_KEY = "test-edge-device-key"

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["CARECALL_EDGE_DEVICE_API_KEY"] = TEST_EDGE_DEVICE_API_KEY
os.environ["CARECALL_ENABLE_DEV_ENDPOINTS"] = "true"

import fcm_service  # noqa: E402
import main  # noqa: E402
from database import Base, SessionLocal, engine  # noqa: E402
from models import (  # noqa: E402
    DeviceHeartbeatRecord,
    EventRecord,
    FcmTokenRecord,
    TelemetryRecord,
)
from seed import seed_database  # noqa: E402


client = TestClient(main.app)
EDGE_HEADERS = {
    "X-CareCall-Device-Key": TEST_EDGE_DEVICE_API_KEY,
}

STATUS_FIELDS = {
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
}

EVENT_FIELDS = {
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
}


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed_database(db)

    yield


def test_health_contract():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "carecall-mock-api",
        "version": "0.1.0",
    }


def test_latest_status_contract():
    response = client.get("/api/v1/status/latest")

    assert response.status_code == 200
    data = response.json()
    assert set(data) == STATUS_FIELDS
    assert data["user_id"] == "user_01"
    assert isinstance(data["online"], bool)
    assert isinstance(data["updated_at"], str)


def test_events_contract():
    response = client.get("/api/v1/events")

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"events"}
    assert isinstance(data["events"], list)
    assert data["events"]
    assert set(data["events"][0]) == EVENT_FIELDS


def test_acknowledge_event_contract():
    response = client.patch(
        "/api/v1/events/event_001/ack",
        json={
            "guardian_id": "guardian_01",
            "acknowledged": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "event_id",
        "acknowledged",
        "acknowledged_by",
        "acknowledged_at",
    }
    assert data["event_id"] == "event_001"
    assert data["acknowledged"] is True
    assert data["acknowledged_by"] == "guardian_01"
    assert isinstance(data["acknowledged_at"], str)

    events = client.get("/api/v1/events").json()["events"]
    event = next(item for item in events if item["event_id"] == "event_001")
    assert event["acknowledged"] is True
    assert event["acknowledged_by"] == "guardian_01"
    assert event["acknowledged_at"] == data["acknowledged_at"]


def test_missing_event_ack_returns_404():
    response = client.patch(
        "/api/v1/events/event_missing/ack",
        json={
            "guardian_id": "guardian_01",
            "acknowledged": True,
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "해당 이벤트를 찾을 수 없습니다."}


def test_ack_is_preserved_in_a_new_database_session():
    response = client.patch(
        "/api/v1/events/event_001/ack",
        json={
            "guardian_id": "guardian_01",
            "acknowledged": True,
        },
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        event = db.get(EventRecord, "event_001")
        assert event is not None
        assert event.acknowledged is True
        assert event.acknowledged_by == "guardian_01"
        assert event.acknowledged_at == response.json()["acknowledged_at"]

        seed_database(db)
        db.refresh(event)
        assert event.acknowledged is True


def test_fcm_token_registration_contract():
    response = client.post(
        "/api/v1/devices/fcm-token",
        json={
            "guardian_id": "guardian_01",
            "user_id": "user_01",
            "platform": "android",
            "fcm_token": "test-fcm-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "registered": True,
        "guardian_id": "guardian_01",
        "user_id": "user_01",
        "platform": "android",
    }

    with SessionLocal() as db:
        token = db.scalar(
            select(FcmTokenRecord).where(
                FcmTokenRecord.fcm_token == "test-fcm-token"
            )
        )
        assert token is not None
        assert token.guardian_id == "guardian_01"
        assert token.user_id == "user_01"
        assert token.platform == "android"
        assert token.active is True


def test_fcm_token_registration_uses_upsert():
    payload = {
        "guardian_id": "guardian_01",
        "user_id": "user_01",
        "platform": "android",
        "fcm_token": "same-fcm-token",
    }

    assert client.post("/api/v1/devices/fcm-token", json=payload).status_code == 200

    payload["platform"] = "ios"
    assert client.post("/api/v1/devices/fcm-token", json=payload).status_code == 200

    with SessionLocal() as db:
        token_count = db.scalar(select(func.count()).select_from(FcmTokenRecord))
        token = db.scalar(
            select(FcmTokenRecord).where(
                FcmTokenRecord.fcm_token == "same-fcm-token"
            )
        )
        assert token_count == 1
        assert token is not None
        assert token.platform == "ios"


def test_chat_contract_without_real_groq_call(monkeypatch):
    captured = {}

    def fake_generate_carecall_answer(**kwargs):
        captured.update(kwargs)
        return "현재 위치는 거실입니다."

    monkeypatch.setattr(
        main,
        "generate_carecall_answer",
        fake_generate_carecall_answer,
    )

    response = client.post(
        "/api/v1/chat",
        json={
            "guardian_id": "guardian_01",
            "user_id": "user_01",
            "question": "현재 어디에 있어?",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "현재 위치는 거실입니다."}
    assert captured["question"] == "현재 어디에 있어?"
    assert captured["user_id"] == "user_01"
    assert captured["latest_status"]["room"] == "거실"
    assert captured["events"][0]["event_id"] == "event_001"


def test_chat_rejects_blank_question():
    response = client.post(
        "/api/v1/chat",
        json={
            "guardian_id": "guardian_01",
            "user_id": "user_01",
            "question": "   ",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "질문을 입력해 주세요."}


def test_edge_motion_contract_and_fall_event_creation():
    response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.93,
            "person_detected": True,
            "snapshot_url": "https://example.test/snapshot.jpg",
            "stream_url": "https://example.test/stream.mjpg",
            "occurred_at": "2026-08-13T12:00:00+09:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "event_created": True,
        "latest_status_updated": True,
    }

    status = client.get("/api/v1/status/latest").json()
    assert set(status) == STATUS_FIELDS
    assert status["room"] == "침실"
    assert status["posture"] == "fallen"
    assert status["fall_risk"] == "high"
    assert status["updated_at"] == "2026-08-13T12:00:00+09:00"
    assert status["camera_stream_url"] == "https://example.test/stream.mjpg"
    assert status["snapshot_url"] == "https://example.test/snapshot.jpg"

    events = client.get("/api/v1/events").json()["events"]
    fall_event = next(
        item
        for item in events
        if item["event_type"] == "fall_suspected"
        and item["device_id"] == "jetson_01"
    )
    assert set(fall_event) == EVENT_FIELDS
    assert fall_event["location"] == "침실"
    assert fall_event["posture"] == "fallen"
    assert fall_event["confidence"] == 0.93
    assert fall_event["acknowledged"] is False


def test_edge_motion_does_not_duplicate_continuous_fall_event():
    with SessionLocal() as db:
        initial_fall_event_count = db.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(EventRecord.event_type == "fall_suspected")
        )

    first_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.93,
            "person_detected": True,
            "snapshot_url": "https://example.test/fall-1.jpg",
            "stream_url": "https://example.test/stream.mjpg",
            "occurred_at": "2026-08-13T12:00:00+09:00",
        },
    )

    repeated_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.95,
            "person_detected": True,
            "snapshot_url": "https://example.test/fall-2.jpg",
            "stream_url": "https://example.test/stream.mjpg",
            "occurred_at": "2026-08-13T12:00:05+09:00",
        },
    )

    assert first_fall_response.status_code == 200
    assert first_fall_response.json()["event_created"] is True

    assert repeated_fall_response.status_code == 200
    assert repeated_fall_response.json() == {
        "saved": True,
        "event_created": False,
        "latest_status_updated": True,
    }

    with SessionLocal() as db:
        final_fall_event_count = db.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(EventRecord.event_type == "fall_suspected")
        )

    assert final_fall_event_count == initial_fall_event_count + 1

    status = client.get("/api/v1/status/latest").json()
    assert status["posture"] == "fallen"
    assert status["fall_risk"] == "high"
    assert status["updated_at"] == "2026-08-13T12:00:05+09:00"
    assert status["snapshot_url"] == "https://example.test/fall-2.jpg"


def test_edge_motion_creates_new_event_after_fall_recovery():
    with SessionLocal() as db:
        initial_fall_event_count = db.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(EventRecord.event_type == "fall_suspected")
        )

    first_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "거실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.91,
            "person_detected": True,
            "occurred_at": "2026-08-13T13:00:00+09:00",
        },
    )

    recovery_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "거실",
            "posture": "standing",
            "motion_state": "moving",
            "fall_risk": "low",
            "confidence": 0.96,
            "person_detected": True,
            "occurred_at": "2026-08-13T13:01:00+09:00",
        },
    )

    assert first_fall_response.status_code == 200
    assert first_fall_response.json()["event_created"] is True

    assert recovery_response.status_code == 200
    assert recovery_response.json() == {
        "saved": True,
        "event_created": False,
        "latest_status_updated": True,
    }

    recovered_status = client.get("/api/v1/status/latest").json()
    assert recovered_status["posture"] == "standing"
    assert recovered_status["motion_state"] == "moving"
    assert recovered_status["fall_risk"] == "low"
    assert recovered_status["updated_at"] == "2026-08-13T13:01:00+09:00"

    second_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "거실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.94,
            "person_detected": True,
            "occurred_at": "2026-08-13T13:02:00+09:00",
        },
    )

    assert second_fall_response.status_code == 200
    assert second_fall_response.json() == {
        "saved": True,
        "event_created": True,
        "latest_status_updated": True,
    }

    with SessionLocal() as db:
        final_fall_event_count = db.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(EventRecord.event_type == "fall_suspected")
        )

    assert final_fall_event_count == initial_fall_event_count + 2

    latest_status = client.get("/api/v1/status/latest").json()
    assert latest_status["posture"] == "fallen"
    assert latest_status["fall_risk"] == "high"
    assert latest_status["updated_at"] == "2026-08-13T13:02:00+09:00"


def test_developer_help_request_contract():
    response = client.post("/api/v1/dev/test/help-request")

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"created", "event"}
    assert data["created"] is True
    assert set(data["event"]) == EVENT_FIELDS
    assert data["event"]["event_type"] == "help_request"
    assert data["event"]["device_id"] == "dev_tool"

    status = client.get("/api/v1/status/latest").json()
    assert status["last_event_type"] == "help_request"
    assert status["updated_at"] == data["event"]["occurred_at"]


def test_fcm_is_scheduled_only_for_new_fall_transitions(monkeypatch):
    register_response = client.post(
        "/api/v1/devices/fcm-token",
        json={
            "guardian_id": "guardian_01",
            "user_id": "user_01",
            "platform": "android",
            "fcm_token": "active-test-token",
        },
    )
    assert register_response.status_code == 200

    captured_notifications = []

    def fake_send_event_notification_safely(*, tokens, event):
        captured_notifications.append(
            {
                "tokens": list(tokens),
                "event": dict(event),
            }
        )

    monkeypatch.setattr(
        main,
        "send_event_notification_safely",
        fake_send_event_notification_safely,
    )

    first_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.93,
            "person_detected": True,
            "occurred_at": "2026-08-18T15:00:00+09:00",
        },
    )

    assert first_fall_response.status_code == 200
    assert first_fall_response.json()["event_created"] is True
    assert len(captured_notifications) == 1
    assert captured_notifications[0]["tokens"] == ["active-test-token"]
    assert captured_notifications[0]["event"]["event_type"] == "fall_suspected"

    repeated_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.95,
            "person_detected": True,
            "occurred_at": "2026-08-18T15:00:05+09:00",
        },
    )

    assert repeated_fall_response.status_code == 200
    assert repeated_fall_response.json()["event_created"] is False
    assert len(captured_notifications) == 1

    recovery_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "standing",
            "motion_state": "moving",
            "fall_risk": "low",
            "confidence": 0.98,
            "person_detected": True,
            "occurred_at": "2026-08-18T15:01:00+09:00",
        },
    )

    assert recovery_response.status_code == 200
    assert recovery_response.json()["event_created"] is False
    assert len(captured_notifications) == 1

    second_fall_response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "침실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.94,
            "person_detected": True,
            "occurred_at": "2026-08-18T15:02:00+09:00",
        },
    )

    assert second_fall_response.status_code == 200
    assert second_fall_response.json()["event_created"] is True
    assert len(captured_notifications) == 2
    assert (
        captured_notifications[0]["event"]["event_id"]
        != captured_notifications[1]["event"]["event_id"]
    )


def test_fcm_is_not_scheduled_without_an_active_token(monkeypatch):
    with SessionLocal() as db:
        db.add(
            FcmTokenRecord(
                guardian_id="guardian_01",
                user_id="user_01",
                platform="android",
                fcm_token="inactive-test-token",
                registered_at="2026-08-18T15:10:00+09:00",
                updated_at="2026-08-18T15:10:00+09:00",
                active=False,
            )
        )
        db.commit()

    captured_notifications = []

    def fake_send_event_notification_safely(*, tokens, event):
        captured_notifications.append((tokens, event))

    monkeypatch.setattr(
        main,
        "send_event_notification_safely",
        fake_send_event_notification_safely,
    )

    response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "거실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.91,
            "person_detected": True,
            "occurred_at": "2026-08-18T15:11:00+09:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "event_created": True,
        "latest_status_updated": True,
    }
    assert captured_notifications == []


def test_fcm_failure_does_not_break_status_or_event_storage(monkeypatch):
    register_response = client.post(
        "/api/v1/devices/fcm-token",
        json={
            "guardian_id": "guardian_01",
            "user_id": "user_01",
            "platform": "android",
            "fcm_token": "failing-test-token",
        },
    )
    assert register_response.status_code == 200

    def fake_send_event_notification(**kwargs):
        raise RuntimeError("simulated Firebase failure")

    monkeypatch.setattr(
        fcm_service,
        "send_event_notification",
        fake_send_event_notification,
    )

    response = client.post(
        "/api/v1/edge/motion",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "욕실",
            "posture": "fallen",
            "motion_state": "still",
            "fall_risk": "high",
            "confidence": 0.92,
            "person_detected": True,
            "occurred_at": "2026-08-18T15:20:00+09:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "event_created": True,
        "latest_status_updated": True,
    }

    status = client.get("/api/v1/status/latest").json()
    assert status["room"] == "욕실"
    assert status["posture"] == "fallen"
    assert status["fall_risk"] == "high"

    events = client.get("/api/v1/events").json()["events"]
    assert any(
        event["event_type"] == "fall_suspected"
        and event["location"] == "욕실"
        for event in events
    )


def test_fcm_event_data_contains_only_strings():
    data = fcm_service.build_event_fcm_data(
        {
            "event_id": "event_test",
            "user_id": "user_01",
            "event_type": "fall_suspected",
            "location": "거실",
            "device_id": "jetson_01",
            "severity": "critical",
            "posture": "fallen",
            "confidence": 0.93,
            "image_url": None,
            "stream_url": "",
            "occurred_at": "2026-08-18T15:30:00+09:00",
            "title": "낙상 의심",
            "body": "거실에서 낙상 의심 상태가 감지되었습니다.",
        }
    )

    assert data["confidence"] == "0.93"
    assert "image_url" not in data
    assert data["stream_url"] == ""
    assert all(isinstance(value, str) for value in data.values())


def test_edge_api_rejects_missing_or_invalid_device_key():
    payload = {
        "device_id": "jetson_01",
        "user_id": "user_01",
        "status": "online",
        "online": True,
    }

    missing_key_response = client.post(
        "/api/v1/edge/heartbeat",
        json=payload,
    )
    invalid_key_response = client.post(
        "/api/v1/edge/heartbeat",
        headers={"X-CareCall-Device-Key": "wrong-key"},
        json=payload,
    )

    assert missing_key_response.status_code == 401
    assert invalid_key_response.status_code == 401


def test_edge_api_returns_503_when_server_key_is_not_configured(monkeypatch):
    monkeypatch.setattr(main, "EDGE_DEVICE_API_KEY", "")

    response = client.post(
        "/api/v1/edge/heartbeat",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "status": "online",
            "online": True,
        },
    )

    assert response.status_code == 503


def test_edge_heartbeat_contract_and_storage():
    response = client.post(
        "/api/v1/edge/heartbeat",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "status": "online",
            "online": True,
            "software_version": "1.0.0",
            "occurred_at": "2026-08-27T15:00:00+09:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "device_id": "jetson_01",
        "online": True,
        "last_seen_at": "2026-08-27T15:00:00+09:00",
        "latest_status_updated": True,
    }

    with SessionLocal() as db:
        heartbeat = db.get(DeviceHeartbeatRecord, "jetson_01")
        assert heartbeat is not None
        assert heartbeat.user_id == "user_01"
        assert heartbeat.software_version == "1.0.0"


def test_edge_telemetry_contract_and_retention_limit():
    for sequence in range(101):
        response = client.post(
            "/api/v1/edge/telemetry",
            headers=EDGE_HEADERS,
            json={
                "device_id": "jetson_01",
                "user_id": "user_01",
                "body_part": "chest",
                "sequence": sequence,
                "accel": {"x": 0.1, "y": 0.2, "z": 9.8},
                "gyro": {"x": 0.01, "y": 0.02, "z": 0.03},
                "quaternion": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                "shock": 0,
                "measured_at": "2026-08-27T15:01:00+09:00",
            },
        )
        assert response.status_code == 200
        assert response.json()["retention_limit"] == 100

    with SessionLocal() as db:
        records = db.scalars(
            select(TelemetryRecord)
            .where(
                TelemetryRecord.device_id == "jetson_01",
                TelemetryRecord.body_part == "chest",
            )
            .order_by(TelemetryRecord.sequence)
        ).all()

    assert len(records) == 100
    assert records[0].sequence == 1
    assert records[-1].sequence == 100


def test_edge_impact_creates_warning_event_and_updates_status():
    response = client.post(
        "/api/v1/edge/impact",
        headers=EDGE_HEADERS,
        json={
            "device_id": "jetson_01",
            "user_id": "user_01",
            "room": "거실",
            "body_part": "right_arm",
            "shock": 1,
            "confidence": 0.88,
            "occurred_at": "2026-08-27T15:02:00+09:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "event_created": True,
        "latest_status_updated": True,
    }

    events = client.get("/api/v1/events").json()["events"]
    impact_event = next(
        event
        for event in events
        if event["event_type"] == "impact_detected"
    )
    assert impact_event["body_part"] == "right_arm"
    assert impact_event["severity"] == "warning"

    latest_status = client.get("/api/v1/status/latest").json()
    assert latest_status["last_event_type"] == "impact_detected"
    assert latest_status["body_part"] == "right_arm"
    assert latest_status["last_impact_at"] == "2026-08-27T15:02:00+09:00"
