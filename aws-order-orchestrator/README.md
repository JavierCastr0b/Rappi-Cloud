# AWS Order Orchestrator

Backend serverless principal. Las rutas `/auth/register` y `/auth/login` son públicas; las demás requieren `Authorization: Bearer <token>`.

## Tablas DynamoDB

- `orders`: pedidos por `tenant_id` y `order_id`.
- `catalog`: productos por `tenant_id` y `product_id`.
- `workflow-tasks`: callback tokens y atención de pasos humanos.
- `identity`: usuarios (`USER#tenant#email`) y tokens hasheados (`TOKEN#sha256`) con TTL.

## Estados

`RECEIVED → COOKING → PACKING → ON_THE_WAY → DELIVERED`

Cada paso guarda hora de inicio, fin y responsable. Para avanzar:

```http
POST /orders/{order_id}/steps/{step}/complete
Authorization: Bearer <token>
Content-Type: application/json

{"handled_by":"Nombre del trabajador"}
```

