"""
Envio de e-mails transacionais (confirmação de cadastro).

Se as variáveis SMTP_* não estiverem preenchidas no .env, o e-mail não é
enviado de verdade: o conteúdo (com o link de confirmação) é só impresso
no terminal onde o `uvicorn` está rodando. Isso permite testar o fluxo de
cadastro completo sem precisar configurar uma conta de e-mail primeiro.
"""
import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "") or SMTP_USER

smtp_configurado = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def enviar_email(destinatario: str, assunto: str, corpo_html: str) -> None:
    if not smtp_configurado:
        print("=" * 70)
        print(f"[E-MAIL NÃO ENVIADO — SMTP não configurado no .env] Para: {destinatario}")
        print(f"Assunto: {assunto}")
        print(corpo_html)
        print("=" * 70)
        return

    msg = MIMEText(corpo_html, "html", "utf-8")
    msg["Subject"] = assunto
    msg["From"] = EMAIL_FROM
    msg["To"] = destinatario

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as servidor:
        servidor.starttls()
        servidor.login(SMTP_USER, SMTP_PASSWORD)
        servidor.sendmail(EMAIL_FROM, [destinatario], msg.as_string())


def enviar_email_confirmacao(destinatario: str, nome: str, link_confirmacao: str) -> None:
    primeiro_nome = nome.split(" ")[0]
    corpo = f"""
    <p>Oi, {primeiro_nome}!</p>
    <p>Confirme seu cadastro no <strong>Serviço na Cidade</strong> clicando no link abaixo:</p>
    <p><a href="{link_confirmacao}">{link_confirmacao}</a></p>
    <p style="color:#888;font-size:13px;">Se você não pediu esse cadastro, pode ignorar este e-mail.</p>
    """
    enviar_email(destinatario, "Confirme seu cadastro — Serviço na Cidade", corpo)
