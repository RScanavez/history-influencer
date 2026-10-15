# Fluxo de aprovação

Este arquivo define como um vídeo vai do lote à publicação. O painel local (`python scripts/painel.py`,
http://127.0.0.1:8766) relê este arquivo a cada atualização. Edite os valores das listas `- chave: valor`;
o texto em volta é só explicação.

## Etapas

1. **Em produção (imagens-chave):** o `/gerar-lote` pesquisa, escreve o roteiro e gera as imagens-chave.
2. **Revisão das imagens-chave:** você confere a folha de imagens-chave antes de gastar 2 a 3 h de GPU.
   Aprovar coloca a animação na fila; uma cena ruim pode ser refeita ou removida.
3. **Na fila da animação / Animando:** a GPU anima um vídeo por vez. Ao terminar, monta PT, FR e EN.
4. **Revisão do vídeo:** você assiste às três versões e confere título e descrição.
5. **Aprovado:** o vídeo entra no próximo horário livre da grade abaixo.
6. **Agendado:** enviado ao YouTube com data de publicação.
7. **Publicado.**

Em qualquer revisão, o vídeo pode ir para **Ajustes** (refazer cenas, editar textos) ou **Rejeitado**.

## Checklist de revisão das imagens-chave

- uma pessoa só, rosto visível, personagem consistente (mesma roupa em todas as cenas)
- a imagem mostra o lugar, o objeto ou a situação histórica que a fala descreve, sem anacronismos grosseiros
- mãos visíveis e fora dos bolsos, sem lápis, papel ou óculos
- quando houver movimento, a imagem já mostra a posição que a fala descreve
- nada perigoso (em pé sobre bola, posição instável)

## Checklist de revisão do vídeo

- movimento corresponde à fala em todas as cenas, sem deformações
- áudio PT, FR e EN sem cortes; fala termina dentro de cada cena
- título e descrição fiéis ao vídeo, sem promessas

## Grade de publicação

- horarios: 09:00, 12:00, 15:00, 18:00
- agendar-apos-min: 20
- fuso: +01:00
- idiomas: pt
- biblioteca-exercicios: não
- auditoria: pendente
- videos-por-dia: sem limite
- aprovados-desde: 2026-10-15T00:00:00+01:00
- playlist:
- playlist-ativa: não
- comentario-automatico-desde: 2026-10-15T00:00:00+01:00

Projeto Historiador (2026-10-15): valores iniciais. `auditoria: pendente` até o Google aprovar o projeto OAuth do novo
canal (`publicacao_youtube.md`); sem isso nada é enviado. Playlist vazia até existir uma no canal.

Biblioteca de exercícios (desde 2026-10-08): `biblioteca-exercicios: não` desliga o uso da biblioteca em vídeos NOVOS
(os resultados não estavam bons). As cenas voltam a ser imagem e movimento por texto, sem esqueleto nem verificação, e
a fala deixa de ficar limitada aos exercícios da biblioteca. Vídeos já feitos e as cenas refeitas deles não mudam.
Para religar, troque por `sim`.

Idiomas (desde 2026-10-07): `idiomas: pt` entrega só o português; o projeto não monta nem pede SEO de francês e inglês.
Para voltar a gerar as versões extras, liste-as (`idiomas: pt, fr, en`). Vídeos já montados em FR/EN continuam em output/. Idioma fora da lista não gera SEO, tradução, vídeo nem capa; as ferramentas `save_french_version`/`save_english_version` e o botão de remontar recusam, e o arquivamento de publicados ignora FR/EN.

Como funciona o envio (fase 4):

- Desde 2026-10-10 (pedido do usuário): grade de 13 horários por dia (desde 2026-10-14, pedido do usuário; antes 22), no horário local do usuário (`fuso: +01:00`, corrigido em 2026-10-10;
  antes estava em Brasília e os vídeos apareciam 4 h depois no Studio). Ao clicar em
  "Aprovar vídeo", o Short sobe na hora como privado e fica agendado no primeiro horário livre da grade que esteja
  a pelo menos 15 minutos no futuro; ocupados os horários do dia, passa para o dia seguinte com a mesma lógica.
  `agendar-apos-min` só vale no modo `imediato`. Para voltar a publicar 20 minutos depois da aprovação, troque a
  grade por `horarios: imediato`.
- Desde 2026-10-04 (`horarios: imediato` + `agendar-apos-min: 20`): ao clicar em "Aprovar vídeo" num Short, o
  painel envia o vídeo na hora, como privado agendado para 20 minutos depois da aprovação (dá tempo de conferir no
  YouTube Studio). Se o envio atrasar e esse horário já tiver passado, agenda para 20 minutos depois do envio.
  Com `agendar-apos-min: 0`, vai ao ar público na hora. Vídeos horizontais continuam com publicação manual. A cota da API (10.000/dia, 1.600 por envio) permite
  uns 6 envios por dia; acima disso o envio fica em erro e é tentado de novo a cada 30 min até a cota renovar.
  Para voltar à grade, troque por horários (ex.: `horarios: 09:00, 12:00, 15:00, 18:00`), que seguem as regras abaixo.
- Com grade de horários: ao clicar em "Aprovar vídeo", ele entra na fila de publicação, na ordem de aprovação,
  e ganha o próximo horário livre da grade que esteja a pelo menos 15 minutos no futuro.
- O painel envia o vídeo ao YouTube como privado com data de publicação (`publishAt`); o próprio YouTube
  o publica no horário, mesmo com o PC desligado. Desde 2026-10-10 não há limite de envios por 24 h: o vídeo sobe
  na hora da aprovação; se a cota da API estourar, o envio fica em erro e é tentado de novo a cada 30 min.
- Nada é enviado enquanto `auditoria` for `pendente`. Troque para `aprovada` quando o Google aprovar a
  auditoria do projeto da YouTube Data API (antes disso os envios ficariam presos como privados).
- `aprovados-desde`: só vídeos aprovados no painel a partir desse instante são enviados (data e hora com fuso).
  Aprovações anteriores não contam: para enviar um vídeo aprovado antes, aprove de novo no painel. Sem essa
  linha, ou com valor inválido, nada é enviado.
- `playlist`: nome exato da playlist do canal onde entram os vídeos em português enviados pelo painel.
  `playlist-ativa: sim` liga a inclusão (decisão de 2026-10-02: só depois que o Google aprovar a auditoria, porque
  `playlistItems.insert` não estava no pedido). Ao ligar, os vídeos já enviados que faltam entram juntos,
  inclusive os cancelados (privados).
- `fuso` é um deslocamento fixo em relação ao UTC (sem horário de verão automático): se o seu relógio mudar de
  hora (ex.: fim do horário de verão europeu), troque o `fuso` à mão.
- Fixos no código (`influencer_mcp/youtube_publish.py`): categoria Guias e estilo, idioma pt-BR, não é para
  crianças, conteúdo sintético declarado, inscritos notificados, descrição igual à do `youtube-metadata.txt`.
- Depois de enviado, "Cancelar publicação" deixa o vídeo privado no YouTube (não apaga).
- `comentario-automatico-desde` (decisão de 2026-10-04): o comentário sugerido pelo SEO é postado sozinho assim que
  o vídeo vai ao ar (o YouTube não aceita comentário em vídeo privado), só nos enviados a partir desse instante; se
  falhar, tenta de novo a cada 10 min (até 5 vezes). Sem a linha, só pelo botão. Fixar continua pelo YouTube
  Studio: a API não permite fixar comentário. Desde 2026-10-06 o comentário é um só: a pergunta do SEO e, abaixo,
  o lembrete "Não se esqueça de seguir nosso canal e ativar o sininho..." (sem pergunta, só o lembrete). Vídeos antigos
  sem comentário ficam em `generated/comentarios-pendentes.json` e saem aos poucos (até 60 por dia, um por minuto). A API também não permite "Vídeo relacionado" dos Shorts.
- Playlist ligada em 2026-10-04 (decisão do usuário).
- `videos-por-dia` (decisão de 2026-10-06, depois da análise do canal): máximo de vídeos novos pedidos por dia no
  painel (soma dos lotes iniciados no dia). Com 10 a 14 vídeos por dia, cada um recebia ~5 vezes menos visualizações.
  Em 2026-10-07 o usuário tirou o limite (`sem limite`; também vale `ilimitado` ou `0`). Para voltar, ponha um número.
