"""
Aplicação principal.

Para rodar localmente:

    uvicorn app.main:app --reload

Depois abra http://127.0.0.1:8000 no navegador.

Veja o LEIA-ME.md na raiz do projeto para o passo a passo completo
de instalação.
"""
import os
import re
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app.database import Base, engine, get_db
from app import models, auth, email_utils, storage
from app.oauth import oauth, google_oauth_habilitado

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Cria as tabelas automaticamente se ainda não existirem
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Serviço na Cidade")

# Chave usada para assinar o cookie de sessão. Em produção, defina a
# variável de ambiente SECRET_KEY com um valor aleatório e secreto.
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-antes-de-colocar-no-ar")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

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


# ---------------------------------------------------------------------------
# Catálogo (página inicial)
# ---------------------------------------------------------------------------

@app.get("/")
def catalogo(
    request: Request,
    categoria: str | None = None,
    cidade: str | None = None,
    busca: str | None = None,
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
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

    if cidade:
        query = query.filter(models.ProfessionalProfile.cidade.ilike(f"%{cidade}%"))

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
    if not categoria_id and not cidade and not busca:
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

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "usuario": usuario,
            "profissionais": profissionais,
            "profissionais_por_bairro": profissionais_por_bairro,
            "bairro_usuario": bairro_usuario,
            "categorias": categorias,
            "filtro_categoria": categoria_id,
            "filtro_cidade": cidade or "",
            "filtro_busca": busca or "",
        },
    )


# ---------------------------------------------------------------------------
# Cadastro / Login / Logout
# ---------------------------------------------------------------------------

def _enviar_confirmacao(request: Request, usuario: "models.User"):
    token = auth.gerar_token_confirmacao(usuario.email)
    link = str(request.url_for("confirmar_email", token=token))
    email_utils.enviar_email_confirmacao(usuario.email, usuario.nome, link)


@app.get("/cadastro")
def form_cadastro(request: Request):
    return templates.TemplateResponse(
        "cadastro.html",
        {"request": request, "erro": None, "google_habilitado": google_oauth_habilitado},
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
            {"request": request, "erro": mensagem, "google_habilitado": google_oauth_habilitado},
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
        email_verificado=False,
    )
    db.add(novo_usuario)
    db.commit()
    db.refresh(novo_usuario)

    if tipo == "profissional":
        perfil = models.ProfessionalProfile(usuario_id=novo_usuario.id, cidade="")
        db.add(perfil)
        db.commit()

    _enviar_confirmacao(request, novo_usuario)

    return templates.TemplateResponse(
        "confirme_seu_email.html", {"request": request, "email": novo_usuario.email, "erro": None},
    )


@app.get("/confirmar-email/{token}")
def confirmar_email(token: str, request: Request, db: Session = Depends(get_db)):
    email = auth.verificar_token_confirmacao(token)
    if not email:
        return templates.TemplateResponse(
            "confirme_seu_email.html",
            {
                "request": request, "email": None,
                "erro": "Link inválido ou expirado. Peça um novo e-mail de confirmação abaixo.",
            },
        )

    usuario = db.query(models.User).filter(models.User.email == email).first()
    if not usuario:
        return RedirectResponse("/cadastro", status_code=303)

    usuario.email_verificado = True
    db.commit()
    request.session["user_id"] = usuario.id

    if usuario.tipo == "profissional":
        return RedirectResponse("/profissional/perfil/editar", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/reenviar-confirmacao")
def reenviar_confirmacao(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    usuario = db.query(models.User).filter(models.User.email == email).first()
    if usuario and not usuario.email_verificado:
        _enviar_confirmacao(request, usuario)
    # Mostra a mesma mensagem mesmo se o e-mail não existir, pra não revelar
    # quais e-mails estão cadastrados.
    return templates.TemplateResponse(
        "confirme_seu_email.html", {"request": request, "email": email, "erro": None},
    )


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

    if not usuario.email_verificado:
        return templates.TemplateResponse(
            "confirme_seu_email.html",
            {
                "request": request, "email": usuario.email,
                "erro": "Confirme seu e-mail antes de entrar — clique no link que te enviamos, ou peça um novo abaixo.",
            },
        )

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
# Minha localização (cidade/bairro do usuário, usada pra agrupar o catálogo)
# ---------------------------------------------------------------------------

@app.get("/minha-localizacao")
def form_minha_localizacao(request: Request, usuario=Depends(auth.usuario_logado)):
    if not usuario:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "minha_localizacao.html", {"request": request, "usuario": usuario},
    )


@app.post("/minha-localizacao")
def salvar_minha_localizacao(
    request: Request,
    cidade: str = Form(""),
    bairro: str = Form(""),
    db: Session = Depends(get_db),
    usuario=Depends(auth.usuario_logado),
):
    if not usuario:
        return RedirectResponse("/login", status_code=303)

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
    perfil = db.query(models.ProfessionalProfile).filter(
        models.ProfessionalProfile.id == profissional_id
    ).first()
    if not perfil:
        return RedirectResponse("/", status_code=303)

    avaliacoes = sorted(perfil.avaliacoes, key=lambda r: r.criado_em, reverse=True)

    minha_avaliacao = None
    if usuario and usuario.tipo == "cliente":
        minha_avaliacao = next((r for r in avaliacoes if r.cliente_id == usuario.id), None)

    return templates.TemplateResponse(
        "perfil_profissional.html",
        {
            "request": request,
            "usuario": usuario,
            "perfil": perfil,
            "avaliacoes": avaliacoes,
            "minha_avaliacao": minha_avaliacao,
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
    # Só clientes avaliam (a tela já esconde o formulário de quem não é
    # cliente, mas a checagem tem que valer aqui também).
    if not usuario or usuario.tipo != "cliente":
        return RedirectResponse("/login", status_code=303)

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

    try:
        categoria_id_int = int(categoria_id) if categoria_id else None
    except ValueError:
        categoria_id_int = None

    indicacao = models.Indicacao(
        indicado_por_id=usuario.id,
        nome_profissional=nome_profissional.strip(),
        telefone_profissional=telefone_profissional.strip(),
        categoria_id=categoria_id_int,
        cidade=cidade.strip() or None,
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
    mensagem de erro (str) se a foto for inválida, ou None se salvou certo.
    """
    if foto and foto.filename:
        if foto.content_type not in EXTENSOES_FOTO_PERMITIDAS:
            return "Formato de foto não suportado. Envie uma imagem JPG, PNG ou WEBP."
        conteudo = foto.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_FOTO:
            return "A foto é muito grande (máximo 5 MB)."
        extensao = EXTENSOES_FOTO_PERMITIDAS[foto.content_type]
        nome_arquivo = f"profissional_{perfil.usuario_id}{extensao}"
        perfil.foto_url = storage.salvar_foto(conteudo, nome_arquivo, foto.content_type)

    perfil.cidade = cidade.strip()
    perfil.bairro = bairro.strip()
    perfil.endereco = endereco.strip()
    perfil.atende_domicilio = bool(atende_domicilio)
    perfil.descricao = descricao.strip()
    perfil.valor_mao_de_obra = valor_mao_de_obra.strip()
    perfil.whatsapp = whatsapp.strip()

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
