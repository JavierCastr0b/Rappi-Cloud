# Sistema serverless de pedidos con integración Rappi

El proyecto quedó ordenado en solo dos carpetas y sin adaptación visual o comercial a ningún restaurante:

```text
aws-order-orchestrator/   Backend principal en AWS
rappi-cloud-connector/    API externa que simula Rappi en GCP
```

## 1. Backend principal en AWS

`aws-order-orchestrator` usa Serverless Framework e incluye:

- API Gateway y funciones Lambda.
- EventBridge para `OrderCreated` y `OrderStatusChanged`.
- Step Functions con `Wait for Callback with Task Token`.
- DynamoDB para pedidos, catálogo, tareas humanas, usuarios y tokens.
- S3 para evidencias mediante URL prefirmada.
- Registro, login, logout y Lambda Authorizer sin Cognito.

Las contraseñas se almacenan con PBKDF2-HMAC-SHA256, sal aleatoria y 210 000 iteraciones. Los tokens son opacos, se devuelve el valor solo al iniciar sesión y DynamoDB guarda únicamente su SHA-256. La tabla tiene TTL para eliminar tokens vencidos.

### Despliegue

```bash
cd aws-order-orchestrator
npm install
npx serverless deploy --stage dev --region us-east-1
```

### Crear una cuenta

```bash
API_URL="URL_DE_API_GATEWAY"

curl -X POST "$API_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-demo",
    "name": "Usuario de integración",
    "email": "rappi@example.com",
    "password": "ClaveSegura123"
  }'
```

### Iniciar sesión

```bash
curl -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-demo",
    "email": "rappi@example.com",
    "password": "ClaveSegura123"
  }'
```

Usa el `access_token` recibido en las rutas protegidas:

```bash
curl "$API_URL/orders" -H "Authorization: Bearer ACCESS_TOKEN"
```

## 2. API externa tipo Rappi

`rappi-cloud-connector` se despliega en Google Cloud Run y persiste su réplica en Firestore. Expone:

- `POST /api/v1/orders`: recibe un pedido externo, inicia sesión y llama al backend AWS.
- `POST /api/v1/order-status`: recibe desde AWS cada actualización de estado.
- `GET /api/v1/orders/{order_id}`: consulta el estado sincronizado.
- `GET /health`: health check.

### Despliegue en GCP

Primero registra en AWS la cuenta que usará el conector. Luego:

```bash
cd rappi-cloud-connector
gcloud run deploy rappi-cloud-connector \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "AWS_ORDERS_URL=URL_AWS/orders,AWS_LOGIN_URL=URL_AWS/auth/login,AWS_CLIENT_TENANT=tenant-demo,AWS_CLIENT_EMAIL=rappi@example.com" \
  --set-secrets "AWS_CLIENT_PASSWORD=rappi-password:latest,PARTNER_SHARED_SECRET=partner-secret:latest"
```

Los secretos `rappi-password` y `partner-secret` deben crearse antes en Google Secret Manager.

Finalmente configura el callback de Cloud Run en AWS:

```bash
cd aws-order-orchestrator
npx serverless deploy --stage dev \
  --param="partnerCallbackUrl=URL_CLOUD_RUN/api/v1/order-status" \
  --param="partnerSharedSecret=CAMBIAR_SECRETO"
```

### Probar el flujo Rappi

```bash
curl -X POST "URL_CLOUD_RUN/api/v1/orders" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-demo",
    "customer": {"name": "Cliente externo", "phone": "999999999"},
    "delivery_address": "Av. Demo 123",
    "items": [{"product_id": "p-1", "name": "Producto", "price": 20, "quantity": 1}]
  }'
```

El flujo resultante es:

```text
Rappi/GCP → login AWS → API Gateway → Lambda → DynamoDB
                                   → EventBridge → Step Functions
AWS/EventBridge → callback GCP → Firestore
```
# Rappi-Cloud
