"""
Popula o banco com as categorias iniciais de serviço.

Rode uma vez, na primeira instalação:

    python -m app.seed

Pode rodar de novo sem medo — ele não duplica categorias que já existem,
e também atualiza o "grupo" de categorias antigas que ainda não tinham
um (por exemplo, se você adicionou uma categoria nova direto no banco).
Edite a lista CATEGORIAS_PADRAO abaixo para ajustar ao que faz sentido
na sua cidade — cada item é (nome, grupo). O grupo é usado pra organizar
o menu de categorias em blocos maiores (estilo "mega menu").
"""
from app.database import SessionLocal, Base, engine
from app import models

CATEGORIAS_PADRAO = [
    ("Eletricista", "Casa e Reformas"),
    ("Encanador", "Casa e Reformas"),
    ("Vidraceiro", "Casa e Reformas"),
    ("Pedreiro / Reformas", "Casa e Reformas"),
    ("Pintor", "Casa e Reformas"),
    ("Gesseiro", "Casa e Reformas"),
    ("Serralheiro", "Casa e Reformas"),
    ("Marido de aluguel", "Casa e Reformas"),

    ("Diarista / Faxina", "Limpeza e Manutenção"),
    ("Dedetização", "Limpeza e Manutenção"),
    ("Montador de móveis", "Limpeza e Manutenção"),
    ("Piscineiro", "Limpeza e Manutenção"),
    ("Jardinagem", "Limpeza e Manutenção"),
    ("Ar-condicionado / Refrigeração", "Limpeza e Manutenção"),

    ("Mecânico/Oficina", "Carros e Tecnologia"),
    ("Chaveiro", "Carros e Tecnologia"),
    ("Informática & Tecnologia", "Carros e Tecnologia"),

    ("Médico", "Saúde e Família"),
    ("Babá", "Saúde e Família"),

    ("Manicure / Cabeleireiro", "Beleza e Eventos"),
    ("Fotógrafo", "Beleza e Eventos"),
    ("Confeiteiro(a) / Doceiro(a)", "Beleza e Eventos"),

    ("Frete / Mudança", "Transporte"),
]


def rodar_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existentes = {c.nome: c for c in db.query(models.Category).all()}

        novas = [
            models.Category(nome=nome, grupo=grupo)
            for nome, grupo in CATEGORIAS_PADRAO
            if nome not in existentes
        ]
        if novas:
            db.add_all(novas)

        atualizadas = 0
        for nome, grupo in CATEGORIAS_PADRAO:
            categoria = existentes.get(nome)
            if categoria and not categoria.grupo:
                categoria.grupo = grupo
                atualizadas += 1

        db.commit()

        if novas:
            print(f"{len(novas)} categorias novas adicionadas.")
        if atualizadas:
            print(f"{atualizadas} categorias existentes ganharam um grupo.")
        if not novas and not atualizadas:
            print("Categorias já estavam todas cadastradas e organizadas.")
    finally:
        db.close()


if __name__ == "__main__":
    rodar_seed()
