"""
Popula o banco com as categorias iniciais de serviço.

Rode uma vez, na primeira instalação:

    python -m app.seed

Pode rodar de novo sem medo — ele não duplica categorias que já existem.
Edite a lista CATEGORIAS_PADRAO abaixo para ajustar ao que faz sentido
na sua cidade.
"""
from app.database import SessionLocal, Base, engine
from app import models

CATEGORIAS_PADRAO = [
    "Eletricista",
    "Encanador",
    "Vidraceiro",
    "Pedreiro / Reformas",
    "Pintor",
    "Marido de aluguel",
    "Mecânico",
    "Chaveiro",
    "Ar-condicionado / Refrigeração",
    "Jardinagem",
    "Diarista / Faxina",
    "Dedetização",
    "Montador de móveis",
    "Informática & Tecnologia",
    "Manicure / Cabeleireiro",
    "Fotógrafo",
    "Frete / Mudança",
    "Gesseiro",
    "Serralheiro",
    "Piscineiro",
    "Médico",
]


def rodar_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existentes = {c.nome for c in db.query(models.Category).all()}
        novas = [models.Category(nome=nome) for nome in CATEGORIAS_PADRAO if nome not in existentes]
        if novas:
            db.add_all(novas)
            db.commit()
            print(f"{len(novas)} categorias novas adicionadas.")
        else:
            print("Categorias já estavam todas cadastradas.")
    finally:
        db.close()


if __name__ == "__main__":
    rodar_seed()
