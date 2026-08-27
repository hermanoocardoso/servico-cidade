"""
Autenticação simples baseada em sessão (cookie assinado), sem JWT e
sem complicação. O FastAPI/Starlette já cuida de assinar o cookie
para o usuário não conseguir forjar/editar ele mesmo.

Guardamos apenas o `user_id` na sessão. A cada requisição, buscamos
o usuário no banco a partir desse id.
"""
import os

import bcrypt
from fastapi import Request, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.database import get_db
from app import models


def gerar_hash_senha(senha: str) -> str:
    hash_bytes = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt())
    return hash_bytes.decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("utf-8"))


# ---------------------------------------------------------------------------
# Token de confirmação de e-mail (usado no link enviado no cadastro).
# Mesma SECRET_KEY do cookie de sessão, mas com "salt" diferente para que um
# token de confirmação não possa ser reaproveitado como cookie de sessão.
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-antes-de-colocar-no-ar")
VALIDADE_TOKEN_CONFIRMACAO = 60 * 60 * 24 * 3  # 3 dias
_serializer_confirmacao = URLSafeTimedSerializer(SECRET_KEY, salt="confirmacao-email")


def gerar_token_confirmacao(email: str) -> str:
    return _serializer_confirmacao.dumps(email)


def verificar_token_confirmacao(token: str) -> str | None:
    """Retorna o e-mail codificado no token, ou None se for inválido/expirado."""
    try:
        return _serializer_confirmacao.loads(token, max_age=VALIDADE_TOKEN_CONFIRMACAO)
    except (BadSignature, SignatureExpired):
        return None


def usuario_logado(request: Request, db: Session = Depends(get_db)):
    """Retorna o usuário logado (User) ou None. Use como Depends() nas rotas."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(models.User).filter(models.User.id == user_id).first()
