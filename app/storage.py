"""
Armazenamento das fotos enviadas pelos profissionais.

Em produção, hospedagens gratuitas geralmente apagam o disco local a cada
reinício/deploy — então guardar fotos em `app/static/fotos` funciona bem
pra testar na sua máquina, mas some quando o app é hospedado de graça em
serviços como Render.

Se as variáveis R2_* estiverem preenchidas no .env, as fotos vão para um
bucket do Cloudflare R2 (compatível com S3, tem plano gratuito generoso)
e sobrevivem a qualquer reinício. Sem essas variáveis, cai automaticamente
para o disco local — bom pra desenvolvimento, sem precisar configurar nada.

Como configurar o R2 (gratuito):
1. Crie uma conta em https://dash.cloudflare.com e vá em "R2 Object Storage"
2. Crie um bucket (ex: "servico-cidade-fotos")
3. Nas configurações do bucket, em "Public access", ative o "R2.dev subdomain"
   pra conseguir uma URL pública tipo https://pub-xxxxxxxx.r2.dev
4. Em "Manage R2 API Tokens", crie um token com permissão de leitura E
   escrita, restrito a esse bucket — isso gera o Access Key ID e o Secret
5. O Account ID aparece na barra lateral do dashboard da Cloudflare
6. Preencha no .env: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
   R2_BUCKET_NAME e R2_PUBLIC_URL (a URL pub-xxxx.r2.dev, sem barra no final)
"""
import os

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR_LOCAL = os.path.join(BASE_DIR, "static", "fotos")
os.makedirs(UPLOAD_DIR_LOCAL, exist_ok=True)

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "").rstrip("/")

r2_habilitado = bool(
    R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME and R2_PUBLIC_URL
)

_cliente_r2 = None
if r2_habilitado:
    import boto3
    from botocore.client import Config

    _cliente_r2 = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def salvar_foto(conteudo: bytes, nome_arquivo: str, content_type: str) -> str:
    """Salva a foto (no R2 ou em disco) e retorna a URL pra usar em <img src>."""
    if r2_habilitado:
        _cliente_r2.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=nome_arquivo,
            Body=conteudo,
            ContentType=content_type,
        )
        return f"{R2_PUBLIC_URL}/{nome_arquivo}"

    caminho = os.path.join(UPLOAD_DIR_LOCAL, nome_arquivo)
    with open(caminho, "wb") as f:
        f.write(conteudo)
    return f"/static/fotos/{nome_arquivo}"
