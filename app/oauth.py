"""
Configuração do login com Google (OAuth2 / OpenID Connect).

Se GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET não estiverem preenchidos no
.env, `google_oauth_habilitado` fica False e o app simplesmente esconde o
botão "Entrar com Google" — nada quebra por falta de credencial.

Para conseguir essas credenciais: crie um "OAuth Client ID" (tipo "Web
application") em https://console.cloud.google.com/apis/credentials e
cadastre como URI de redirecionamento autorizada:

    http://127.0.0.1:8000/auth/google/callback

(troque pelo domínio real quando for para produção).
"""
import os

from authlib.integrations.starlette_client import OAuth

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

google_oauth_habilitado = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

oauth = OAuth()
if google_oauth_habilitado:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
