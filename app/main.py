"""
Aplicação principal.

Para rodar localmente:

    uvicorn app.main:app --reload

Depois abra http://127.0.0.1:8000 no navegador.

Veja o LEIA-ME.md na raiz do projeto para o passo a passo completo
de instalação.
"""
import hashlib
import os
import re
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app.database import Base, engine, get_db
from app import models, auth, storage
from app.oauth import oauth, google_oauth_habilitado
from app.seed import rodar_seed

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Cria as tabelas automaticamente se ainda não existirem, e mantém as
# categorias padrão (nome + grupo) sempre alinhadas com CATEGORIAS_PADRAO —
# assim um novo deploy já atualiza o catálogo de categorias sozinho, sem
# precisar rodar "python -m app.seed" manualmente em produção.
Base.metadata.create_all(bind=engine)
rodar_seed()

app = FastAPI(title="SocorreAqui")

# Chave usada para assinar o cookie de sessão. Em produção, defina a
# variável de ambiente SECRET_KEY com um valor aleatório e secreto.
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-antes-de-colocar-no-ar")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

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
TAMANHO_MAXIMO_FOTO = 5 * 1024 * 1024  # 5 MB

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


# ---------------------------------------------------------------------------
# Catálogo (página inicial)
# ---------------------------------------------------------------------------

@app.get("/")
def catalogo(
    request: Request,
    categoria: str | None = None,
    grupo: str | None = None,
    cidade: str | None = None,
    busca: str | None = None,
    explorar: str | None = None,
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
        return templates.TemplateResponse(
            "landing.html",
            {"request": request, "categorias_destaque": categorias_destaque},
        )

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
    # Ordena pelos mais bem avaliados primeiro
    profissionais.sort(key=lambda p: (p.nota_media, p.total_avaliacoes), reverse=True)

    # Sem nenhum filtro (busca "em branco"): mostra todo mundo, mas
    # agrupado por bairro, com o bairro do usuário logado aparecendo
    # primeiro — assim é mais fácil achar quem atende perto de você.
    profissionais_por_bairro = None
    bairro_usuario = ""
    if not categoria_id and not grupo and not cidade and not busca:
        grupos = {}
        for p in profissionais:
            chave = (p.bairro or "").strip() or "Bairro não informado"
            grupos.setdefault(chave, []).append(p)

        if usuario:
            if usuario.bairro:
                bairro_usuario = usuario.bairro.strip()
            elif usuario.tipo == "profissional" and usuario.perfil_profissional and usuario.perfil_profissional.bairro:
                # Profissional já tem bairro cadastrado no perfil de atendimento —
                # não faz sentido pedir de novo em "Minha localização".
                bairro_usuario = usuario.perfil_profissional.bairro.strip()

        def ordem_bairro(nome):
            return (nome != bairro_usuario, nome == "Bairro não informado", nome.lower())

        profissionais_por_bairro = [(nome, grupos[nome]) for nome in sorted(grupos, key=ordem_bairro)]

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
            "profissionais_por_bairro": profissionais_por_bairro,
            "bairro_usuario": bairro_usuario,
            "categorias": categorias,
            "categorias_por_grupo": categorias_por_grupo,
            "filtro_categoria": categoria_id,
            "filtro_grupo": grupo or "",
            "filtro_cidade": cidade or "",
            "filtro_busca": busca or "",
            "eh_admin_usuario": eh_admin(usuario),
        },
    )


# ---------------------------------------------------------------------------
# Cadastro / Login / Logout
# ---------------------------------------------------------------------------

@app.get("/comecar")
def form_comecar(request: Request, tipo: str = "cliente"):
    return templates.TemplateResponse(
        "comecar.html",
        {"request": request, "tipo": tipo if tipo in ("cliente", "profissional") else "cliente"},
    )


@app.get("/cadastro")
def form_cadastro(request: Request, tipo: str = "cliente"):
    return templates.TemplateResponse(
        "cadastro.html",
        {
            "request": request, "erro": None, "google_habilitado": google_oauth_habilitado,
            "tipo_selecionado": tipo if tipo in ("cliente", "profissional") else "cliente",
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
            },
        )

    if not EMAIL_REGEX.match(email):
        return erro("Digite um e-mail válido.")
    if db.query(models.User).filter(models.User.email == email).first():
        return erro("Já existe um cadastro com esse e-mail.")
    if db.query(models.User).filter(models.User.telefone == telefone).first():
        return erro("Já existe um cadastro com esse telefone.")

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

    request.session["user_id"] = novo_usuario.id

    if tipo == "profissional":
        return RedirectResponse("/profissional/perfil/editar", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/login")
def form_login(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "erro": None, "google_habilitado": google_oauth_habilitado},
    )


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    usuario = db.query(models.User).filter(models.User.email == email).first()

    def erro(mensagem):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "erro": mensagem, "google_habilitado": google_oauth_habilitado},
        )

    if usuario and not usuario.senha_hash:
        return erro('Essa conta usa login com Google. Clique em "Entrar com Google" abaixo.')
    if not usuario or not auth.verificar_senha(senha, usuario.senha_hash):
        return erro("E-mail ou senha incorretos.")

    if not usuario.ativo:
        return erro("Essa conta foi bloqueada. Entre em contato com o suporte.")

    request.session["user_id"] = usuario.id
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Login com Google
# ---------------------------------------------------------------------------

@app.get("/auth/google/login")
async def google_login(request: Request):
    if not google_oauth_habilitado:
        return RedirectResponse("/login", status_code=303)
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
        return RedirectResponse("/", status_code=303)

    # Conta nova via Google: ainda falta telefone (contato) e tipo de conta.
    request.session["google_pendente"] = {"email": email, "nome": nome, "google_id": google_id}
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

    del request.session["google_pendente"]
    request.session["user_id"] = novo_usuario.id

    if tipo == "profissional":
        return RedirectResponse("/profissional/perfil/editar", status_code=303)
    return RedirectResponse("/", status_code=303)


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

    tem_foto_nova = bool(foto and foto.filename)
    if not tem_foto_nova and not perfil.foto_url:
        return "Foto é obrigatória."

    if tem_foto_nova:
        if foto.content_type not in EXTENSOES_FOTO_PERMITIDAS:
            return "Formato de foto não suportado. Envie uma imagem JPG, PNG ou WEBP."
        conteudo = foto.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_FOTO:
            return "A foto é muito grande (máximo 5 MB)."
        extensao = EXTENSOES_FOTO_PERMITIDAS[foto.content_type]
        nome_arquivo = f"profissional_{perfil.usuario_id}{extensao}"
        perfil.foto_url = storage.salvar_foto(conteudo, nome_arquivo, foto.content_type)

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

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()  # e-mail do seu usuário admin, defina no .env


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

    clientes = db.query(models.User).filter(
        models.User.tipo == "cliente"
    ).order_by(models.User.criado_em.desc()).all()

    # --- Números do site ---------------------------------------------------
    sete_dias_atras = datetime.utcnow() - timedelta(days=7)
    total_avaliacoes = db.query(models.Review).count()
    media_geral = db.query(func.avg(models.Review.estrelas)).scalar()
    total_indicacoes_contatadas = db.query(models.Indicacao).filter(
        models.Indicacao.status == "contatada"
    ).count()
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
        "total_indicacoes_contatadas": total_indicacoes_contatadas,
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
