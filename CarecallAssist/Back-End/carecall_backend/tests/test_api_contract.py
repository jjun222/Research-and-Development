from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import main


client = TestClient(main.app)

INITIAL_LATEST_STATUS = deepcopy(main.latest_status)
INITIAL_EVENTS = deepcopy(main.events)

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
def reset_in_memory_state():
    main.latest_status.clear()
    main.latest_status.update(deepcopy(INITIAL_LATEST_STATUS))
    main.events.clear()
    main.events.extend(deepcopy(INITIAL_EVENTS))
    main.fcm_tokens.clear()

    yield

    main.latest_status.clear()
    main.latest_status.update(deepcopy(INITIAL_LATEST_STATUS))
    main.events.clear()
    main.events.extend(deepcopy(INITIAL_EVENTS))
    main.fcm_tokens.clear()


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
    assert len(main.fcm_tokens) == 1
    assert main.fcm_tokens[0]["fcm_token"] == "test-fcm-token"


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
    assert captured["latest_status"] is main.latest_status
    assert captured["events"] is main.events


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
