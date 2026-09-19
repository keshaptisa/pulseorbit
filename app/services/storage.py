from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from threading import Lock

from app.models import AssessmentResponse

RESULTS_DIR = Path("data/results")
_WRITE_LOCK = Lock()


def save_result(result: AssessmentResponse) -> str:
    payload = result.model_dump(mode="json")
    payload["result_id"] = None
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result_id = sha256(canonical.encode("utf-8")).hexdigest()[:16]
    result.result_id = result_id
    payload["result_id"] = result_id
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    envelope = {
        "result_id": result_id,
        "saved_at": datetime.now(UTC).isoformat(),
        "content_sha256": sha256(canonical.encode("utf-8")).hexdigest(),
        "result": payload,
    }
    path = RESULTS_DIR / f"{result_id}.json"
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with _WRITE_LOCK:
        temporary.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    return result_id


def load_result(result_id: str) -> dict | None:
    if not result_id.isalnum() or len(result_id) != 16:
        return None
    path = RESULTS_DIR / f"{result_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
