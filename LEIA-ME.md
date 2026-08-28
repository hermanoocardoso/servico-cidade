# SocorreAqui — MVP

> Nome anterior do projeto: "Serviço na Cidade" (a pasta/repositório no
> disco e no GitHub continua se chamando `servico-cidade` por enquanto —
> só o nome de marca visível pro usuário mudou).

Esqueleto funcional do app de catálogo de profissionais autônomos:
cadastro de profissional, cadastro de cliente, catálogo com busca e
filtro, perfil público com contato via WhatsApp, e sistema de
avaliação por estrelas. Já testei o fluxo completo (cadastro →
aprovação → aparecer no catálogo → avaliação) e está funcionando.

## 1. O que já funciona

- Cadastro de **cliente** e de **profissional** (mesma tela, escolhe o tipo)
- Login por **e-mail + senha, com confirmação por e-mail** (a conta só
  fica ativa depois que a pessoa clica no link que o app manda), ou
  **login com Google** (opcional — veja a seção 3)
- Indicação de profissional: qualquer usuário logado pode sugerir alguém
  que ainda não está cadastrado; a indicação aparece no painel `/admin`
- Profissional monta o perfil: categorias, cidade/bairro, foto, valor
  da mão de obra, descrição, WhatsApp de contato
- **Aprovação manual**: todo profissional novo entra "pendente" e só
  aparece no catálogo depois que você aprova no painel `/admin` —
  isso evita perfil falso ou spam logo de cara
- Catálogo com busca por nome/categoria e filtro por cidade
- Perfil público com botão de WhatsApp e telefone
- Avaliação por estrelas (1 a 5) + comentário, só clientes logados podem
  avaliar (reavaliar atualiza a nota, e dá pra excluir a própria avaliação)
- Nota média calculada automaticamente e catálogo ordenado pelos
  melhores avaliados primeiro

## 2. Como rodar na sua máquina

Pré-requisito: Python 3.10 ou mais novo instalado.

```bash
# 1. Entrar na pasta do projeto
cd servico-cidade

# 2. Criar um ambiente virtual (recomendado, mas opcional)
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# 3. Instalar as dependências
pip install -r requirements.txt

# 4. Copiar o arquivo de configuração de exemplo
copy .env.example .env        # Windows
# cp .env.example .env        # Mac/Linux

# 5. Popular o banco com as categorias iniciais (Eletricista, Vidraceiro...)
python -m app.seed

# 6. Subir o servidor
uvicorn app.main:app --reload
```

Depois abra **http://127.0.0.1:8000** no navegador. Funciona também
pelo celular se você estiver na mesma rede Wi-Fi (usando o IP da sua
máquina em vez de 127.0.0.1).

Ao se cadastrar com e-mail e senha, o app manda um e-mail de confirmação.
**Se você ainda não configurou o envio de e-mail** (seção 3.2), esse
e-mail não sai de verdade — o link de confirmação aparece impresso no
terminal onde o `uvicorn` está rodando. Copie esse link e cole no
navegador pra ativar a conta.

## 3. Configurações de login

### 3.1 Como virar admin (aprovar profissionais)

1. Cadastre-se normalmente pelo site com seu e-mail e confirme a conta
2. Abra o arquivo `.env` e coloque esse mesmo e-mail em `ADMIN_EMAIL`
   (exatamente como você digitou no cadastro)
3. Reinicie o servidor
4. Acesse **http://127.0.0.1:8000/admin** — só esse e-mail consegue ver essa página

### 3.2 Enviar o e-mail de confirmação de verdade

Usamos a API do **SendGrid** (via HTTPS) em vez de SMTP tradicional —
muita hospedagem gratuita, incluindo o Render, bloqueia as portas de SMTP
(587/465/25) pra evitar spam, o que faz o cadastro travar sem nenhum erro
aparecer.

1. Crie uma conta gratuita em https://signup.sendgrid.com
2. Vá em **Settings → Sender Authentication → "Verify a Single Sender"**
   e preencha com o e-mail que vai aparecer como remetente (não precisa
   ter domínio próprio, dá pra usar seu Gmail mesmo) — confirme clicando
   no link que o SendGrid manda pra esse endereço
3. Vá em **Settings → API Keys → "Create API Key"**, com permissão
   "Restricted Access" → "Mail Send: Full Access"
4. Preencha no `.env`: `SENDGRID_API_KEY` (a chave gerada, começa com
   `SG.`) e `EMAIL_FROM` (o mesmo e-mail verificado no passo 2)

Se você tiver (ou comprar) um domínio próprio, dá pra trocar pelo
**Resend** com domínio verificado — entrega ainda melhor, mas exige
configurar registros DNS do domínio.

### 3.3 Ativar "Entrar com Google"

1. Vá em https://console.cloud.google.com/apis/credentials, crie um
   "OAuth Client ID" do tipo **Web application**
2. Em "URIs de redirecionamento autorizados", cadastre
   `http://127.0.0.1:8000/auth/google/callback` (e depois, quando
   colocar no ar, adicione também a URL de produção com o mesmo caminho)
3. Copie o **Client ID** e o **Client Secret** gerados para
   `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` no `.env`
4. Reinicie o servidor — o botão "Entrar com Google" aparece
   automaticamente nas telas de login e cadastro

Se deixar essas duas variáveis em branco, o botão simplesmente não
aparece — nada quebra.

## 4. Estrutura do projeto

```
servico-cidade/
├── app/
│   ├── main.py         → todas as rotas (páginas) da aplicação
│   ├── models.py       → tabelas do banco (usuários, profissionais, avaliações...)
│   ├── database.py     → conexão com o banco de dados
│   ├── auth.py         → senha/hash e token de confirmação de e-mail
│   ├── email_utils.py  → envio do e-mail de confirmação (ou impressão no terminal, se SMTP não configurado)
│   ├── oauth.py         → configuração do login com Google
│   ├── seed.py         → categorias iniciais (edite a lista aqui!)
│   ├── templates/       → as páginas HTML
│   └── static/          → CSS e fotos enviadas pelos profissionais
├── requirements.txt
├── .env.example
└── LEIA-ME.md
```

## 5. O que ajustar antes de divulgar de verdade

- **Categorias**: edite a lista `CATEGORIAS_PADRAO` em `app/seed.py`
  para bater com o que faz sentido na sua cidade
- **Nome/marca**: já está como "SocorreAqui" em `app/templates/base.html`,
  `landing.html` e `email_utils.py` — troque nesses arquivos se decidir
  mudar de novo
- **SECRET_KEY**: troque o valor no `.env` por um texto aleatório antes
  de colocar no ar publicamente (essa chave protege o login das pessoas)
- **Banco de dados**: por padrão usa SQLite (um arquivo `servico_cidade.db`
  que é criado sozinho). Isso é ótimo pra testar e validar a ideia. Quando
  for pra produção com mais gente usando ao mesmo tempo, troque pelo
  PostgreSQL só mudando a variável `DATABASE_URL` no `.env` — nenhum
  código precisa mudar.

## 6. Como colocar no ar (hospedar de verdade)

Pra sair do "só funciona na minha máquina" e virar um site que as
pessoas acessam pelo celular, as opções mais simples e baratas são:

- **Railway** ou **Render**: você sobe o código (ex: via GitHub) e eles
  cuidam do servidor e do banco PostgreSQL pra você. Plano gratuito
  ou bem barato é suficiente pra começar numa cidade.
- Depois de rodando, dá pra comprar um domínio (ex: `servicosmacae.com.br`)
  e apontar pra lá.

Posso te ajudar com esse deploy quando você estiver pronto pra colocar
no ar — é só avisar.

## 7. Ideias para depois do MVP validar

Não implementei agora de propósito, pra você lançar rápido e testar
com gente de verdade primeiro. Mas são os próximos passos naturais:

- Notificar profissional (WhatsApp/e-mail) quando alguém pede orçamento
- Cliente poder pedir orçamento estruturado (descrição + fotos do problema)
  em vez de só ir direto pro WhatsApp
- Múltiplas fotos por profissional (galeria de trabalhos)
- Virar PWA (instalar o site na tela inicial do celular como se fosse app)
- App nativo (Android/iOS) — só faz sentido depois que a versão web
  já provou demanda na sua cidade
