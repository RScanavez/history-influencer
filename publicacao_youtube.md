# Publicação no YouTube: configuração

Guia para ligar a fase 4 do painel (publicação agendada). Feito uma única vez. Nada aqui é executado pelo
Claude: você cria a conta, baixa a credencial e autoriza o canal.

## 1. Criar o projeto no Google Cloud

1. Entre em https://console.cloud.google.com com a conta Google **dona do canal** Historiador.
2. No seletor de projetos (topo), clique em **Novo projeto**. Nome: `History-Advisor`. Crie e selecione-o.
3. Menu **APIs e serviços → Biblioteca** → procure **YouTube Data API v3** → **Ativar**.

## 2. Tela de consentimento OAuth

1. Menu **APIs e serviços → Tela de consentimento OAuth** (em versões novas: **Google Auth Platform**).
2. Tipo de usuário: **Externo**. Preencha:
   - nome do app: `History-Advisor`;
   - e-mail de suporte e e-mail do desenvolvedor: o seu.
3. **Escopos / Acesso a dados**: adicione `https://www.googleapis.com/auth/youtube.force-ssl`.
4. **Público / Usuários de teste**: adicione o seu e-mail (a conta dona do canal).
5. **Branding**: página inicial `https://sites.google.com/view/history-advisor` e política de privacidade
   `https://sites.google.com/view/history-advisor/privacy-policy` (site no Google Sites). Em **Domínios autorizados**, adicionar `google.com` (sem isso aparece
   "Domínio ausente: google.com").
6. **Status de publicação: Em produção** (botão "Publicar app"), **sem** pedir verificação. Em modo
   "Teste" o Google expira o login a cada 7 dias e os envios parariam. Em produção sem verificação, o login
   não expira; o aviso "app não verificado" aparece só na hora do login (uso pessoal).

## 3. Credencial do aplicativo

1. Menu **APIs e serviços → Credenciais** (ou **Google Auth Platform → Clientes**) → **Criar credenciais →
   ID do cliente OAuth**.
2. Tipo de aplicativo: **App para computador** (Desktop). Nome: `History-Advisor`.
3. Clique em **Baixar JSON** e salve o arquivo como:

   `D:\Historiador\secrets\client_secret.json`

   A pasta `secrets/` está no `.gitignore`: não vai para o GitHub. Não envie esse arquivo a ninguém.

## 4. Conectar o canal no painel

1. Abra o painel (`abrir-painel.cmd`). Na seção **Grade de publicação**, clique em **Conectar canal do YouTube**.
2. O navegador abre o login do Google. Escolha a conta e, se pedir, o canal Historiador.
3. Se aparecer "O Google não verificou este app", clique em **Avançado → Acessar History-Advisor (não seguro)**:
   é o seu próprio app, usado só por você.
4. Autorize. O painel passa a mostrar **Canal conectado: <nome do canal>**. O token fica em `secrets/token.json`.

Se `- auditoria:` no `fluxo_aprovacao.md` for `pendente`, nada é enviado: os vídeos aprovados ficam na fila de
publicação com o horário previsto. O valor atual é `aprovada`.
