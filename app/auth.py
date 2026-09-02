"""
Autenticação simples baseada em sessão (cookie assinado), sem JWT e
sem complicação. O FastAPI/Starlette já cuida de assinar o cookie
para o usuário não conseguir forjar/editar ele mesmo.

Guardamos apenas o `user_id` na sessão. A cada requisição, buscamos
o usuário no banco a partir desse id.
"""
import bcrypt
from fastapi import Request, Depends
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.database import get_db
from app import models


def gerar_hash_senha(senha: str) -> str:
    hash_bytes = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt())
    return hash_bytes.decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("utf-8"))


def gerar_token(secret_key: str, salt: str, dados: dict) -> str:
    """Token assinado e com validade (link de confirmação de e-mail, link de
    redefinição de senha). Não precisa guardar nada no banco: o próprio
    token carrega os dados e expira sozinho."""
    return URLSafeTimedSerializer(secret_key, salt=salt).dumps(dados)


def ler_token(secret_key: str, salt: str, token: str, max_idade_segundos: int) -> dict | None:
    """Retorna os dados do token se a assinatura bater e ele ainda não tiver
    expirado, senão None (token forjado, de outro salt, ou vencido)."""
    try:
        return URLSafeTimedSerializer(secret_key, salt=salt).loads(token, max_age=max_idade_segundos)
    except (BadSignature, SignatureExpired):
        return None


def usuario_logado(request: Request, db: Session = Depends(get_db)):
    """Retorna o usuário logado (User) ou None. Use como Depends() nas rotas."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    usuario = db.query(models.User).filter(models.User.id == user_id).first()
    if usuario and not usuario.ativo:
        # Conta bloqueada pelo admin depois que a sessão foi criada: derruba
        # o login sem precisar que a pessoa clique em "Sair".
        request.session.clear()
        return None
    return usuario
