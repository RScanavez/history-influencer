---
version: 1
slug: "influencer-mcp-painel-html"
primary_target: "influencer_mcp/painel.html"
related_targets: []
---

# Painel de produção (influencer_mcp/painel.html)

Scope: o painel local inteiro, incluindo a folha de revisão de vídeo. Mode: Operate.
Audience: o dono do canal, sozinho, no navegador do Mac; às vezes abre só para aprovar, às vezes deixa aberto o dia todo.
Task: decidir (aprovar imagens-chave, aprovar vídeo, pedir ajustes, rejeitar), acompanhar a produção e a publicação.
Constraints: arquivo HTML único sem dependências; JS validado por tests/test_painel_html.py; rodapé legal da YouTube API fica; ações destrutivas com confirmação; mesma API (/api/estado, /api/sistema, /api/video, /api/acao, /api/lote, /api/verificar).
Craft bar: Vercel dashboard e GitHub (escolha do usuário).

## Direction contract

THESIS: A primeira coisa na tela é a bandeja do que espera a decisão do operador; produção, computador e acervo vêm depois. Recusa a pilha de seções iguais em que tudo tem o mesmo peso.
OWN-WORLD: padrão de dashboard executado com rigor: neutros frios em claro e escuro, um azul de ação, verde, âmbar e vermelho só para estado. Sans do sistema, números tabulares, mono só para ids e slugs. Cartões com borda de 1px e raio de 8px, sem sombras decorativas; pílulas de estado com ponto colorido.
STORY: o operador entende em 2 s quantas decisões o esperam e se a GPU está trabalhando; aprova direto do cartão ou abre a revisão; acompanha a fila e a grade de publicação.
FIRST VIEWPORT: barra superior fixa com o nome, a faixa de saúde (ComfyUI, GPU, temperatura, fim da fila) e o botão "Novo lote". Abaixo, "Aguardando você (N)" em linha de cartões com miniatura, etapa e ação principal. Em seguida, duas colunas: fila da GPU à esquerda e medidores compactos à direita.
FORM: canon (saída padrão escolhida pelo usuário em vez da direção sorteada); seed:b7c1998025a1a786
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
