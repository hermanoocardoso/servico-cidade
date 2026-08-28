"""
Autenticação simples baseada em sessão (cookie assinado), sem JWT e
sem complicação. O FastAPI/Starlette já cuida de assinar o cookie
para o usuário não conseguir forjar/editar ele mesmo.

Guardamos apenas o `user_id` na sessão. A cada requisição, buscamos
o usuário no banco a partir desse id.
"""
import bcrypt
from fastapi import Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models


def gerar_hash_senha(senha: str) -> str:
    hash_bytes = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt())
    return hash_bytes.decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("utf-8"))


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
