import os
import uuid
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request
from google.cloud import firestore


app = Flask(__name__)
database = firestore.Client()
orders = database.collection("partner_orders")

AWS_ORDERS_URL = os.environ.get("AWS_ORDERS_URL", "")
AWS_LOGIN_URL = os.environ.get("AWS_LOGIN_URL", "")
AWS_CLIENT_TENANT = os.environ.get("AWS_CLIENT_TENANT", "")
AWS_CLIENT_EMAIL = os.environ.get("AWS_CLIENT_EMAIL", "")
AWS_CLIENT_PASSWORD = os.environ.get("AWS_CLIENT_PASSWORD", "")
PARTNER_SHARED_SECRET = os.environ.get("PARTNER_SHARED_SECRET", "")
token_cache = {"value": None, "expires_at": 0}


def now():
    return datetime.now(timezone.utc).isoformat()


def authorized():
    return not PARTNER_SHARED_SECRET or request.headers.get("X-Partner-Secret") == PARTNER_SHARED_SECRET


def aws_access_token(force_refresh=False):
    if (
        not force_refresh
        and token_cache["value"]
        and token_cache["expires_at"] > time.time() + 60
    ):
        return token_cache["value"]
    if not all((AWS_LOGIN_URL, AWS_CLIENT_TENANT, AWS_CLIENT_EMAIL, AWS_CLIENT_PASSWORD)):
        raise RuntimeError("Faltan las credenciales del backend AWS")
    result = requests.post(
        AWS_LOGIN_URL,
        json={
            "tenant_id": AWS_CLIENT_TENANT,
            "email": AWS_CLIENT_EMAIL,
            "password": AWS_CLIENT_PASSWORD,
        },
        timeout=15,
    )
    result.raise_for_status()
    authentication = result.json()
    token_cache["value"] = authentication["access_token"]
    token_cache["expires_at"] = time.time() + int(authentication["expires_in"])
    return token_cache["value"]


def send_to_aws(payload):
    for attempt in range(2):
        token = aws_access_token(force_refresh=attempt == 1)
        result = requests.post(
            AWS_ORDERS_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Tenant-Id": payload["tenant_id"],
            },
            timeout=15,
        )
        if result.status_code != 401:
            result.raise_for_status()
            return result
    result.raise_for_status()


@app.get("/health")
def health():
    return jsonify({"status": "healthy", "service": "delivery-partner-api"})


@app.post("/api/v1/orders")
def create_partner_order():
    payload = request.get_json(silent=True) or {}
    required = ("tenant_id", "customer", "items", "delivery_address")
    if any(not payload.get(field) for field in required):
        return jsonify({"error": f"Campos obligatorios: {', '.join(required)}"}), 400
    if not AWS_ORDERS_URL:
        return jsonify({"error": "AWS_ORDERS_URL no está configurada"}), 503

    order_id = payload.get("order_id") or str(uuid.uuid4())
    tenant_id = payload["tenant_id"]
    partner_order = {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "customer": payload["customer"],
        "items": payload["items"],
        "delivery_address": payload["delivery_address"],
        "origin": "RAPPI",
        "status": "CREATED",
        "created_at": now(),
        "updated_at": now(),
    }
    orders.document(order_id).set(partner_order)
    try:
        send_to_aws(partner_order)
    except (requests.RequestException, RuntimeError) as exc:
        app.logger.exception("AWS order platform unavailable")
        return jsonify({"error": "No se pudo gatillar el flujo AWS", "detail": str(exc)}), 502
    return jsonify(partner_order), 201


@app.post("/api/v1/order-status")
def update_partner_status():
    if not authorized():
        return jsonify({"error": "Credencial de integración inválida"}), 401
    payload = request.get_json(silent=True) or {}
    if not payload.get("order_id") or not payload.get("status"):
        return jsonify({"error": "order_id y status son obligatorios"}), 400

    document = orders.document(payload["order_id"])
    document.set(
        {
            "order_id": payload["order_id"],
            "tenant_id": payload.get("tenant_id"),
            "status": payload["status"],
            "updated_at": now(),
        },
        merge=True,
    )
    return jsonify(
        {
            "order_id": payload["order_id"],
            "status": payload["status"],
            "synchronized": True,
        }
    )


@app.get("/api/v1/orders/<order_id>")
def get_partner_order(order_id):
    snapshot = orders.document(order_id).get()
    if not snapshot.exists:
        return jsonify({"error": "Pedido no encontrado"}), 404
    return jsonify(snapshot.to_dict())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
