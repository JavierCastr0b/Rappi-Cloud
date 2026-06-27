import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from src.common.events import publish
from src.common.http import body_from, response, tenant_from


table = boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])
s3 = boto3.client("s3")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path_id(event):
    return (event.get("pathParameters") or {}).get("order_id")


def create_order(event, _context):
    try:
        payload = body_from(event)
        tenant_id = tenant_from(event, payload)
        items = payload.get("items") or []
        if not payload.get("customer") or not items or not payload.get("delivery_address"):
            return response(400, {"error": "customer, items y delivery_address son obligatorios"})

        order_id = payload.get("order_id") or str(uuid.uuid4())
        timestamp = _now()
        order = {
            "tenant_id": tenant_id,
            "order_id": order_id,
            "customer": payload["customer"],
            "items": items,
            "delivery_address": payload["delivery_address"],
            "origin": payload.get("origin", "WEB").upper(),
            "status": "CREATED",
            "created_at": timestamp,
            "updated_at": timestamp,
            "history": [],
        }
        table.put_item(
            Item=order,
            ConditionExpression="attribute_not_exists(order_id)",
        )
        publish(
            "OrderCreated",
            {
                "tenant_id": tenant_id,
                "order_id": order_id,
                "origin": order["origin"],
            },
        )
        return response(201, order)
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(409, {"error": "El pedido ya existe"})


def get_order(event, _context):
    try:
        tenant_id = tenant_from(event)
        result = table.get_item(Key={"tenant_id": tenant_id, "order_id": _path_id(event)})
        if "Item" not in result:
            return response(404, {"error": "Pedido no encontrado"})
        return response(200, result["Item"])
    except ValueError as exc:
        return response(400, {"error": str(exc)})


def list_orders(event, _context):
    try:
        tenant_id = tenant_from(event)
        result = table.query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
            ScanIndexForward=False,
        )
        items = sorted(result.get("Items", []), key=lambda item: item["created_at"], reverse=True)
        return response(200, {"items": items, "count": len(items)})
    except ValueError as exc:
        return response(400, {"error": str(exc)})


def create_evidence_upload(event, _context):
    try:
        tenant_id = tenant_from(event)
        order_id = _path_id(event)
        if "Item" not in table.get_item(Key={"tenant_id": tenant_id, "order_id": order_id}):
            return response(404, {"error": "Pedido no encontrado"})
        payload = body_from(event)
        content_type = payload.get("content_type", "image/jpeg")
        extension = payload.get("extension", "jpg").replace(".", "")
        key = f"{tenant_id}/{order_id}/{uuid.uuid4()}.{extension}"
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": os.environ["EVIDENCE_BUCKET"],
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=900,
        )
        return response(200, {"upload_url": upload_url, "object_key": key, "expires_in": 900})
    except ValueError as exc:
        return response(400, {"error": str(exc)})
