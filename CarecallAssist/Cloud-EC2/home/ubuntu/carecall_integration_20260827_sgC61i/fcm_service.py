import logging
from typing import Any

import firebase_admin
from firebase_admin import messaging


logger = logging.getLogger(__name__)

FCM_MULTICAST_LIMIT = 500

FCM_DATA_FIELDS = (
    "event_id",
    "user_id",
    "event_type",
    "location",
    "device_id",
    "severity",
    "posture",
    "confidence",
    "image_url",
    "stream_url",
    "occurred_at",
    "title",
    "body",
)


def _get_or_initialize_firebase_app():
    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app()


def _stringify_fcm_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def build_event_fcm_data(event: dict[str, Any]) -> dict[str, str]:
    data: dict[str, str] = {}

    for field in FCM_DATA_FIELDS:
        value = event.get(field)

        if value is None:
            continue

        data[field] = _stringify_fcm_value(value)

    return data


def _normalize_tokens(tokens: list[str]) -> list[str]:
    normalized_tokens: list[str] = []
    seen_tokens: set[str] = set()

    for token in tokens:
        normalized_token = token.strip()

        if not normalized_token or normalized_token in seen_tokens:
            continue

        normalized_tokens.append(normalized_token)
        seen_tokens.add(normalized_token)

    return normalized_tokens


def send_event_notification(
    *,
    tokens: list[str],
    event: dict[str, Any],
) -> None:
    normalized_tokens = _normalize_tokens(tokens)

    if not normalized_tokens:
        return

    app = _get_or_initialize_firebase_app()
    title = str(event.get("title") or "돌봄 알림")
    body = str(event.get("body") or "사용자 상태 확인이 필요합니다.")
    data = build_event_fcm_data(event)

    success_count = 0
    failure_count = 0

    for start_index in range(0, len(normalized_tokens), FCM_MULTICAST_LIMIT):
        token_chunk = normalized_tokens[
            start_index : start_index + FCM_MULTICAST_LIMIT
        ]

        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data,
            tokens=token_chunk,
        )

        response = messaging.send_each_for_multicast(message, app=app)
        success_count += response.success_count
        failure_count += response.failure_count

        for token_index, send_response in enumerate(response.responses):
            if send_response.success:
                continue

            logger.warning(
                "FCM token send failed: token_index=%s, error_type=%s",
                start_index + token_index,
                type(send_response.exception).__name__,
            )

    logger.info(
        "FCM event notification completed: event_id=%s, requested=%s, "
        "success=%s, failure=%s",
        event.get("event_id"),
        len(normalized_tokens),
        success_count,
        failure_count,
    )


def send_event_notification_safely(
    *,
    tokens: list[str],
    event: dict[str, Any],
) -> None:
    try:
        send_event_notification(tokens=tokens, event=event)
    except Exception:
        logger.exception(
            "FCM event notification failed without affecting the saved event: "
            "event_id=%s",
            event.get("event_id"),
        )
