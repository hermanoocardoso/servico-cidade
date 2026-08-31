"""
Aplicação principal.

Para rodar localmente:

    uvicorn app.main:app --reload

Depois abra http://127.0.0.1:8000 no navegador.

Veja o LEIA-ME.md na raiz do projeto para o passo a passo completo
de instalação.
"""
import hashlib
import io
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app.database import Base, engine, get_db
from app import models, auth, storage, email_utils
from app.oauth import oauth, google_oauth_habilitado
from app.seed import rodar_seed

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Cria as tabelas automaticamente se ainda não existirem, e mantém as
# categorias padrão (nome + grupo) sempre alinhadas com CATEGORIAS_PADRAO —
# assim um novo deploy já atualiza o catálogo de categorias sozinho, sem
# precisar rodar "python -m app.seed" manualmente em produção.
Base.metadata.create_all(bind=engine)


def _garantir_colunas_novas():
    """create_all() só cria tabelas que ainda não existem -- não adiciona
    coluna nova numa tabela que já existe em produção. Como não usamos uma
    ferramenta de migração (Alembic), essa função confere colunas que os
    modelos esperam e adiciona na mão as que estiverem faltando, sem apagar
    nada. Precisa ficar em dia manualmente sempre que um Column novo for
    adicionado a um model existente."""
    from sqlalchemy import inspect, text

    inspetor = inspect(engine)
    if "professional_profiles" not in inspetor.get_table_names():
        return  # tabela acabou de ser criada pelo create_all, já vem completa
    colunas = {c["name"] for c in inspetor.get_columns("professional_profiles")}
    if "criado_via_indicacao" not in colunas:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE professional_profiles "
                "ADD COLUMN criado_via_indicacao BOOLEAN NOT NULL DEFAULT FALSE"
            ))


_garantir_colunas_novas()
rodar_seed()

app = FastAPI(title="SocorreAqui")

# Chave usada para assinar o cookie de sessão. Em produção, defina a
# variável de ambiente SECRET_KEY com um valor aleatório e secreto -- sem
# isso, qualquer pessoa que veja este código (é público no GitHub) consegue
# forjar um cookie de sessão válido pra qualquer usuário, sem senha nenhuma.
_SECRET_KEY_PADRAO = "troque-esta-chave-antes-de-colocar-no-ar"
SECRET_KEY = os.getenv("SECRET_KEY", _SECRET_KEY_PADRAO)
if SECRET_KEY == _SECRET_KEY_PADRAO:
    print(
        "!" * 70 + "\n"
        "AVISO DE SEGURANÇA: SECRET_KEY não configurada -- rodando com a "
        "chave padrão, que é pública no código-fonte. Defina a variável de "
        "ambiente SECRET_KEY com um valor aleatório antes de expor este "
        "app pra internet, senão qualquer pessoa consegue forjar login.\n"
        + "!" * 70
    )

# RENDER=true é definido automaticamente pelo próprio Render em produção --
# usamos isso pra só marcar o cookie de sessão como "só HTTPS" (Secure) lá,
# já que em desenvolvimento local (http://127.0.0.1) isso impediria o login
# de funcionar sem HTTPS.
RODANDO_EM_PRODUCAO = os.getenv("RENDER", "").lower() == "true"
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=RODANDO_EM_PRODUCAO,
)


# --- Limite de tentativas de login (proteção simples contra força bruta) ---
# Guardado em memória (zera a cada reinício/deploy) -- não é perfeito, mas
# barra o ataque mais óbvio: tentar senha atrás de senha sem parar.
_tentativas_login: dict[str, list[float]] = {}
LOGIN_MAX_TENTATIVAS = 8
LOGIN_JANELA_SEGUNDOS = 15 * 60  # 15 minutos


def _ip_cliente(request: Request) -> str:
    # Render (como qualquer hospedagem atrás de proxy) entrega o IP real do
    # visitante em X-Forwarded-For -- sem isso, request.client.host seria
    # sempre o IP interno do proxy, e todo mundo cairia no mesmo balde.
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def _login_bloqueado(chave: str) -> bool:
    agora = time.time()
    tentativas = _tentativas_login.get(chave, [])
    tentativas = [t for t in tentativas if agora - t < LOGIN_JANELA_SEGUNDOS]
    _tentativas_login[chave] = tentativas
    return len(tentativas) >= LOGIN_MAX_TENTATIVAS


def _registrar_tentativa_falha(chave: str) -> None:
    _tentativas_login.setdefault(chave, []).append(time.time())


def _limpar_tentativas(chave: str) -> None:
    _tentativas_login.pop(chave, None)


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    """Cabeçalhos básicos de proteção do navegador -- não substituem outras
    práticas, mas custam nada e bloqueiam classes inteiras de ataque
    (clickjacking, MIME sniffing, vazamento de referrer)."""
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if RODANDO_EM_PRODUCAO:
        resposta.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resposta

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()  # e-mail do seu usuário admin, defina no .env


def _avisar_admin_novo_cadastro(usuario: "models.User") -> None:
    # Se ADMIN_EMAIL não estiver configurado, ou o SendGrid não estiver
    # configurado, enviar_email() já cuida de não quebrar nada (só imprime
    # no terminal) -- então não precisa checar sendgrid_habilitado aqui.
    if ADMIN_EMAIL:
        email_utils.enviar_email_novo_cadastro(
            ADMIN_EMAIL, usuario.nome, usuario.email, usuario.telefone, usuario.tipo
        )


BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# "Cache busting" do CSS: gera um código a partir do conteúdo do arquivo, pra
# usar como ?v=... no link do style.css. Assim, toda vez que o visual muda,
# o navegador (e qualquer CDN na frente, tipo a que o Render usa) é obrigado
# a baixar a versão nova em vez de continuar usando uma antiga guardada.
with open(os.path.join(BASE_DIR, "static", "style.css"), "rb") as _f:
    CSS_VERSION = hashlib.md5(_f.read()).hexdigest()[:8]
templates.env.globals["css_version"] = CSS_VERSION
templates.env.globals["ano_atual"] = datetime.now().year
templates.env.globals["rodando_em_producao"] = RODANDO_EM_PRODUCAO


def _tojson(valor):
    # Jinja2 "puro" (sem Flask) não vem com filtro tojson — usado pra
    # embutir listas simples (ex: nomes de categoria) num <script> inline
    # com segurança, evitando fechar a tag </script> sem querer.
    return Markup(json.dumps(valor, ensure_ascii=False).replace("</", "<\\/"))


templates.env.filters["tojson"] = _tojson

# Ícone do WhatsApp (SVG inline) usado nos botões de contato — evita
# depender de um pacote de ícones externo pra um único glifo.
_WHATSAPP_ICON_PATH = (
    "M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.25-1.38c1.45.79 3.08 1.21 4.79 "
    "1.21h.01c5.46 0 9.9-4.45 9.9-9.91C21.95 6.45 17.5 2 12.04 2zm0 18.15h-.01c-1.5 0-2.97-.4-4.25-1.16l-.3-"
    ".18-3.12.82.83-3.04-.2-.31a8.19 8.19 0 0 1-1.26-4.37c0-4.54 3.7-8.24 8.25-8.24 2.2 0 4.27.86 5.83 2.42a"
    "8.18 8.18 0 0 1 2.41 5.83c0 4.55-3.7 8.23-8.18 8.23zm4.52-6.16c-.25-.12-1.47-.72-1.7-.81-.23-.08-.4-.12"
    "-.56.13-.17.25-.64.81-.78.97-.14.17-.29.19-.54.06-.25-.12-1.04-.38-1.98-1.22-.73-.65-1.23-1.46-1.37-1.7"
    "1-.14-.25-.02-.38.11-.51.11-.11.25-.29.37-.43.12-.14.16-.25.25-.41.08-.17.04-.31-.02-.43-.06-.12-.56-1."
    "36-.77-1.86-.2-.48-.41-.42-.56-.43h-.48c-.17 0-.43.06-.66.31-.23.25-.86.85-.86 2.07 0 1.22.89 2.4 1.01 "
    "2.57.12.17 1.75 2.68 4.25 3.75.59.26 1.06.41 1.42.53.6.19 1.14.16 1.57.1.48-.07 1.47-.6 1.68-1.18.21-.5"
    "8.21-1.08.14-1.18-.06-.1-.23-.16-.48-.28z"
)


def _whatsapp_icon(tamanho: int = 18) -> Markup:
    return Markup(
        f'<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 24 24" fill="currentColor" '
        f'aria-hidden="true"><path d="{_WHATSAPP_ICON_PATH}"/></svg>'
    )


templates.env.globals["whatsapp_icon"] = _whatsapp_icon

# Formatos de foto aceitos (chave = content-type enviado pelo navegador).
# Usamos a extensão daqui em vez de confiar no nome do arquivo enviado.
EXTENSOES_FOTO_PERMITIDAS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_FORMATO_PILLOW_POR_CONTENT_TYPE = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
TAMANHO_MAXIMO_FOTO = 5 * 1024 * 1024  # 5 MB


def _validar_e_normalizar_foto(conteudo: bytes, content_type: str) -> bytes | None:
    """Confere que os bytes enviados são mesmo uma imagem de verdade (o
    content-type do formulário é só o que o navegador declarou, e isso o
    usuário controla -- não dá pra confiar nele sozinho) e reexporta a
    imagem do zero, o que também descarta metadados EXIF (ex: coordenadas
    de GPS de onde a foto foi tirada, que o profissional provavelmente não
    quer expor). Retorna None se o arquivo não for uma imagem válida.
    """
    from PIL import Image, ImageOps

    try:
        imagem = Image.open(io.BytesIO(conteudo))
        imagem.verify()
        # verify() invalida o objeto pra qualquer uso seguinte -- reabre.
        imagem = Image.open(io.BytesIO(conteudo))
        imagem = ImageOps.exif_transpose(imagem)  # aplica a rotação do EXIF antes de descartá-lo
        formato = _FORMATO_PILLOW_POR_CONTENT_TYPE[content_type]
        if formato == "JPEG" and imagem.mode in ("RGBA", "P", "LA"):
            imagem = imagem.convert("RGB")
        buffer = io.BytesIO()
        imagem.save(buffer, format=formato)
        return buffer.getvalue()
    except Exception:
        return None

# Especialidades médicas mais comuns pro <select> — se não estiver na lista,
# o profissional pode digitar a própria em "Outra especialidade".
ESPECIALIDADES_MEDICAS = [
    "Clínico geral",
    "Cardiologista",
    "Dermatologista",
    "Pediatra",
    "Ginecologista/Obstetra",
    "Ortopedista",
    "Psiquiatra",
    "Neurologista",
    "Oftalmologista",
    "Otorrinolaringologista",
    "Urologista",
    "Endocrinologista",
    "Gastroenterologista",
    "Reumatologista",
    "Anestesiologista",
    "Nefrologista",
    "Pneumologista",
    "Infectologista",
    "Geriatra",
]

# Ícone (emoji) de cada categoria, usado na fileira de categorias da home e
# nos cards do catálogo. Categoria nova/"Outro" que não estiver aqui cai no
# ícone genérico 🔧.
CATEGORIA_EMOJIS = {
    "Eletricista": "⚡",
    "Encanador": "🚿",
    "Vidraceiro": "🪟",
    "Pedreiro / Reformas": "🧱",
    "Pintor": "🎨",
    "Gesseiro": "🏗️",
    "Serralheiro": "🛠️",
    "Marceneiro": "🪚",
    "Chaveiro": "🔑",
    "Marido de aluguel": "🔧",
    "Telhadista / Impermeabilização": "🏚️",

    "Diarista / Faxina": "🧹",
    "Dedetização": "🐜",
    "Montador de móveis": "🪑",
    "Piscineiro": "🏊",
    "Jardinagem": "🌳",
    "Ar-condicionado / Refrigeração": "❄️",
    "Tapeceiro / Estofados": "🛋️",
    "Limpeza pós-obra": "🧽",

    "Mecânico/Oficina": "🔩",
    "Auto elétrico": "🔋",
    "Funilaria e Pintura": "🚙",
    "Guincho": "🚛",
    "Lavagem de carros": "🧼",

    "Informática & Tecnologia": "💻",
    "Conserto de celular": "📱",
    "Instalação de câmeras / CFTV": "🎥",
    "Assistência de eletrônicos": "🔌",

    "Médico": "🩺",
    "Dentista": "🦷",
    "Babá": "👶",
    "Cuidador de idosos": "🧓",
    "Personal trainer": "🏋️",
    "Fisioterapeuta": "🦵",
    "Psicólogo": "🧠",
    "Nutricionista": "🥗",

    "Cabeleireiro": "💇",
    "Manicure": "💅",
    "Podóloga": "🦶",
    "Barbeiro": "💈",
    "Salão de Beleza": "✂️",
    "Maquiador(a)": "💄",
    "Fotógrafo": "📸",
    "Confeiteiro(a) / Doceiro(a)": "🧁",
    "Buffet / Garçom": "🍽️",
    "DJ / Som": "🎵",
    "Decoração de festas": "🎈",

    "Aulas particulares": "📖",
    "Professor de idiomas": "🗣️",
    "Contador": "📊",
    "Advogado": "⚖️",

    "Frete / Mudança": "🚚",
    "Motoboy / Entregador": "🛵",
    "Motorista Particular / Uber": "🚕",
}
templates.env.globals["categoria_emoji"] = lambda nome: CATEGORIA_EMOJIS.get(nome, "🔧")

# Ícone de cada grupo amplo (usado no menu de categorias, estilo "mega menu").
GRUPO_EMOJIS = {
    "Casa e Reformas": "🏠",
    "Limpeza e Manutenção": "🧹",
    "Carros e Motos": "🚗",
    "Tecnologia": "💻",
    "Saúde e Família": "🩺",
    "Beleza e Autocuidado": "💅",
    "Eventos": "🎉",
    "Aulas e Consultoria": "📚",
    "Transporte": "🚚",
}
templates.env.globals["grupo_emoji"] = lambda nome: GRUPO_EMOJIS.get(nome, "🔧")

# Categorias mostradas em destaque na home de quem não está logado —
# curadoria manual das mais buscadas, pra não sobrecarregar a tela
# com as ~50 categorias inteiras.
CATEGORIAS_DESTAQUE_LANDING = [
    "Eletricista",
    "Mecânico/Oficina",
    "Diarista / Faxina",
    "Manicure",
    "Encanador",
    "Informática & Tecnologia",
    "Pedreiro / Reformas",
    "Ar-condicionado / Refrigeração",
    "Conserto de celular",
    "Cabeleireiro",
]

# Descrição curta de cada categoria em destaque, só pro card ficar mais
# informativo na home — puramente texto de apresentação, não afeta o filtro.
CATEGORIA_DESCRICOES_DESTAQUE = {
    "Eletricista": "Instalações e reparos",
    "Mecânico/Oficina": "Manutenção e oficina",
    "Diarista / Faxina": "Limpeza residencial",
    "Manicure": "Unhas e cuidados",
    "Encanador": "Vazamentos e hidráulica",
    "Informática & Tecnologia": "Computadores e redes",
    "Pedreiro / Reformas": "Obras e reformas",
    "Ar-condicionado / Refrigeração": "Instalação e manutenção",
    "Conserto de celular": "Telas e reparos",
    "Cabeleireiro": "Cortes e tratamentos",
}
templates.env.globals["categoria_descricao"] = lambda nome: CATEGORIA_DESCRICOES_DESTAQUE.get(nome, "")


# ---------------------------------------------------------------------------
# SEO: slugs amigáveis pra URL (ex: "Ar-condicionado / Refrigeração" -> "ar-condicionado-refrigeracao")
# ---------------------------------------------------------------------------

def _slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
    return texto


# ---------------------------------------------------------------------------
# Catálogo (página inicial)
# ---------------------------------------------------------------------------

def cidades_mais_ativas(db: Session, limite: int = 6) -> list[str]:
    """Cidades com mais profissionais cadastrados, pra oferecer como atalho
    de clique — pra quem não quer (ou não confia n)a localização automática
    do navegador (GPS de notebook/rede errando é comum, e o site não tem
    como controlar isso)."""
    linhas = (
        db.query(models.ProfessionalProfile.cidade)
        .filter(
            models.ProfessionalProfile.aprovado == True,  # noqa: E712
            models.ProfessionalProfile.ativo == True,  # noqa: E712
            models.ProfessionalProfile.cidade.isnot(None),
            models.ProfessionalProfile.cidade != "",
        )
        .group_by(models.ProfessionalProfile.cidade)
        .order_by(func.count(models.ProfessionalProfile.id).desc())
        .limit(limite)
        .all()
    )
    return [linha[0] for linha in linhas]


def _resultados_catalogo(
    request, db, usuario, *,
    categoria=None, grupo=None, cidade=None, busca=None, ordenar="avaliacao",
    titulo_pagina=None, meta_descricao_pagina=None, h1_pagina=None,
):
    """Monta a resposta da tela de resultados (index.html) -- extraído do
    catalogo() pra ser reaproveitado pelas páginas de SEO por
    categoria+cidade (/servicos/...), que precisam da mesma listagem só
    que com título e descrição únicos pra cada combinação."""
    # O <select> de categoria manda "" quando é "Todas as categorias" —
    # não dá pra tipar o parâmetro como int direto, senão o FastAPI
    # rejeita a string vazia antes de chegar aqui.
    try:
        categoria_id = int(categoria) if categoria else None
    except ValueError:
        categoria_id = None

    query = db.query(models.ProfessionalProfile).filter(
        models.ProfessionalProfile.aprovado == True,  # noqa: E712
        models.ProfessionalProfile.ativo == True,  # noqa: E712
    )

    if categoria_id:
        query = query.filter(models.ProfessionalProfile.categorias.any(models.Category.id == categoria_id))
    elif grupo:
        query = query.filter(models.ProfessionalProfile.categorias.any(models.Category.grupo == grupo))

    if cidade:
        # O campo aceita cidade OU bairro (o rótulo já diz "Cidade / bairro"),
        # e é o mesmo campo usado pelo check-in por geolocalização.
        query = query.filter(or_(
            models.ProfessionalProfile.cidade.ilike(f"%{cidade}%"),
            models.ProfessionalProfile.bairro.ilike(f"%{cidade}%"),
        ))

    if busca:
        query = query.join(models.User).filter(
            or_(
                models.User.nome.ilike(f"%{busca}%"),
                models.ProfessionalProfile.descricao.ilike(f"%{busca}%"),
            )
        )

    profissionais = query.all()
    if ordenar == "recentes":
        profissionais.sort(key=lambda p: p.criado_em, reverse=True)
    else:
        ordenar = "avaliacao"
        profissionais.sort(key=lambda p: (p.nota_media, p.total_avaliacoes), reverse=True)

    # Sem nenhum filtro (busca "em branco"): mostra todo mundo, mas
    # agrupado por CIDADE (não bairro) -- o catálogo já tem gente de
    # municípios diferentes, e é o mesmo nível geográfico usado em todo
    # o resto do site (check-in, atalhos de cidade, páginas de SEO). Bairro
    # continua sendo um campo próprio no cadastro, só não é mais o critério
    # de agrupamento da home, com a cidade do usuário logado aparecendo
    # primeiro — assim é mais fácil achar quem atende perto de você.
    profissionais_por_cidade = None
    cidade_usuario = ""
    if not categoria_id and not grupo and not cidade and not busca:
        grupos = {}
        for p in profissionais:
            chave = (p.cidade or "").strip() or "Cidade não informada"
            grupos.setdefault(chave, []).append(p)

        if usuario:
            if usuario.cidade:
                cidade_usuario = usuario.cidade.strip()
            elif usuario.tipo == "profissional" and usuario.perfil_profissional and usuario.perfil_profissional.cidade:
                # Profissional já tem cidade cadastrada no perfil de atendimento —
                # não faz sentido pedir de novo em "Minha localização".
                cidade_usuario = usuario.perfil_profissional.cidade.strip()

        def ordem_cidade(nome):
            return (nome != cidade_usuario, nome == "Cidade não informada", nome.lower())

        profissionais_por_cidade = [(nome, grupos[nome]) for nome in sorted(grupos, key=ordem_cidade)]

    categorias = db.query(models.Category).order_by(models.Category.nome).all()

    # Organiza as categorias em grupos amplos pro menu (estilo "mega menu"):
    # segue a ordem de GRUPO_EMOJIS, e qualquer categoria sem grupo definido
    # (ex: criada via "Outro" no perfil) cai num grupo "Outros" no final.
    por_grupo = {}
    for c in categorias:
        por_grupo.setdefault(c.grupo or "Outros", []).append(c)
    ordem_grupos = list(GRUPO_EMOJIS.keys()) + [g for g in por_grupo if g not in GRUPO_EMOJIS]
    categorias_por_grupo = [(g, por_grupo[g]) for g in ordem_grupos if g in por_grupo]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "usuario": usuario,
            "profissionais": profissionais,
            "profissionais_por_cidade": profissionais_por_cidade,
            "cidade_usuario": cidade_usuario,
            "categorias": categorias,
            "categorias_por_grupo": categorias_por_grupo,
            "filtro_categoria": categoria_id,
            "filtro_grupo": grupo or "",
            "filtro_cidade": cidade or "",
            "filtro_busca": busca or "",
            "ordenar": ordenar,
            "cidades_destaque": cidades_mais_ativas(db),
            "eh_admin_usuario": eh_admin(usuario),
            "titulo_pagina": titulo_pagina,
            "meta_descricao_pagina": meta_descricao_pagina,
            "h1_pagina": h1_pagina,
        },
    )


@app.get("/")
def catalogo(
    request: Request,
    categoria: str | None = None,
    grupo: str | None = None,
    cidade: str | None = None,
    busca: str | None = None,
    explorar: str | None = None,
    ordenar: str = "avaliacao",
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    # Buscar e navegar o catálogo não exige conta — só pedimos login na hora
    # de agir de verdade (chamar no WhatsApp, ligar ou avaliar). Um visitante
    # sem nenhum filtro ainda cai na home de apresentação; qualquer busca,
    # filtro ou clique em "ver todas as categorias" (explorar) já mostra
    # os resultados de verdade.
    tem_filtro = bool(categoria or grupo or cidade or busca or explorar)

    if not usuario and not tem_filtro:
        categorias_destaque = (
            db.query(models.Category)
            .filter(models.Category.nome.in_(CATEGORIAS_DESTAQUE_LANDING))
            .all()
        )
        categorias_destaque.sort(
            key=lambda c: CATEGORIAS_DESTAQUE_LANDING.index(c.nome)
            if c.nome in CATEGORIAS_DESTAQUE_LANDING
            else len(CATEGORIAS_DESTAQUE_LANDING)
        )

        # "Profissionais em destaque": só dados reais, nunca inventados. Pega
        # os aprovados/ativos, prioriza melhor avaliados e desempata pelos
        # mais recentes — assim quem acabou de ser aprovado também aparece,
        # não só quem já tem avaliação.
        candidatos = (
            db.query(models.ProfessionalProfile)
            .filter(
                models.ProfessionalProfile.aprovado == True,  # noqa: E712
                models.ProfessionalProfile.ativo == True,  # noqa: E712
            )
            .all()
        )
        candidatos.sort(key=lambda p: (p.nota_media, p.total_avaliacoes, p.criado_em), reverse=True)
        profissionais_destaque = candidatos[:4]

        # Foto real pro hero: o melhor avaliado que tenha foto cadastrada de
        # verdade (procura em todo mundo aprovado, não só no top 4) — se
        # ninguém tiver foto ainda, o hero simplesmente não mostra essa
        # coluna, em vez de usar uma imagem de banco de imagens.
        profissional_hero = next((p for p in candidatos if p.foto_url), None)

        nomes_categorias = [c.nome for c in db.query(models.Category).order_by(models.Category.nome).all()]

        return templates.TemplateResponse(
            "landing.html",
            {
                "request": request,
                "categorias_destaque": categorias_destaque,
                "cidades_destaque": cidades_mais_ativas(db),
                "profissionais_destaque": profissionais_destaque,
                "profissional_hero": profissional_hero,
                "nomes_categorias": nomes_categorias,
            },
        )

    return _resultados_catalogo(
        request, db, usuario,
        categoria=categoria, grupo=grupo, cidade=cidade, busca=busca, ordenar=ordenar,
    )


# ---------------------------------------------------------------------------
# Páginas de SEO local: /servicos/<categoria>/<cidade>, ex: /servicos/eletricista/macae
# Mesma listagem do catálogo, só que com título/descrição únicos pra cada
# combinação -- é o formato de URL que melhor ranqueia pra busca tipo
# "eletricista em macaé".
# ---------------------------------------------------------------------------

@app.get("/servicos/{categoria_slug}/{cidade_slug}")
def pagina_seo_categoria_cidade(
    categoria_slug: str,
    cidade_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    categoria = next(
        (c for c in db.query(models.Category).all() if _slugify(c.nome) == categoria_slug),
        None,
    )
    if not categoria:
        return RedirectResponse("/?explorar=1", status_code=303)

    # A cidade é texto livre digitado por cada profissional -- não dá pra
    # ter uma lista fixa de slugs válidos, então batemos o slug da URL
    # contra as cidades que realmente existem no catálogo. Se não achar
    # nenhuma (ex: alguém digitou uma cidade errada na URL), mostra a
    # categoria em todas as cidades em vez de dar uma página quebrada.
    cidades_reais = (
        db.query(models.ProfessionalProfile.cidade)
        .filter(models.ProfessionalProfile.cidade.isnot(None), models.ProfessionalProfile.cidade != "")
        .distinct()
        .all()
    )
    cidade_real = next((c[0] for c in cidades_reais if _slugify(c[0]) == cidade_slug), None)

    if cidade_real:
        titulo = f"{categoria.nome} em {cidade_real} — SocorreAqui"
        h1 = f"{categoria.nome} em {cidade_real}"
        meta = f"Encontre {categoria.nome.lower()} em {cidade_real}. Veja avaliações e chame direto no WhatsApp, sem intermediário."
    else:
        titulo = f"{categoria.nome} — SocorreAqui"
        h1 = categoria.nome
        meta = f"Encontre {categoria.nome.lower()} perto de você. Veja avaliações e chame direto no WhatsApp, sem intermediário."

    return _resultados_catalogo(
        request, db, usuario,
        categoria=str(categoria.id), cidade=cidade_real,
        titulo_pagina=titulo, meta_descricao_pagina=meta, h1_pagina=h1,
    )


SITE_URL = "https://socorreaqui.tec.br"


@app.get("/googlefcb4228506c22a8f.html")
def google_search_console_verificacao():
    # Arquivo de verificação de propriedade do Google Search Console --
    # o conteúdo precisa ser exatamente esse (o nome do arquivo repetido
    # como texto), e o arquivo nunca pode ser removido, senão a
    # verificação é perdida.
    return PlainTextResponse("google-site-verification: googlefcb4228506c22a8f.html")


@app.get("/robots.txt")
def robots_txt():
    conteudo = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /minha-localizacao\n"
        "Disallow: /profissional/perfil/editar\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return PlainTextResponse(conteudo)


@app.get("/sitemap.xml")
def sitemap_xml(db: Session = Depends(get_db)):
    """Só lista páginas com conteúdo de verdade (sem thin content): a home,
    combinações categoria+cidade que têm pelo menos um profissional
    aprovado, e o perfil público de cada um deles."""
    urls = [(f"{SITE_URL}/", "1.0")]

    aprovados = (
        db.query(models.ProfessionalProfile)
        .filter(
            models.ProfessionalProfile.aprovado == True,  # noqa: E712
            models.ProfessionalProfile.ativo == True,  # noqa: E712
        )
        .all()
    )
    for p in aprovados:
        urls.append((f"{SITE_URL}/profissional/{p.id}", "0.7"))

    combinacoes = set()
    for p in aprovados:
        if not p.cidade:
            continue
        for cat in p.categorias:
            combinacoes.add((_slugify(cat.nome), _slugify(p.cidade)))
    for categoria_slug, cidade_slug in sorted(combinacoes):
        if categoria_slug and cidade_slug:
            urls.append((f"{SITE_URL}/servicos/{categoria_slug}/{cidade_slug}", "0.8"))

    itens_xml = "".join(
        f"<url><loc>{loc}</loc><priority>{prioridade}</priority></url>"
        for loc, prioridade in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{itens_xml}"
        "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Cadastro / Login / Logout
# ---------------------------------------------------------------------------

def _next_seguro(next: str | None) -> str:
    """Só aceita caminhos internos (começando com uma única "/") como destino
    pós-login — evita que alguém use ?next= pra redirecionar a vítima de um
    link malicioso pra fora do site (open redirect)."""
    if next and next.startswith("/") and not next.startswith("//") and not next.startswith("/\\"):
        return next
    return "/"


@app.get("/comecar")
def form_comecar(request: Request, tipo: str = "cliente", next: str | None = None):
    return templates.TemplateResponse(
        "comecar.html",
        {
            "request": request, "tipo": tipo if tipo in ("cliente", "profissional") else "cliente",
            "next": _next_seguro(next) if next else "",
        },
    )


@app.get("/cadastro")
def form_cadastro(request: Request, tipo: str = "cliente", next: str | None = None):
    return templates.TemplateResponse(
        "cadastro.html",
        {
            "request": request, "erro": None, "google_habilitado": google_oauth_habilitado,
            "tipo_selecionado": tipo if tipo in ("cliente", "profissional") else "cliente",
            "next": _next_seguro(next) if next else "",
        },
    )


@app.post("/cadastro")
def cadastrar(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    telefone: str = Form(...),
    senha: str = Form(...),
    tipo: str = Form(...),
    cidade: str = Form(""),
    bairro: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    nome = nome.strip()
    email = email.strip().lower()
    telefone = telefone.strip()

    def erro(mensagem):
        return templates.TemplateResponse(
            "cadastro.html",
            {
                "request": request, "erro": mensagem, "google_habilitado": google_oauth_habilitado,
                "tipo_selecionado": tipo if tipo in ("cliente", "profissional") else "cliente",
                "next": next,
            },
        )

    if not EMAIL_REGEX.match(email):
        return erro("Digite um e-mail válido.")
    # Mensagem genérica de propósito -- não diz se foi o e-mail ou o telefone
    # que já existe, pra não dar pra alguém checar "esse e-mail já tem
    # conta?" só tentando cadastrar (enumeração de contas).
    ja_existe = (
        db.query(models.User).filter(models.User.email == email).first()
        or db.query(models.User).filter(models.User.telefone == telefone).first()
    )
    if ja_existe:
        return erro("Já existe uma conta com esse e-mail ou telefone. Se for sua, faça login.")

    novo_usuario = models.User(
        nome=nome,
        email=email,
        telefone=telefone,
        senha_hash=auth.gerar_hash_senha(senha),
        tipo=tipo,
        cidade=cidade.strip() or None,
        bairro=bairro.strip() or None,
        email_verificado=True,
    )
    db.add(novo_usuario)
    db.commit()
    db.refresh(novo_usuario)

    if tipo == "profissional":
        perfil = models.ProfessionalProfile(usuario_id=novo_usuario.id, cidade="")
        db.add(perfil)
        db.commit()

    _avisar_admin_novo_cadastro(novo_usuario)
    request.session["user_id"] = novo_usuario.id

    if tipo == "profissional":
        return RedirectResponse("/profissional/perfil/editar", status_code=303)
    return RedirectResponse(_next_seguro(next), status_code=303)


@app.get("/login")
def form_login(request: Request, next: str | None = None):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request, "erro": None, "google_habilitado": google_oauth_habilitado,
            "next": _next_seguro(next) if next else "",
        },
    )


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()

    def erro(mensagem):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "erro": mensagem, "google_habilitado": google_oauth_habilitado, "next": next},
        )

    chave_limite = _ip_cliente(request)
    if _login_bloqueado(chave_limite):
        return erro("Muitas tentativas de login. Aguarde alguns minutos e tente de novo.")

    usuario = db.query(models.User).filter(models.User.email == email).first()

    # Mensagem sempre igual (não diz se o e-mail existe, se a senha é
    # errada, ou se a conta é só de Google) -- evita dar pra alguém
    # descobrir quais e-mails têm conta só de tentar logar com eles. Quem
    # cadastrou com Google já vê o botão "Entrar com Google" na mesma tela.
    if (
        not usuario
        or not usuario.senha_hash
        or not auth.verificar_senha(senha, usuario.senha_hash)
    ):
        _registrar_tentativa_falha(chave_limite)
        return erro("E-mail ou senha incorretos.")

    if not usuario.ativo:
        return erro("Essa conta foi bloqueada. Entre em contato com o suporte.")

    _limpar_tentativas(chave_limite)
    request.session["user_id"] = usuario.id
    return RedirectResponse(_next_seguro(next), status_code=303)


# ---------------------------------------------------------------------------
# Login com Google
# ---------------------------------------------------------------------------

@app.get("/auth/google/login")
async def google_login(request: Request, next: str | None = None):
    if not google_oauth_habilitado:
        return RedirectResponse("/login", status_code=303)
    # Guarda o destino na sessão -- a ida e volta pro Google não preserva
    # query string nenhuma além da que o próprio OAuth usa.
    request.session["oauth_next"] = _next_seguro(next) if next else ""
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    if not google_oauth_habilitado:
        return RedirectResponse("/login", status_code=303)

    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.google.userinfo(token=token)
    email = userinfo["email"].strip().lower()
    nome = userinfo.get("name") or email.split("@")[0]
    google_id = userinfo["sub"]

    proximo = request.session.pop("oauth_next", "") or "/"
    usuario = db.query(models.User).filter(models.User.email == email).first()

    if usuario:
        if not usuario.ativo:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request, "google_habilitado": google_oauth_habilitado,
                    "erro": "Essa conta foi bloqueada. Entre em contato com o suporte.",
                },
            )
        atualizado = False
        if not usuario.google_id:
            usuario.google_id = google_id
            atualizado = True
        if not usuario.email_verificado:
            usuario.email_verificado = True
            atualizado = True
        if atualizado:
            db.commit()
        request.session["user_id"] = usuario.id
        return RedirectResponse(proximo, status_code=303)

    # Conta nova via Google: ainda falta telefone (contato) e tipo de conta.
    # Guarda o destino de novo, porque completar-cadastro-google é mais um
    # redirect antes do login de verdade acontecer.
    request.session["google_pendente"] = {"email": email, "nome": nome, "google_id": google_id}
    request.session["oauth_next"] = proximo
    return RedirectResponse("/completar-cadastro-google", status_code=303)


@app.get("/completar-cadastro-google")
def form_completar_cadastro_google(request: Request):
    pendente = request.session.get("google_pendente")
    if not pendente:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "completar_cadastro_google.html",
        {"request": request, "nome": pendente["nome"], "email": pendente["email"], "erro": None},
    )


@app.post("/completar-cadastro-google")
def completar_cadastro_google(
    request: Request,
    telefone: str = Form(...),
    tipo: str = Form(...),
    cidade: str = Form(""),
    bairro: str = Form(""),
    db: Session = Depends(get_db),
):
    pendente = request.session.get("google_pendente")
    if not pendente:
        return RedirectResponse("/login", status_code=303)

    telefone = telefone.strip()

    if db.query(models.User).filter(models.User.telefone == telefone).first():
        return templates.TemplateResponse(
            "completar_cadastro_google.html",
            {
                "request": request, "nome": pendente["nome"], "email": pendente["email"],
                "erro": "Já existe um cadastro com esse telefone.",
            },
        )

    novo_usuario = models.User(
        nome=pendente["nome"],
        email=pendente["email"],
        telefone=telefone,
        senha_hash=None,
        google_id=pendente["google_id"],
        tipo=tipo,
        cidade=cidade.strip() or None,
        bairro=bairro.strip() or None,
        email_verificado=True,  # o Google já confirmou esse e-mail
    )
    db.add(novo_usuario)
    db.commit()
    db.refresh(novo_usuario)

    if tipo == "profissional":
        perfil = models.ProfessionalProfile(usuario_id=novo_usuario.id, cidade="")
        db.add(perfil)
        db.commit()

    _avisar_admin_novo_cadastro(novo_usuario)
    del request.session["google_pendente"]
    proximo = request.session.pop("oauth_next", "") or "/"
    request.session["user_id"] = novo_usuario.id

    if tipo == "profissional":
        return RedirectResponse("/profissional/perfil/editar", status_code=303)
    return RedirectResponse(proximo, status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Meus dados (nome, cidade/bairro do usuário — usado pra agrupar o catálogo)
# ---------------------------------------------------------------------------

@app.get("/minha-localizacao")
def form_minha_localizacao(request: Request, usuario=Depends(auth.usuario_logado)):
    if not usuario:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "minha_localizacao.html", {"request": request, "usuario": usuario, "erro": None},
    )


@app.post("/minha-localizacao")
def salvar_minha_localizacao(
    request: Request,
    nome: str = Form(""),
    cidade: str = Form(""),
    bairro: str = Form(""),
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario:
        return RedirectResponse("/login", status_code=303)

    nome = nome.strip()
    if not nome:
        return templates.TemplateResponse(
            "minha_localizacao.html",
            {"request": request, "usuario": usuario, "erro": "O nome não pode ficar em branco."},
        )

    usuario.nome = nome
    usuario.cidade = cidade.strip() or None
    usuario.bairro = bairro.strip() or None
    db.commit()

    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Perfil público do profissional + avaliação
# ---------------------------------------------------------------------------

@app.get("/profissional/{profissional_id}")
def ver_profissional(
    profissional_id: int,
    request: Request,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    # O perfil público pode ser visto sem login — só avaliar e chamar no
    # WhatsApp exigem conta (a própria tela já sabia lidar com usuario=None
    # no bloco de avaliação; só faltava permitir chegar até aqui sem login).
    perfil = db.query(models.ProfessionalProfile).filter(
        models.ProfessionalProfile.id == profissional_id
    ).first()
    if not perfil:
        return RedirectResponse("/", status_code=303)

    avaliacoes = sorted(perfil.avaliacoes, key=lambda r: r.criado_em, reverse=True)

    # Qualquer pessoa logada pode avaliar, contanto que não seja o próprio
    # dono do perfil se autoavaliando (cliente ou profissional avaliando
    # outro profissional são ambos permitidos).
    pode_avaliar = bool(usuario) and usuario.id != perfil.usuario_id
    minha_avaliacao = None
    if usuario and pode_avaliar:
        minha_avaliacao = next((r for r in avaliacoes if r.cliente_id == usuario.id), None)

    return templates.TemplateResponse(
        "perfil_profissional.html",
        {
            "request": request,
            "usuario": usuario,
            "perfil": perfil,
            "avaliacoes": avaliacoes,
            "minha_avaliacao": minha_avaliacao,
            "pode_avaliar": pode_avaliar,
        },
    )


@app.post("/profissional/{profissional_id}/avaliar")
def avaliar(
    profissional_id: int,
    request: Request,
    estrelas: int = Form(...),
    comentario: str = Form(""),
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario:
        return RedirectResponse("/login", status_code=303)

    perfil = db.query(models.ProfessionalProfile).filter(
        models.ProfessionalProfile.id == profissional_id
    ).first()
    # Ninguém pode se autoavaliar — vale tanto pra cliente quanto pra
    # profissional avaliando outro profissional (a tela já esconde o
    # formulário nesse caso, mas a checagem tem que valer aqui também).
    if not perfil or usuario.id == perfil.usuario_id:
        return RedirectResponse(f"/profissional/{profissional_id}", status_code=303)

    estrelas = max(1, min(5, estrelas))  # trava entre 1 e 5

    # Um cliente só tem uma avaliação por profissional: se ele avaliar de
    # novo, atualiza a que já existia em vez de criar outra (evita inflar
    # a nota média com avaliações repetidas da mesma pessoa).
    review = db.query(models.Review).filter(
        models.Review.profissional_id == profissional_id,
        models.Review.cliente_id == usuario.id,
    ).first()
    if review:
        review.estrelas = estrelas
        review.comentario = comentario.strip() or None
    else:
        review = models.Review(
            profissional_id=profissional_id,
            cliente_id=usuario.id,
            estrelas=estrelas,
            comentario=comentario.strip() or None,
        )
        db.add(review)
    db.commit()

    return RedirectResponse(f"/profissional/{profissional_id}", status_code=303)


@app.post("/profissional/{profissional_id}/avaliar/excluir")
def excluir_avaliacao(
    profissional_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario or usuario.tipo != "cliente":
        return RedirectResponse("/login", status_code=303)

    db.query(models.Review).filter(
        models.Review.profissional_id == profissional_id,
        models.Review.cliente_id == usuario.id,
    ).delete()
    db.commit()

    return RedirectResponse(f"/profissional/{profissional_id}", status_code=303)


# ---------------------------------------------------------------------------
# Indicação de profissional (usuário logado indica alguém que ainda não
# está cadastrado no app; o admin vê e entra em contato)
# ---------------------------------------------------------------------------

@app.get("/indicar")
def form_indicar(
    request: Request,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario:
        return RedirectResponse("/login", status_code=303)

    categorias = db.query(models.Category).order_by(models.Category.nome).all()
    return templates.TemplateResponse(
        "indicar.html",
        {"request": request, "usuario": usuario, "categorias": categorias, "enviado": False},
    )


@app.post("/indicar")
def indicar(
    request: Request,
    nome_profissional: str = Form(...),
    telefone_profissional: str = Form(...),
    categoria_id: str = Form(""),
    cidade: str = Form(""),
    observacao: str = Form(""),
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario:
        return RedirectResponse("/login", status_code=303)

    nome_profissional = nome_profissional.strip()
    telefone_profissional = telefone_profissional.strip()
    cidade = cidade.strip()

    try:
        categoria_id_int = int(categoria_id) if categoria_id else None
    except ValueError:
        categoria_id_int = None

    def erro(mensagem):
        categorias = db.query(models.Category).order_by(models.Category.nome).all()
        return templates.TemplateResponse(
            "indicar.html",
            {
                "request": request, "usuario": usuario, "categorias": categorias, "enviado": False,
                "erro": mensagem,
                "valores": {
                    "nome_profissional": nome_profissional,
                    "telefone_profissional": telefone_profissional,
                    "categoria_id": categoria_id,
                    "cidade": cidade,
                },
            },
        )

    # Nome/telefone já são obrigatórios no HTML, mas a categoria e a cidade
    # também precisam vir preenchidas — sem isso não dá pra saber o que
    # oferecer nem onde a pessoa atende na hora de convidar ela pra plataforma.
    if not nome_profissional or not telefone_profissional:
        return erro("Preencha o nome e o telefone do profissional.")
    if not categoria_id_int:
        return erro("Selecione a categoria do profissional.")
    if not cidade:
        return erro("Informe a cidade/bairro do profissional.")

    indicacao = models.Indicacao(
        indicado_por_id=usuario.id,
        nome_profissional=nome_profissional,
        telefone_profissional=telefone_profissional,
        categoria_id=categoria_id_int,
        cidade=cidade,
        observacao=observacao.strip() or None,
    )
    db.add(indicacao)
    db.commit()

    categorias = db.query(models.Category).order_by(models.Category.nome).all()
    return templates.TemplateResponse(
        "indicar.html",
        {"request": request, "usuario": usuario, "categorias": categorias, "enviado": True},
    )


# ---------------------------------------------------------------------------
# Área do profissional (editar o próprio perfil)
# ---------------------------------------------------------------------------

@app.get("/profissional/perfil/editar")
def form_editar_perfil(
    request: Request,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario or usuario.tipo != "profissional":
        return RedirectResponse("/login", status_code=303)

    categorias = db.query(models.Category).order_by(models.Category.nome).all()
    perfil = usuario.perfil_profissional
    categorias_selecionadas = {c.id for c in perfil.categorias} if perfil else set()

    return templates.TemplateResponse(
        "editar_perfil.html",
        {
            "request": request,
            "usuario": usuario,
            "perfil": perfil,
            "categorias": categorias,
            "categorias_selecionadas": categorias_selecionadas,
            "especialidades_medicas": ESPECIALIDADES_MEDICAS,
            "erro": None,
        },
    )


def _erro_editar_perfil(request, db, usuario, perfil, mensagem):
    """Reexibe o formulário de editar perfil com uma mensagem de erro."""
    categorias = db.query(models.Category).order_by(models.Category.nome).all()
    categorias_selecionadas = {c.id for c in perfil.categorias} if perfil else set()
    return templates.TemplateResponse(
        "editar_perfil.html",
        {
            "request": request,
            "usuario": usuario,
            "perfil": perfil,
            "categorias": categorias,
            "categorias_selecionadas": categorias_selecionadas,
            "especialidades_medicas": ESPECIALIDADES_MEDICAS,
            "erro": mensagem,
        },
    )


def _aplicar_dados_perfil(
    perfil, db, *, cidade, bairro, endereco, atende_domicilio, descricao,
    valor_mao_de_obra, whatsapp, categorias_ids, outra_categoria,
    crm, especialidade_medica, especialidade_medica_outra,
    atende_convenio, convenios_aceitos, foto,
):
    """
    Aplica os campos do formulário de editar perfil (usado tanto pelo próprio
    profissional quanto pelo admin editando em nome dele). Retorna uma
    mensagem de erro (str) se algum campo obrigatório estiver faltando ou a
    foto for inválida, ou None se salvou certo.
    """
    cidade = cidade.strip()
    bairro = bairro.strip()
    whatsapp = whatsapp.strip()

    if not cidade:
        return "Cidade é obrigatória."
    if not bairro:
        return "Bairro é obrigatório."
    if not whatsapp:
        return "WhatsApp para contato é obrigatório."
    # Sem isso, um profissional conseguia terminar o cadastro sem escolher
    # nenhuma área de atuação -- e o admin não tinha como saber o que essa
    # pessoa faz na hora de decidir se aprova ou não.
    if not categorias_ids and not outra_categoria.strip():
        return "Selecione pelo menos uma categoria (ou digite em \"Outra categoria\")."

    tem_foto_nova = bool(foto and foto.filename)
    if not tem_foto_nova and not perfil.foto_url:
        return "Foto é obrigatória."

    if tem_foto_nova:
        if foto.content_type not in EXTENSOES_FOTO_PERMITIDAS:
            return "Formato de foto não suportado. Envie uma imagem JPG, PNG ou WEBP."
        conteudo = foto.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_FOTO:
            return "A foto é muito grande (máximo 5 MB)."
        conteudo_normalizado = _validar_e_normalizar_foto(conteudo, foto.content_type)
        if conteudo_normalizado is None:
            return "O arquivo enviado não é uma imagem válida. Tente outra foto."
        extensao = EXTENSOES_FOTO_PERMITIDAS[foto.content_type]
        nome_arquivo = f"profissional_{perfil.usuario_id}{extensao}"
        perfil.foto_url = storage.salvar_foto(conteudo_normalizado, nome_arquivo, foto.content_type)

    perfil.cidade = cidade
    perfil.bairro = bairro
    perfil.endereco = endereco.strip()
    perfil.atende_domicilio = bool(atende_domicilio)
    perfil.descricao = descricao.strip()
    perfil.valor_mao_de_obra = valor_mao_de_obra.strip()
    perfil.whatsapp = whatsapp

    # Campos de médico — só têm efeito visível se a categoria "Médico"
    # também estiver marcada (ver property `eh_medico`), mas salvamos o
    # que veio no formulário de qualquer forma.
    perfil.crm = crm.strip() or None
    especialidade_final = (
        especialidade_medica_outra.strip()
        if especialidade_medica == "_outra"
        else especialidade_medica.strip()
    )
    perfil.especialidade_medica = especialidade_final or None
    if atende_convenio == "sim":
        perfil.atende_convenio = True
    elif atende_convenio == "nao":
        perfil.atende_convenio = False
    else:
        perfil.atende_convenio = None
    perfil.convenios_aceitos = convenios_aceitos.strip() or None

    categorias_escolhidas = db.query(models.Category).filter(
        models.Category.id.in_(categorias_ids)
    ).all()

    outra_categoria = outra_categoria.strip()
    if outra_categoria:
        # Reaproveita a categoria se já existir uma com o mesmo nome
        # (ignorando maiúsculas/minúsculas), pra não criar duplicada.
        categoria_nova = db.query(models.Category).filter(
            func.lower(models.Category.nome) == outra_categoria.lower()
        ).first()
        if not categoria_nova:
            categoria_nova = models.Category(nome=outra_categoria)
            db.add(categoria_nova)
            db.commit()
            db.refresh(categoria_nova)
        if categoria_nova not in categorias_escolhidas:
            categorias_escolhidas.append(categoria_nova)

    perfil.categorias = categorias_escolhidas

    db.commit()
    db.refresh(perfil)
    return None


@app.post("/profissional/perfil/editar")
def salvar_perfil(
    request: Request,
    cidade: str = Form(...),
    bairro: str = Form(""),
    endereco: str = Form(""),
    atende_domicilio: str | None = Form(None),
    descricao: str = Form(""),
    valor_mao_de_obra: str = Form(""),
    whatsapp: str = Form(""),
    categorias_ids: list[int] = Form([]),
    outra_categoria: str = Form(""),
    crm: str = Form(""),
    especialidade_medica: str = Form(""),
    especialidade_medica_outra: str = Form(""),
    atende_convenio: str | None = Form(None),
    convenios_aceitos: str = Form(""),
    foto: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario or usuario.tipo != "profissional":
        return RedirectResponse("/login", status_code=303)

    perfil = usuario.perfil_profissional
    if not perfil:
        perfil = models.ProfessionalProfile(usuario_id=usuario.id, cidade="")
        db.add(perfil)

    erro_msg = _aplicar_dados_perfil(
        perfil, db, cidade=cidade, bairro=bairro, endereco=endereco,
        atende_domicilio=atende_domicilio, descricao=descricao,
        valor_mao_de_obra=valor_mao_de_obra, whatsapp=whatsapp,
        categorias_ids=categorias_ids, outra_categoria=outra_categoria,
        crm=crm, especialidade_medica=especialidade_medica,
        especialidade_medica_outra=especialidade_medica_outra,
        atende_convenio=atende_convenio, convenios_aceitos=convenios_aceitos,
        foto=foto,
    )
    if erro_msg:
        return _erro_editar_perfil(request, db, usuario, perfil, erro_msg)

    return RedirectResponse(f"/profissional/{perfil.id}", status_code=303)


# ---------------------------------------------------------------------------
# Painel admin (aprovar profissionais)
# ---------------------------------------------------------------------------

def eh_admin(usuario) -> bool:
    return bool(usuario) and bool(ADMIN_EMAIL) and usuario.email == ADMIN_EMAIL


@app.get("/admin")
def admin_painel(
    request: Request,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)

    pendentes = db.query(models.ProfessionalProfile).filter(
        models.ProfessionalProfile.aprovado == False  # noqa: E712
    ).all()
    aprovados = db.query(models.ProfessionalProfile).filter(
        models.ProfessionalProfile.aprovado == True  # noqa: E712
    ).all()
    indicacoes_pendentes = db.query(models.Indicacao).filter(
        models.Indicacao.status == "pendente"
    ).order_by(models.Indicacao.criado_em.desc()).all()
    indicacoes_contatadas = db.query(models.Indicacao).filter(
        models.Indicacao.status == "contatada"
    ).order_by(models.Indicacao.criado_em.desc()).all()
    indicacoes_autenticadas = db.query(models.Indicacao).filter(
        models.Indicacao.status == "autenticada"
    ).order_by(models.Indicacao.criado_em.desc()).all()
    # Indicações autenticadas antes dessa função criar o profissional de
    # verdade (versão anterior só marcava o status) ficaram "presas" sem
    # perfil -- usamos isso pra saber quais ainda precisam do botão de
    # publicar de novo.
    telefones_ja_profissionais = {
        linha[0] for linha in db.query(models.User.telefone).filter(models.User.tipo == "profissional").all()
    }

    clientes = db.query(models.User).filter(
        models.User.tipo == "cliente"
    ).order_by(models.User.criado_em.desc()).all()

    # --- Números do site ---------------------------------------------------
    sete_dias_atras = datetime.utcnow() - timedelta(days=7)
    total_avaliacoes = db.query(models.Review).count()
    media_geral = db.query(func.avg(models.Review.estrelas)).scalar()
    novos_cadastros_7d = db.query(models.User).filter(
        models.User.criado_em >= sete_dias_atras
    ).count()
    categorias_populares = (
        db.query(models.Category.nome, func.count(models.ProfessionalProfile.id))
        .join(models.professional_categories, models.Category.id == models.professional_categories.c.category_id)
        .join(models.ProfessionalProfile, models.ProfessionalProfile.id == models.professional_categories.c.professional_id)
        .filter(models.ProfessionalProfile.aprovado == True)  # noqa: E712
        .group_by(models.Category.nome)
        .order_by(func.count(models.ProfessionalProfile.id).desc())
        .limit(5)
        .all()
    )

    numeros = {
        "total_clientes": len(clientes),
        "total_profissionais_aprovados": len(aprovados),
        "total_profissionais_pendentes": len(pendentes),
        "total_avaliacoes": total_avaliacoes,
        "media_geral": round(media_geral, 1) if media_geral else None,
        "total_indicacoes_pendentes": len(indicacoes_pendentes),
        "total_indicacoes_contatadas": len(indicacoes_contatadas),
        "total_indicacoes_autenticadas": len(indicacoes_autenticadas),
        "novos_cadastros_7d": novos_cadastros_7d,
        "categorias_populares": categorias_populares,
    }

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "usuario": usuario,
            "pendentes": pendentes,
            "aprovados": aprovados,
            "indicacoes_pendentes": indicacoes_pendentes,
            "indicacoes_contatadas": indicacoes_contatadas,
            "indicacoes_autenticadas": indicacoes_autenticadas,
            "telefones_ja_profissionais": telefones_ja_profissionais,
            "clientes": clientes,
            "numeros": numeros,
        },
    )


@app.get("/admin/profissional/{perfil_id}/editar")
def admin_form_editar_perfil(
    perfil_id: int,
    request: Request,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)

    perfil = db.query(models.ProfessionalProfile).filter(models.ProfessionalProfile.id == perfil_id).first()
    if not perfil:
        return RedirectResponse("/admin", status_code=303)

    categorias = db.query(models.Category).order_by(models.Category.nome).all()
    categorias_selecionadas = {c.id for c in perfil.categorias}

    return templates.TemplateResponse(
        "editar_perfil.html",
        {
            "request": request,
            "usuario": usuario,
            "perfil": perfil,
            "categorias": categorias,
            "categorias_selecionadas": categorias_selecionadas,
            "especialidades_medicas": ESPECIALIDADES_MEDICAS,
            "erro": None,
            "form_action": f"/admin/profissional/{perfil_id}/editar",
            "admin_editando": perfil.usuario,
        },
    )


@app.post("/admin/profissional/{perfil_id}/editar")
def admin_salvar_perfil(
    perfil_id: int,
    request: Request,
    cidade: str = Form(...),
    bairro: str = Form(""),
    endereco: str = Form(""),
    atende_domicilio: str | None = Form(None),
    descricao: str = Form(""),
    valor_mao_de_obra: str = Form(""),
    whatsapp: str = Form(""),
    categorias_ids: list[int] = Form([]),
    outra_categoria: str = Form(""),
    crm: str = Form(""),
    especialidade_medica: str = Form(""),
    especialidade_medica_outra: str = Form(""),
    atende_convenio: str | None = Form(None),
    convenios_aceitos: str = Form(""),
    foto: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)

    perfil = db.query(models.ProfessionalProfile).filter(models.ProfessionalProfile.id == perfil_id).first()
    if not perfil:
        return RedirectResponse("/admin", status_code=303)

    erro_msg = _aplicar_dados_perfil(
        perfil, db, cidade=cidade, bairro=bairro, endereco=endereco,
        atende_domicilio=atende_domicilio, descricao=descricao,
        valor_mao_de_obra=valor_mao_de_obra, whatsapp=whatsapp,
        categorias_ids=categorias_ids, outra_categoria=outra_categoria,
        crm=crm, especialidade_medica=especialidade_medica,
        especialidade_medica_outra=especialidade_medica_outra,
        atende_convenio=atende_convenio, convenios_aceitos=convenios_aceitos,
        foto=foto,
    )
    if erro_msg:
        categorias = db.query(models.Category).order_by(models.Category.nome).all()
        categorias_selecionadas = {c.id for c in perfil.categorias}
        return templates.TemplateResponse(
            "editar_perfil.html",
            {
                "request": request,
                "usuario": usuario,
                "perfil": perfil,
                "categorias": categorias,
                "categorias_selecionadas": categorias_selecionadas,
                "especialidades_medicas": ESPECIALIDADES_MEDICAS,
                "erro": erro_msg,
                "form_action": f"/admin/profissional/{perfil_id}/editar",
                "admin_editando": perfil.usuario,
            },
        )

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/usuario/{usuario_id}/bloquear")
def admin_bloquear_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    alvo = db.query(models.User).filter(models.User.id == usuario_id).first()
    if alvo and alvo.email != ADMIN_EMAIL:  # admin nao consegue se autobloquear
        alvo.ativo = not alvo.ativo
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/usuario/{usuario_id}/excluir")
def admin_excluir_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    alvo = db.query(models.User).filter(models.User.id == usuario_id).first()
    if alvo and alvo.email != ADMIN_EMAIL:  # admin nao consegue se autoexcluir
        db.delete(alvo)
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/aprovar/{perfil_id}")
def admin_aprovar(
    perfil_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    perfil = db.query(models.ProfessionalProfile).filter(models.ProfessionalProfile.id == perfil_id).first()
    if perfil:
        perfil.aprovado = True
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/recusar/{perfil_id}")
def admin_recusar(
    perfil_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    perfil = db.query(models.ProfessionalProfile).filter(models.ProfessionalProfile.id == perfil_id).first()
    if perfil and not perfil.aprovado:
        # Recusa = remove o cadastro inteiro (perfil pendente costuma ser
        # spam/perfil falso). Se for um profissional de verdade, ele pode
        # se cadastrar de novo.
        db.delete(perfil.usuario)
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/pausar/{perfil_id}")
def admin_pausar(
    perfil_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    perfil = db.query(models.ProfessionalProfile).filter(models.ProfessionalProfile.id == perfil_id).first()
    if perfil:
        perfil.ativo = not perfil.ativo
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/indicacao/{indicacao_id}/contatado")
def admin_indicacao_contatado(
    indicacao_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    indicacao = db.query(models.Indicacao).filter(models.Indicacao.id == indicacao_id).first()
    if indicacao:
        indicacao.status = "contatada"
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/indicacao/{indicacao_id}/autenticar")
def admin_indicacao_autenticar(
    indicacao_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    indicacao = db.query(models.Indicacao).filter(models.Indicacao.id == indicacao_id).first()
    if not indicacao:
        return RedirectResponse("/admin", status_code=303)

    # "Autenticar" publica o profissional direto no catálogo, sem ele
    # precisar se cadastrar sozinho -- útil pra quando o admin já confirmou
    # por telefone que a pessoa existe e topa aparecer. Se já existir conta
    # com esse telefone (a pessoa se cadastrou por conta própria nesse
    # meio-tempo, por exemplo), só marca a indicação como autenticada e
    # não cria duplicado.
    ja_tem_conta = db.query(models.User).filter(
        models.User.telefone == indicacao.telefone_profissional
    ).first()
    if not ja_tem_conta:
        telefone_digitos = re.sub(r"\D", "", indicacao.telefone_profissional) or str(indicacao.id)
        novo_usuario = models.User(
            nome=indicacao.nome_profissional,
            email=f"indicacao-{telefone_digitos}@sem-email.socorreaqui.com.br",
            telefone=indicacao.telefone_profissional,
            senha_hash=None,
            tipo="profissional",
            cidade=indicacao.cidade,
            email_verificado=False,
            ativo=True,
        )
        db.add(novo_usuario)
        db.commit()
        db.refresh(novo_usuario)

        perfil = models.ProfessionalProfile(
            usuario_id=novo_usuario.id,
            cidade=indicacao.cidade or "",
            aprovado=True,
            ativo=True,
            criado_via_indicacao=True,
        )
        if indicacao.categoria:
            perfil.categorias.append(indicacao.categoria)
        db.add(perfil)

    indicacao.status = "autenticada"
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/indicacao/{indicacao_id}/descartar")
def admin_indicacao_descartar(
    indicacao_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not eh_admin(usuario):
        return RedirectResponse("/", status_code=303)
    indicacao = db.query(models.Indicacao).filter(models.Indicacao.id == indicacao_id).first()
    if indicacao:
        db.delete(indicacao)
        db.commit()
    return RedirectResponse("/admin", status_code=303)
