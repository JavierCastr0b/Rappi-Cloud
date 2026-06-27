import base64
import hashlib
import hmac
import os
import secrets
import time

import boto3

from src.common.http import body_from, response


table = boto3.resource("dynamodb").Table(os.environ["IDENTITY_TABLE"])
ITERATIONS = 210_000
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "86400"))


def _normalize_email(value):
    return str(value or "").strip().lower()


def _user_key(tenant_id, email):
    return f"USER#{tenant_id}#{_normalize_email(email)}"


def _token_key(raw_token):
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    return f"TOKEN#{digest}"


def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def _password_matches(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(calculated, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def _bearer_token(event):
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    authorization = headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def register(event, _context):
    try:
        payload = body_from(event)
        tenant_id = str(payload.get("tenant_id") or "").strip()
        email = _normalize_email(payload.get("email"))
        password = str(payload.get("password") or "")
        if not tenant_id or not email or not payload.get("name"):
            return response(400, {"error": "tenant_id, name, email y password son obligatorios"})
        if len(password) < 8:
            return response(400, {"error": "La contraseña debe tener al menos 8 caracteres"})

        user = {
            "record_id": _user_key(tenant_id, email),
            "record_type": "USER",
            "tenant_id": tenant_id,
            "email": email,
            "name": str(payload["name"]).strip(),
            "role": "WORKER",
            "password_hash": _hash_password(password),
            "created_at": int(time.time()),
            "active": True,
        }
        table.put_item(Item=user, ConditionExpression="attribute_not_exists(record_id)")
        return response(
            201,
            {
                "tenant_id": tenant_id,
                "email": email,
                "name": user["name"],
                "role": user["role"],
            },
        )
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(409, {"error": "El usuario ya está registrado"})


def login(event, _context):
    try:
        payload = body_from(event)
        tenant_id = str(payload.get("tenant_id") or "").strip()
        email = _normalize_email(payload.get("email"))
        password = str(payload.get("password") or "")
        user = table.get_item(Key={"record_id": _user_key(tenant_id, email)}).get("Item")
        if not user or not user.get("active") or not _password_matches(password, user["password_hash"]):
            return response(401, {"error": "Credenciales inválidas"})

        raw_token = secrets.token_urlsafe(48)
        now = int(time.time())
        expires_at = now + TOKEN_TTL_SECONDS
        table.put_item(
            Item={
                "record_id": _token_key(raw_token),
                "record_type": "TOKEN",
                "tenant_id": tenant_id,
                "email": email,
                "role": user["role"],
                "created_at": now,
                "expires_at": expires_at,
            }
        )
        return response(
            200,
            {
                "access_token": raw_token,
                "token_type": "Bearer",
                "expires_in": TOKEN_TTL_SECONDS,
                "user": {
                    "tenant_id": tenant_id,
                    "email": email,
                    "name": user["name"],
                    "role": user["role"],
                },
            },
        )
    except ValueError as exc:
        return response(400, {"error": str(exc)})


def logout(event, _context):
    raw_token = _bearer_token(event)
    if raw_token:
        table.delete_item(Key={"record_id": _token_key(raw_token)})
    return response(204, {})


def authorize(event, _context):
    raw_token = _bearer_token(event)
    token = (
        table.get_item(Key={"record_id": _token_key(raw_token)}).get("Item")
        if raw_token
        else None
    )
    now = int(time.time())
    if not token or int(token.get("expires_at", 0)) <= now:
        if token:
            table.delete_item(Key={"record_id": token["record_id"]})
        raise Exception("Unauthorized")

    method_arn = event["methodArn"]
    return {
        "principalId": token["email"],
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{"Action": "execute-api:Invoke", "Effect": "Allow", "Resource": method_arn}],
        },
        "context": {
            "tenant_id": token["tenant_id"],
            "email": token["email"],
            "role": token["role"],
        },
    }
