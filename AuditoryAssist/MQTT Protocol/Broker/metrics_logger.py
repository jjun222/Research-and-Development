import csv
import os
from datetime import datetime

BASE_DIR = "metrics"
EVENTS_CSV = os.path.join(BASE_DIR, "events_raw.csv")
TRIALS_CSV = os.path.join(BASE_DIR, "trial_results.csv")

EVENTS_HEADER = [
    "timestamp_iso", "run_id", "test_group", "test_id", "scenario_id", "trial_no",
    "phase", "node_id", "topic", "device_id", "seq", "event_name", "priority_class",
    "qos", "t_sent_ms", "t_broker_recv_ms", "t_broker_publish_ms", "t_sink_recv_ms",
    "status", "notes"
]

TRIALS_HEADER = [
    "timestamp_iso", "run_id", "test_group", "test_id", "scenario_id", "trial_no",
    "source_path", "expected_inputs", "received_inputs", "expected_devices",
    "activated_devices", "delivery_ok", "activation_ok", "ack_ok", "priority_ok",
    "all_true_ok", "wrong_activation_count", "message_loss_rate", "duplicate_count",
    "broker_processing_latency_ms", "e2e_latency_ms", "priority_settle_time_ms",
    "trigger_latency_ms", "throughput_msg_s", "notes"
]

def _ensure_csv(path: str, header: list[str]) -> None:
    os.makedirs(BASE_DIR, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(header)

def _append_row(path: str, header: list[str], row_dict: dict) -> None:
    _ensure_csv(path, header)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writerow(row_dict)

def log_event(**kwargs) -> None:
    row = {k: "" for k in EVENTS_HEADER}
    row.update(kwargs)
    row["timestamp_iso"] = datetime.now().isoformat(timespec="seconds")
    _append_row(EVENTS_CSV, EVENTS_HEADER, row)

def log_trial(**kwargs) -> None:
    row = {k: "" for k in TRIALS_HEADER}
    row.update(kwargs)
    row["timestamp_iso"] = datetime.now().isoformat(timespec="seconds")
    _append_row(TRIALS_CSV, TRIALS_HEADER, row)
