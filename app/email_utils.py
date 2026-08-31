"""
Envio de e-mails transacionais (confirmação de cadastro).

Usa a API do SendGrid (HTTPS, porta 443) em vez de SMTP tradicional — muita
hospedagem gratuita, incluindo o Render, bloqueia as portas de SMTP
(587/465/25) pra evitar spam, o que fazia o cadastro travar sem erro
nenhum aparecer.

Se SENDGRID_API_KEY ou EMAIL_FROM não estiverem preenchidos no .env, o
e-mail não é enviado de verdade: o conteúdo (com o link de confirmação) é
só impresso no terminal onde o `uvicorn` está rodando. Isso permite testar
o fluxo de cadastro completo sem precisar configurar nada primeiro.

Como configurar (gratuito, sem precisar ter domínio próprio):
1. Crie uma conta em https://signup.sendgrid.com
2. Vá em Settings -> Sender Authentication -> "Verify a Single Sender"
   e preencha com o e-mail que você quer usar como remetente (ex: seu
   Gmail). O SendGrid manda um e-mail de confirmação pra esse endereço —
   clique no link de lá pra verificar.
3. Vá em Settings -> API Keys -> "Create API Key", permissão
   "Restricted Access" com "Mail Send: Full Access"
4. Preencha no .env: SENDGRID_API_KEY (a chave gerada, começa com "SG.")
   e EMAIL_FROM (o mesmo e-mail que você verificou no passo 2)
"""
import html
import os
import httpx

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")

sendgrid_habilitado = bool(SENDGRID_API_KEY and EMAIL_FROM)


def enviar_email(destinatario: str, assunto: str, corpo_html: str) -> None:
    if not sendgrid_habilitado:
        print("=" * 70)
        print(f"[E-MAIL NÃO ENVIADO — SendGrid não configurado no .env] Para: {destinatario}")
        print(f"Assunto: {assunto}")
        print(corpo_html)
        print("=" * 70)
        return

    try:
        resposta = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": destinatario}]}],
                "from": {"email": EMAIL_FROM},
                "subject": assunto,
                "content": [{"type": "text/html", "value": corpo_html}],
            },
            timeout=10,
        )
        if resposta.status_code >= 400:
            print(f"[ERRO AO ENVIAR E-MAIL] status={resposta.status_code} resposta={resposta.text}")
    except httpx.HTTPError as e:
        print(f"[ERRO AO ENVIAR E-MAIL] {e}")


def enviar_email_confirmacao(destinatario: str, nome: str, link_confirmacao: str) -> None:
    primeiro_nome = html.escape(nome.split(" ")[0])
    link_seguro = html.escape(link_confirmacao)
    corpo = f"""
    <p>Oi, {primeiro_nome}!</p>
    <p>Confirme seu cadastro no <strong>SocorreAqui</strong> clicando no link abaixo:</p>
    <p><a href="{link_seguro}">{link_seguro}</a></p>
    <p style="color:#888;font-size:13px;">Se você não pediu esse cadastro, pode ignorar este e-mail.</p>
    """
    enviar_email(destinatario, "Confirme seu cadastro — SocorreAqui", corpo)


def enviar_email_novo_cadastro(
    destinatario_admin: str, nome: str, email: str, telefone: str, tipo: str
) -> None:
    """Avisa o admin (ADMIN_EMAIL) sempre que um cliente ou profissional novo
    se cadastra -- pra ele acompanhar o crescimento sem precisar ficar
    entrando no /admin toda hora.

    Escapa nome/email/telefone porque vêm direto do formulário de cadastro
    (usuário controla o conteúdo) -- sem isso, alguém poderia injetar HTML
    no e-mail que você recebe só preenchendo o próprio nome de um jeito
    malicioso no cadastro.
    """
    tipo_label = "Profissional" if tipo == "profissional" else "Cliente"
    nome_seguro = html.escape(nome)
    email_seguro = html.escape(email)
    telefone_seguro = html.escape(telefone)
    corpo = f"""
    <p>Novo cadastro no SocorreAqui:</p>
    <ul>
        <li><strong>Tipo:</strong> {tipo_label}</li>
        <li><strong>Nome:</strong> {nome_seguro}</li>
        <li><strong>E-mail:</strong> {email_seguro}</li>
        <li><strong>Telefone:</strong> {telefone_seguro}</li>
    </ul>
    """
    assunto = f"Novo cadastro: {tipo_label} — {nome}".replace("\r", " ").replace("\n", " ")
    enviar_email(destinatario_admin, assunto, corpo)
