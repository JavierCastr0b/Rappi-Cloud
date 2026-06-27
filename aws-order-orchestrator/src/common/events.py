import json
import os
from datetime import datetime, timezone

import boto3


eventbridge = boto3.client("events")


def publish(detail_type, detail):
    detail["event_time"] = datetime.now(timezone.utc).isoformat()
    result = eventbridge.put_events(
        Entries=[
            {
                "EventBusName": os.environ["EVENT_BUS_NAME"],
                "Source": "com.orderplatform.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
            }
        ]
    )
    if result.get("FailedEntryCount"):
        raise RuntimeError("EventBridge no pudo publicar el evento")
    return result
