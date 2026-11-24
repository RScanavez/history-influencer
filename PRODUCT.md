# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Um único operador: o dono do canal de YouTube "Historiador" (History-Advisor). Ele produz Shorts de história com ferramentas locais e usa o painel para decidir o que avança na produção e o que vai ao ar. Às vezes abre o painel algumas vezes ao dia só para aprovar; em outros dias deixa o painel aberto o dia todo, acompanhando a fila da GPU e a temperatura do chip. Usa sempre o navegador do Mac, nunca o celular.

## Product Purpose

O painel local (`influencer_mcp/painel.html`, servido por `scripts/painel.py` em http://127.0.0.1:8766) é o centro de controle da produção. Por ele o operador inicia lotes de vídeos, acompanha a fila da GPU, revisa as imagens-chave e os vídeos animados, refaz ou remove cenas, edita os metadados e acompanha a publicação no YouTube. O painel cumpre seu papel quando, ao abri-lo, o operador vê de imediato **o que espera a decisão dele**, e logo depois se a produção está andando.

## Operating Context

- Fluxo de cada vídeo (`fluxo_aprovacao.md`): Em produção (imagens-chave) → Revisão das imagens-chave → Na fila da animação / Animando → Revisão do vídeo → Aprovado → Agendado → Publicado. Em qualquer revisão o vídeo pode ir para Ajustes ou Rejeitado.
- A GPU é cara em tempo: aprovar as imagens-chave libera de 2 a 3 h de animação. Por isso a revisão das imagens-chave é o ponto de decisão mais importante.
- O painel se atualiza sozinho (estado a cada 30 s, medidores a cada 3 s). Pedidos de texto e de prompt continuam sendo feitos no chat do Claude Code.
- Roda em macOS com Apple Silicon (branch `mac-version`). A GPU é a do Mac, e o servidor envia sensores de temperatura, pressão de memória e swap.
- Os valores da grade de publicação (horários, fuso, auditoria, playlist, idiomas) são editados em `fluxo_aprovacao.md`, não no painel.

## Capabilities and Constraints

- Arquivo HTML único com CSS e JS embutidos, sem build e sem dependências externas. Os testes (`tests/test_painel_html.py`, com quickjs) conferem a sintaxe do JS e partes da marcação.
- Interface em português do Brasil. Idiomas dos vídeos: PT, com FR e EN opcionais.
- Ações destrutivas (excluir vídeo, rejeitar, cancelar job, arquivar, limpar idiomas) pedem confirmação, porque apagam arquivos sem volta.
- Publicação pela YouTube Data API, bloqueada até a auditoria do Google ser aprovada. O rodapé com os links dos Termos do YouTube e da Política de Privacidade do Google é exigido pela API e precisa continuar no painel.

## Brand Commitments

- Nome do canal: Historiador / History-Advisor. Conteúdo: história para adultos curiosos do Brasil e de Portugal, com um fato concreto por vídeo.

## Product Principles

1. Primeiro o que precisa de decisão, depois o andamento, depois o histórico.
2. Nenhuma ação destrutiva sem confirmação clara do que se perde.
3. O estado de cada vídeo e de cada job deve ser legível de relance, sem ler parágrafos.
4. O painel é uma ferramenta de trabalho: precisão e calma valem mais que efeito.
