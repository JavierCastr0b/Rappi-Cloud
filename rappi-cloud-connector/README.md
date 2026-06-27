# Rappi Cloud Connector

API Flask para Google Cloud Run. Simula el origen Rappi, llama al backend AWS con un token obtenido desde `/auth/login` y recibe los callbacks de estado.

## Variables

| Variable | Uso |
|---|---|
| `AWS_ORDERS_URL` | Ruta completa `.../orders` |
| `AWS_LOGIN_URL` | Ruta completa `.../auth/login` |
| `AWS_CLIENT_TENANT` | Tenant de la cuenta registrada |
| `AWS_CLIENT_EMAIL` | Email de la cuenta registrada |
| `AWS_CLIENT_PASSWORD` | Contraseña de la cuenta registrada |
| `PARTNER_SHARED_SECRET` | Secreto del callback AWS → GCP |

Firestore debe estar habilitado en modo nativo en el proyecto GCP.

