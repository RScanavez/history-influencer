---
name: Painel Historiador
description: Painel local de produção do canal Historiador; primeiro o que espera a decisão do operador, depois o andamento.
colors:
  canvas: "#ffffff"
  subtle: "#f6f7f9"
  inset: "#eef0f3"
  line: "#d9dde3"
  line-soft: "#e8ebef"
  ink: "#1b1f24"
  muted: "#555d68"
  faint: "#656e79"
  accent: "#0b63ce"
  accent-hover: "#0a56b3"
  accent-ink: "#ffffff"
  accent-soft: "#e6f0fc"
  ok: "#1a7f37"
  ok-soft: "#e4f4e8"
  warn: "#845800"
  warn-soft: "#fbf1d9"
  bad: "#c4291c"
  bad-soft: "#fde9e7"
  need: "#6639ba"
  need-soft: "#f1ebfb"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 550
    lineHeight: 1.5
  label-xs:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 550
    lineHeight: 1
  mono:
    fontFamily: "ui-monospace, SF Mono, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  full: "999px"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
components:
  button:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-hover:
    backgroundColor: "{colors.subtle}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-danger-hover:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad}"
  button-sm:
    typography: "{typography.label}"
    padding: "0 9px"
    height: "26px"
  input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "6px 10px"
    height: "34px"
  pill:
    backgroundColor: "{colors.inset}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 9px"
    height: "22px"
  pill-need:
    backgroundColor: "{colors.need-soft}"
    textColor: "{colors.need}"
  pill-run:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  pill-ok:
    backgroundColor: "{colors.ok-soft}"
    textColor: "{colors.ok}"
  pill-warn:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn}"
  pill-bad:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad}"
  count:
    backgroundColor: "{colors.inset}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 7px"
    height: "20px"
  count-need:
    backgroundColor: "{colors.need}"
  panel:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.md}"
    padding: "16px"
  decision-card:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.md}"
    padding: "12px 14px 14px"
  sheet:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.lg}"
    padding: "20px"
  tab:
    textColor: "{colors.muted}"
    typography: "{typography.body-sm}"
    padding: "8px 10px 9px"
  tab-active:
    textColor: "{colors.ink}"
---

# Design System: Painel Historiador

## Overview

**Creative North Star: "A Bandeja de Decisões"**

O painel é um dashboard da categoria executado com rigor, no padrão de precisão do Vercel e do GitHub: neutros frios, um único azul de ação, e cor saturada reservada a estado. A primeira coisa na tela é a bandeja "Aguardando você", e tudo o mais (fila da GPU, medidores do computador, vídeos, publicação) vem depois, com peso visual menor. O sistema recusa a pilha de seções iguais: a hierarquia vem da ordem, de um roxo de decisão que só aparece onde o operador precisa agir, e da contagem ao lado de cada título.

A densidade é de ferramenta de trabalho: corpo de 14px, tabelas e botões em 13px, rótulos em 12px, sem tipografia de vitrine. As superfícies são planas, separadas por bordas de 1px e por três tons de neutro (canvas, subtle, inset). Sombra aparece só como resposta: ao passar o mouse sobre um cartão clicável, e nas camadas que flutuam (folha de revisão, aviso). O mesmo sistema existe em claro e escuro, trocado por `prefers-color-scheme`, com os mesmos nomes de token.

O movimento é discreto e funcional: transições de cor e borda de 0,15 s, uma curva de desaceleração única para o que entra ou cresce, e um pulso lento no ponto das pílulas "em andamento". Com `prefers-reduced-motion`, tudo para.

**Key Characteristics:**
- Neutros frios em três camadas (canvas, subtle, inset) com bordas de 1px; nada de sombra em repouso.
- Um azul de ação; verde, âmbar e vermelho só para estado; roxo só para "espera a sua decisão".
- Fonte do sistema em todos os papéis de texto; mono só para ids, slugs, lotes e logs; números sempre tabulares.
- Pílulas de estado com ponto colorido de 6px, raio total.
- Claro e escuro com paridade de tokens.

## Colors

Uma paleta de dashboard contida: neutros frios carregam a estrutura, e cada cor saturada tem um único significado de estado.

### Primary
- **Azul de Ação** (`accent`): botão primário ("Novo lote", "Revisar imagens", "Aprovar"), links, anel de foco (`outline` 2px), sublinhado da aba ativa, preenchimento dos medidores e estado "rodando/em andamento" nas pílulas `run`. Hover escurece para `accent-hover`. No escuro: #4c95f5, hover #6aa8f7, e o texto sobre ele (`accent-ink`) vira quase preto #0b0f14.
- **Azul Lavado** (`accent-soft`): fundo da pílula `run`, halo de foco de 3px nos campos, fundo da seleção de texto e da cena selecionada (misturado a 45% com o canvas). No escuro: #13253d.

### Tertiary (papéis de estado)
- **Roxo de Decisão** (`need`, com `need-soft` de fundo): o estado "espera a decisão do operador". Marca as pílulas das etapas "Revisão das imagens-chave" e "Revisão do vídeo" e a contagem de "Aguardando você" quando há pendências. É um papel deliberado, escolhido para não competir com o azul de ação nem ser confundido com alerta. No escuro: #b294f2 sobre #251c38.
- **Verde Concluído** (`ok`, `ok-soft`): aprovado, publicado, canal conectado, cena animada, ComfyUI no ar, bandeja vazia. No escuro: #4cc06a sobre #142a1b.
- **Âmbar Atenção** (`warn`, `warn-soft`): pausado, aguardando GPU livre, ajustes, SEO na fila, auditoria pendente, avisos de ambiente. No escuro: #e3a93a sobre #2d2412.
- **Vermelho Falha** (`bad`, `bad-soft`): falhou, rejeitado, erro de envio, temperatura acima do limite, marca de limite nos medidores, hover dos botões destrutivos. No escuro: #f2776b sobre #321716.

### Neutral
- **Canvas** (`canvas`): fundo de painéis, cartões, barra superior, folha de revisão e campos. No escuro: #0f1216.
- **Fundo Sutil** (`subtle`): fundo da página, cabeçalho das tabelas, hover de botão, fundo dos logs. No escuro: #161a20.
- **Embutido** (`inset`): pílulas e contagens neutras, trilho dos medidores, fundo de miniaturas sem imagem, `code` inline, hover de aba. No escuro: #1d222a.
- **Linha** (`line`): borda de painéis, cartões, botões, campos e da barra superior. No escuro: #2e353f.
- **Linha Suave** (`line-soft`): divisórias internas (linhas de tabela, base das miniaturas, grade de publicação). No escuro: #242a33.
- **Tinta** (`ink`): texto principal; também o fundo do aviso flutuante (toast), com texto em `canvas`. No escuro: #e9edf2.
- **Tinta Secundária** (`muted`): textos `.small`, rótulos de campo, cabeçalhos de tabela, texto de pílula neutra. No escuro: #a3acb7.
- **Tinta Fraca** (`faint`): placeholders, carimbo "Atualizado", índice da fila, borda no hover de cartões e botões. No escuro: #8b949f.

Camadas fora da paleta: o véu da folha de revisão (`--overlay`, rgba(15,20,28,.5); no escuro rgba(0,0,0,.62)) e o fundo preto (#000) atrás dos players de vídeo.

### Named Rules
**A Regra do Estado que Fala.** Verde, âmbar, vermelho e roxo existem só para estado. Nunca decoram, nunca marcam seção, nunca dão "personalidade". Se não há estado a comunicar, o elemento é neutro.

**A Regra do Roxo de Decisão.** `need` marca uma coisa só: um item que está parado esperando o operador decidir. Não é cor de marca, de destaque nem de novidade. Se o operador não tem nada a fazer ali, não há roxo na tela.

**A Regra do Azul Único.** Existe um azul, e ele significa ação (ou trabalho em andamento). Um segundo azul, ou um gradiente, quebra a leitura.

## Typography

**Display Font:** nenhuma (o sistema não tem papel de display)
**Body Font:** fonte do sistema (-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif)
**Label/Mono Font:** ui-monospace (SF Mono, Menlo, Consolas)

**Character:** a tipografia de ferramenta do próprio sistema operacional, com pesos intermediários (550, 650) que dão firmeza sem gritar. A hierarquia vem de peso e posição, não de tamanho: o maior texto da tela tem 18px.

### Hierarchy
- **Headline** (650, 18px, 1.3, -0.01em): títulos de bloco ("Aguardando você", "Produção", "Computador") e o título da folha de revisão.
- **Title** (650, 15px): nome "Historiador" na barra superior e o valor numérico dos medidores. Títulos de cartão usam 600 a 14px, com corte em duas linhas.
- **Body** (400, 14px, 1.5): texto base da página e dos campos.
- **Body pequeno** (400, 13px): tabelas, botões (em 550), avisos, faixa de saúde, textos auxiliares `.small` em `muted`.
- **Label** (550, 12px): pílulas, contagens (600), rótulos de campo, cabeçalhos de tabela (600), botões pequenos, rodapé.
- **Label mínimo** (11px): chips de idioma e contagens dentro das abas. Nada abaixo disso.
- **Mono** (12px): ids de job, slugs, nomes de lote, logs (12px/1.5), número da cena (600).

### Named Rules
**A Regra dos Números Tabulares.** Toda contagem, hora, duração, porcentagem e temperatura usa `font-variant-numeric: tabular-nums`, para que os valores não dancem a cada atualização de 3 s.

**A Regra do Mono Restrito.** Mono é para identificadores que o operador copia ou compara (lote, slug, id de job, log). Títulos, rótulos e números de medição ficam na fonte do sistema.

## Layout

Coluna única centrada com largura máxima de 1280px e margens laterais de 24px (16px abaixo de 640px). A barra superior é fixa (`position: sticky`), com o nome à esquerda, a faixa de saúde (ComfyUI, fila, GPU, temperatura) e o botão "Novo lote", quebrando linha quando falta espaço.

A página é uma sequência de blocos com 32px entre eles; dentro do bloco, 12px entre o cabeçalho (título + contagem + ações à direita) e o conteúdo. A ordem é fixa e é a hierarquia: novo lote (oculto até ser pedido), Aguardando você, avisos, depois a divisão Produção (2fr) e Computador (1fr, mínimo 280px), depois Vídeos, Publicação e Falhas recentes. A divisão vira uma coluna abaixo de 960px.

Grades automáticas (`auto-fill`) com 12px de espaço: cartões de decisão com mínimo de 290px, cartões de vídeo com 230px, cenas com 165px. A folha de revisão abre sobre véu escuro com no máximo 1180px de largura, cabeçalho fixo com as ações de decisão, e corpo com 28px entre seções.

Ritmo de espaçamento observado: 4, 6, 8, 12, 16, 24 e 32px, com 10px e 14px como ajustes de padding interno (células de tabela, cartões).

## Elevation & Depth

O sistema é plano. A profundidade vem de tom (página em `subtle`, superfícies em `canvas`, recessos em `inset`) e de bordas de 1px. A única sombra (`--shadow`) é um par difuso e baixo, mais denso no escuro, e aparece em dois casos: como resposta ao hover de cartões clicáveis (decisão, vídeo) e em camadas que flutuam sobre o conteúdo (folha de revisão, aviso flutuante). Campos em foco ganham um halo de 3px em `accent-soft`, não uma sombra.

### Shadow Vocabulary
- **Sombra de resposta** (`box-shadow: 0 1px 2px rgba(15,23,32,.06), 0 4px 12px rgba(15,23,32,.06)`; no escuro `0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35)`): hover de cartões clicáveis, folha de revisão, aviso flutuante.
- **Halo de foco** (`box-shadow: 0 0 0 3px` em `accent-soft`): campos de formulário em foco.
- **Anel de seleção** (`box-shadow: 0 0 0 1px` em `accent`): cena selecionada, somado à borda azul.

### Named Rules
**A Regra da Sombra como Resposta.** Em repouso, nada tem sombra. A sombra só aparece quando o elemento responde ao ponteiro ou flutua sobre a página.

## Shapes

Formas retangulares com cantos suaves numa escada curta: 4px para elementos mínimos (chips de idioma, `code`, anel de foco, topo das abas), 6px para controles (botões, campos, logs, imagens dentro de cartões), 8px para contêineres (painéis, cartões, avisos, players), 12px só para a folha de revisão, e raio total para pílulas, contagens e trilhos de medidor. Bordas são sempre de 1px; a única exceção é o sublinhado de 2px da aba ativa. Miniaturas são cortadas pelo cartão (`overflow: hidden`) e ancoradas no topo da imagem.

## Components

### Buttons
Firmes e baixos, no padrão GitHub.
- **Shape:** cantos suaves (6px), altura de 32px, borda de 1px em `line`.
- **Padrão:** fundo `canvas`, texto `ink`, 13px peso 550, padding horizontal de 12px. Hover: fundo `subtle` e borda `faint`.
- **Primário:** fundo e borda `accent`, texto `accent-ink`; hover `accent-hover`. Uma ação primária por contexto (o "Novo lote" na barra, a ação principal de cada cartão de decisão, a aprovação na folha).
- **Perigo:** em repouso parece um botão padrão; no hover e no foco ganha texto `bad`, fundo `bad-soft` e borda `bad`. A variante forte ("Rejeitar", "Excluir vídeo") já mostra o texto em `bad` em repouso. Toda ação destrutiva pede confirmação.
- **Pequeno:** 26px de altura, 12px de texto, padding de 9px; usado em linhas de tabela, cabeçalhos de bloco e cenas.
- **Desabilitado / ocupado:** opacidade 0,45 com cursor `not-allowed`; ocupado (`aria-busy`) 0,7 com cursor de progresso.

### Pílulas de estado
- **Estilo:** 22px de altura, raio total, 12px peso 550, ponto de 6px na cor do texto (`currentColor`) à esquerda.
- **Variantes:** neutra (`inset`/`muted`), `run` (azul, ponto pulsando a cada 1,6 s), `ok`, `warn`, `bad` e `need` (roxo), cada uma com o tom `-soft` de fundo e o tom cheio no texto. A variante `plain` tira o ponto e serve para atributos que não são estado (formato do vídeo).

### Contagens
Selo de raio total, 20px de altura, 12px peso 600 tabular, em `inset`/`muted` ao lado de cada título de bloco e de cada aba. A contagem de "Aguardando você" passa a fundo `need` cheio quando há pendências, e o título da aba do navegador ganha o número entre parênteses.

### Cards / Containers
- **Corner Style:** 8px; folha de revisão 12px.
- **Background:** `canvas` sobre a página em `subtle`.
- **Shadow Strategy:** plana em repouso; ver Elevation & Depth.
- **Border:** 1px `line`; divisórias internas em `line-soft`.
- **Internal Padding:** painéis 16px; cartões de decisão 12px/14px; cartões de vídeo 10px/12px; folha 20px.

### Cartão de decisão (componente de assinatura)
O cartão da bandeja "Aguardando você": miniatura 16:7 da folha de imagens-chave ou de revisão, pílula `need` com a etapa e o tempo de espera, título em até duas linhas, linha de metadados (formato, cenas, duração, lote em mono), e no rodapé os chips de SEO à esquerda e o botão primário pequeno à direita ("Revisar imagens" ou "Assistir e decidir"). Quando a bandeja está vazia, um único painel com círculo verde de confirmação diz quando a próxima revisão deve chegar.

### Inputs / Fields
- **Style:** fundo `canvas`, borda 1px `line`, raio 6px, altura mínima de 34px, padding 6px/10px, texto 14px; placeholder em `faint`. Rótulo acima, 12px peso 550 em `muted`, 5px de distância.
- **Focus:** borda `accent` e halo de 3px em `accent-soft`, sem `outline`.

### Navigation (abas de filtro)
Linha de abas sobre uma borda inferior de 1px: texto 13px peso 550 em `muted`, padding 8px/10px, contagem embutida. Hover: texto `ink` e fundo `inset`. Ativa (`aria-pressed`): texto `ink` e sublinhado de 2px em `accent`.

### Medidores
Rótulo à esquerda e valor à direita (15px peso 650, tabular), trilho de 6px em `inset` com raio total, preenchimento em `accent` que cresce em 0,5 s com a curva de desaceleração. Uma marca vertical de 2px em `bad` (opacidade 0,7) indica o limite; acima dele, o medidor fica "quente" e preenchimento e valor passam a `bad`.

### Avisos
Faixas de 8px de raio com fundo `-soft` e borda na cor do estado a 35%, ícone SVG de traço (16px) na cor do estado, título em 13px negrito e ação pequena à direita. O aviso flutuante (toast) é invertido: fundo `ink`, texto `canvas`, centralizado embaixo, entra subindo 12px.

### Tabelas
13px, células de 10px, linhas separadas por `line-soft`, cabeçalho em `subtle` com 12px peso 600 em `muted`, hover de linha em `subtle` a 60%. Colunas numéricas não quebram e usam números tabulares; ações ficam alinhadas à direita.

## Do's and Don'ts

### Do:
- **Do** colocar o que espera a decisão do operador antes de qualquer outro bloco, com contagem visível no título e no título da aba do navegador.
- **Do** usar `need` (roxo) só nas etapas de revisão e na contagem de pendências; ver A Regra do Roxo de Decisão.
- **Do** comunicar estado com pílula de ponto colorido (`ok`, `warn`, `bad`, `run`, `need`), sempre com texto, nunca só com cor.
- **Do** separar superfícies com borda de 1px e com os tons `canvas`/`subtle`/`inset`.
- **Do** usar números tabulares em toda contagem, hora, duração e medição.
- **Do** definir toda cor nova como token com valor para claro e para escuro.
- **Do** usar ícones como SVG inline de traço (viewBox 16, traço 1,6 a 1,8, `currentColor`).
- **Do** respeitar `prefers-reduced-motion` desligando animações e transições.

### Don't:
- **Don't** usar verde, âmbar, vermelho ou roxo como decoração, cor de seção ou destaque de marca.
- **Don't** introduzir um segundo azul, gradientes ou cor de marca na interface do painel.
- **Don't** pôr sombra em superfície em repouso; ver A Regra da Sombra como Resposta.
- **Don't** criar um papel de display ou títulos acima de 18px; a hierarquia vem de ordem e peso.
- **Don't** usar mono em títulos, rótulos ou números de medição.
- **Don't** pôr rótulos-sobrescrito (kickers/eyebrows) acima dos títulos de bloco nem usar emoji ou glifos de texto como ícone.
- **Don't** usar texto abaixo de 11px.
- **Don't** fixar `#fff` ou outra cor literal sobre um token de estado: o texto sobre cor cheia precisa vir de um token que mantenha contraste nos dois temas.
