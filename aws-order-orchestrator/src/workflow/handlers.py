import json
import os
from collections import Counter
from datetime import datetime, timezone
from urllib import request

import boto3
from boto3.dynamodb.conditions import Key

from src.common.events import publish
from src.common.http import body_from, response, tenant_from


dynamodb = boto3.resource("dynamodb")
orders = dynamodb.Table(os.environ["ORDERS_TABLE"])
tasks = dynamodb.Table(os.environ["TASKS_TABLE"])
stepfunctions = boto3.client("stepfunctions")

STEPS = ["RECEIVED", "COOKING", "PACKING", "ON_THE_WAY", "DELIVERED"]


def _now():
    return datetime.now(timezone.utc).isoformat()


def start_workflow(event, _context):
    detail = event.get("detail") or event
    execution = stepfunctions.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"order-{detail['order_id']}",
        input=json.dumps(
            {
                "tenant_id": detail["tenant_id"],
                "order_id": detail["order_id"],
                "origin": detail.get("origin", "WEB"),
            }
        ),
    )
    return {"execution_arn": execution["executionArn"]}


def register_human_task(event, _context):
    timestamp = _now()
    item = {
        "order_id": event["order_id"],
        "step": event["step"],
        "tenant_id": event["tenant_id"],
        "origin": event.get("origin", "WEB"),
        "task_token": event["taskToken"],
        "status": "PENDING",
        "started_at": timestamp,
    }
    tasks.put_item(Item=item)
    orders.update_item(
        Key={"tenant_id": event["tenant_id"], "order_id": event["order_id"]},
        UpdateExpression="SET #status = :status, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": event["step"], ":now": timestamp},
    )
    publish(
        "OrderStatusChanged",
        {
            "tenant_id": event["tenant_id"],
            "order_id": event["order_id"],
            "origin": event.get("origin", "WEB"),
            "status": event["step"],
        },
    )
    return {"registered": True}


def complete_step(event, _context):
    try:
        payload = body_from(event)
        tenant_id = tenant_from(event, payload)
        path = event.get("pathParameters") or {}
        order_id, step = path.get("order_id"), path.get("step", "").upper()
        if step not in STEPS:
            return response(400, {"error": "Paso no válido", "allowed_steps": STEPS})
        if not payload.get("handled_by"):
            return response(400, {"error": "handled_by es obligatorio"})

        result = tasks.get_item(Key={"order_id": order_id, "step": step})
        task = result.get("Item")
        if not task or task["tenant_id"] != tenant_id:
            return response(404, {"error": "No existe una tarea pendiente para ese paso"})
        if task["status"] == "COMPLETED":
            return response(409, {"error": "El paso ya fue completado"})

        completed_at = _now()
        stepfunctions.send_task_success(
            taskToken=task["task_token"],
            output=json.dumps(
                {"tenant_id": tenant_id, "order_id": order_id, "origin": task["origin"]}
            ),
        )
        tasks.update_item(
            Key={"order_id": order_id, "step": step},
            UpdateExpression=(
                "SET #status = :completed, completed_at = :completed_at, "
                "handled_by = :handled_by"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":completed": "COMPLETED",
                ":completed_at": completed_at,
                ":handled_by": payload["handled_by"],
            },
        )
        orders.update_item(
            Key={"tenant_id": tenant_id, "order_id": order_id},
            UpdateExpression=(
                "SET updated_at = :now, "
                "history = list_append(if_not_exists(history, :empty), :entry)"
            ),
            ExpressionAttributeValues={
                ":now": completed_at,
                ":empty": [],
                ":entry": [
                    {
                        "step": step,
                        "started_at": task["started_at"],
                        "completed_at": completed_at,
                        "handled_by": payload["handled_by"],
                    }
                ],
            },
        )
        return response(200, {"order_id": order_id, "completed_step": step})
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except stepfunctions.exceptions.TaskTimedOut:
        return response(409, {"error": "El token de la tarea expiró"})


def dashboard(event, _context):
    try:
        tenant_id = tenant_from(event)
        order_items = orders.query(KeyConditionExpression=Key("tenant_id").eq(tenant_id)).get(
            "Items", []
        )
        counts = Counter(item["status"] for item in order_items)
        completed_durations = []
        for item in order_items:
            for entry in item.get("history", []):
                if entry.get("started_at") and entry.get("completed_at"):
                    started = datetime.fromisoformat(entry["started_at"])
                    completed = datetime.fromisoformat(entry["completed_at"])
                    completed_durations.append((completed - started).total_seconds())
        average = round(sum(completed_durations) / len(completed_durations), 1) if completed_durations else 0
        return response(
            200,
            {
                "total_orders": len(order_items),
                "orders_by_status": dict(counts),
                "average_step_duration_seconds": average,
            },
        )
    except ValueError as exc:
        return response(400, {"error": str(exc)})


def notify_partner(event, _context):
    detail = event.get("detail") or {}
    if detail.get("origin") != "RAPPI" or not os.environ.get("PARTNER_CALLBACK_URL"):
        return {"skipped": True}
    payload = json.dumps(detail).encode()
    req = request.Request(
        os.environ["PARTNER_CALLBACK_URL"],
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Partner-Secret": os.environ.get("PARTNER_SHARED_SECRET", ""),
        },
        method="POST",
    )
    with request.urlopen(req, timeout=10) as result:
        return {"status_code": result.status}
