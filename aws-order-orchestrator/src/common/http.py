import json
from decimal import Decimal


class DecimalEncoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, Decimal):
            return int(value) if value % 1 == 0 else float(value)
        return super().default(value)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-Tenant-Id",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, cls=DecimalEncoder, ensure_ascii=False),
    }


def body_from(event):
    raw_body = event.get("body") or "{}"
    if isinstance(raw_body, dict):
        return raw_body
    try:
        return json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("El cuerpo debe ser JSON válido") from exc


def tenant_from(event, payload=None):
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    query = event.get("queryStringParameters") or {}
    supplied_tenant = (
        headers.get("x-tenant-id")
        or (payload or {}).get("tenant_id")
        or query.get("tenant_id")
    )
    authorizer = ((event.get("requestContext") or {}).get("authorizer") or {})
    authorized_tenant = authorizer.get("tenant_id")
    if authorized_tenant and supplied_tenant and authorized_tenant != supplied_tenant:
        raise ValueError("El tenant no coincide con el token autenticado")
    tenant_id = authorized_tenant or supplied_tenant
    if not tenant_id:
        raise ValueError("No se pudo identificar el tenant")
    return tenant_id
