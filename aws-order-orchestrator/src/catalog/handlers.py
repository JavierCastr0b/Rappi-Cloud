import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3
from boto3.dynamodb.conditions import Key

from src.common.http import body_from, response, tenant_from


table = boto3.resource("dynamodb").Table(os.environ["CATALOG_TABLE"])


def create_product(event, _context):
    try:
        payload = body_from(event)
        tenant_id = tenant_from(event, payload)
        if not payload.get("name") or payload.get("price") is None:
            return response(400, {"error": "name y price son obligatorios"})
        product = {
            "tenant_id": tenant_id,
            "product_id": payload.get("product_id") or str(uuid.uuid4()),
            "name": payload["name"],
            "description": payload.get("description", ""),
            "price": Decimal(str(payload["price"])),
            "image_url": payload.get("image_url", ""),
            "category": payload.get("category", "General"),
            "available": bool(payload.get("available", True)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=product)
        return response(201, product)
    except (ValueError, InvalidOperation) as exc:
        return response(400, {"error": str(exc)})


def list_catalog(event, _context):
    try:
        tenant_id = tenant_from(event)
        result = table.query(KeyConditionExpression=Key("tenant_id").eq(tenant_id))
        items = sorted(result.get("Items", []), key=lambda item: (item["category"], item["name"]))
        return response(200, {"items": items, "count": len(items)})
    except ValueError as exc:
        return response(400, {"error": str(exc)})
