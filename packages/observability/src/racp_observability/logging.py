import json
import logging
from datetime import UTC, datetime
from typing import Any


def log_event(
    event: str,
    *,
    trace_id: str = "",
    request_id: str = "",
    device_id: str = "",
    operation: str = "",
    **safe_fields: Any,
) -> None:
    logging.getLogger("racp").info(
        json.dumps(
            {
                "event": event,
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_id": trace_id,
                "request_id": request_id,
                "device_id": device_id,
                "operation": operation,
                **safe_fields,
            },
            ensure_ascii=False,
        )
    )
