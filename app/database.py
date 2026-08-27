"""
Configuração da conexão com o banco de dados.

Por padrão usa SQLite (arquivo local, zero configuração) para você
rodar e testar na sua máquina sem instalar nada além do Python.

Quando for para produção (hospedar o site de verdade), basta trocar
a variável de ambiente DATABASE_URL por uma string de conexão do
PostgreSQL, por exemplo:

    DATABASE_URL=postgresql://usuario:senha@host:5432/nome_do_banco

O resto do código (models, rotas, etc.) não muda nada.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Carrega as variáveis do arquivo .env (SECRET_KEY, ADMIN_TELEFONE, DATABASE_URL...)
# Precisa acontecer aqui porque este módulo é o primeiro a ser importado
# tanto por app.main quanto por app.seed.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./servico_cidade.db")

# Provedores de Postgres (Render, Neon, Heroku...) costumam entregar a URL
# como "postgres://" ou "postgresql://", que por padrão o SQLAlchemy tenta
# abrir com o driver psycopg2. Usamos o psycopg (v3) no lugar, então
# reescrevemos o esquema da URL pra apontar pro driver certo.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# connect_args só é necessário para SQLite
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependência do FastAPI: abre uma sessão de banco por requisição e fecha no final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
