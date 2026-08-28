"""
Popula o banco com as categorias iniciais de serviço.

Rode uma vez, na primeira instalação:

    python -m app.seed

Pode rodar de novo sem medo — ele não duplica categorias que já existem,
e sempre realinha o "grupo" de cada categoria com o que está definido
aqui embaixo (então também serve pra mover uma categoria de grupo).
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
    ("Marceneiro", "Casa e Reformas"),
    ("Chaveiro", "Casa e Reformas"),
    ("Marido de aluguel", "Casa e Reformas"),
    ("Telhadista / Impermeabilização", "Casa e Reformas"),

    ("Diarista / Faxina", "Limpeza e Manutenção"),
    ("Dedetização", "Limpeza e Manutenção"),
    ("Montador de móveis", "Limpeza e Manutenção"),
    ("Piscineiro", "Limpeza e Manutenção"),
    ("Jardinagem", "Limpeza e Manutenção"),
    ("Ar-condicionado / Refrigeração", "Limpeza e Manutenção"),
    ("Tapeceiro / Estofados", "Limpeza e Manutenção"),
    ("Limpeza pós-obra", "Limpeza e Manutenção"),

    ("Mecânico/Oficina", "Carros e Motos"),
    ("Auto elétrico", "Carros e Motos"),
    ("Funilaria e Pintura", "Carros e Motos"),
    ("Guincho", "Carros e Motos"),
    ("Lavagem de carros", "Carros e Motos"),

    ("Informática & Tecnologia", "Tecnologia"),
    ("Conserto de celular", "Tecnologia"),
    ("Instalação de câmeras / CFTV", "Tecnologia"),
    ("Assistência de eletrônicos", "Tecnologia"),

    ("Médico", "Saúde e Família"),
    ("Babá", "Saúde e Família"),
    ("Cuidador de idosos", "Saúde e Família"),
    ("Personal trainer", "Saúde e Família"),
    ("Fisioterapeuta", "Saúde e Família"),
    ("Psicólogo", "Saúde e Família"),
    ("Nutricionista", "Saúde e Família"),

    ("Manicure / Cabeleireiro", "Beleza e Eventos"),
    ("Maquiador(a)", "Beleza e Eventos"),
    ("Fotógrafo", "Beleza e Eventos"),
    ("Confeiteiro(a) / Doceiro(a)", "Beleza e Eventos"),
    ("Buffet / Garçom", "Beleza e Eventos"),
    ("DJ / Som", "Beleza e Eventos"),
    ("Decoração de festas", "Beleza e Eventos"),

    ("Aulas particulares", "Aulas e Consultoria"),
    ("Professor de idiomas", "Aulas e Consultoria"),
    ("Contador", "Aulas e Consultoria"),
    ("Advogado", "Aulas e Consultoria"),

    ("Frete / Mudança", "Transporte"),
    ("Motoboy / Entregador", "Transporte"),
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
            if categoria and categoria.grupo != grupo:
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
