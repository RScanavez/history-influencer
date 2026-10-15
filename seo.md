# Regras de SEO

Regras dos metadados de publicação (título, descrição, hashtags, tags e comentário fixado) e das traduções.
Quem segue: o Claude do `/gerar-lote` (desde 2026-10-14 o SEO vai dentro do roteiro, no objeto `seo`, e o sistema
salva os metadados ao aceitar o roteiro) e o `/seo-video` do painel (vídeos antigos sem SEO). Sem pesquisa: nada de
YouTube, Analytics ou web (decisão do usuário, 2026-10-14); tudo sai do roteiro e do conhecimento do Claude.
O código só garante os limites técnicos (título até 100 caracteres, descrição até 5.000, até 20 hashtags sem
espaço nem repetição, tags até 500 caracteres no total); o resto vale por estas regras.

## Público

- Canal Historiador: adultos curiosos
  por história, Brasil e Portugal; um episódio verificável por vídeo.
- Título, descrição e comentário fixado em português neutro (em FR, franceses; em EN, americanos), sem gírias.
- Hashtags e tags de busca: maior alcance orgânico possível entre quem procura história no YouTube.
- Título: comece pelo nome, lugar ou objeto que as pessoas digitam no YouTube para esse episódio (o termo comum, não
  o acadêmico).
- Comentário fixado: a MESMA pergunta que o vídeo faz numa cena do meio (ex.: "Você consegue? Responde 1, 2 ou 3"),
  curta e fácil de responder. O sistema acrescenta embaixo o lembrete de seguir o canal.

## Conteúdo

Base: pesquisa de 2026-10-04 (algoritmo dos Shorts, guias de SEO de 2026 e dados do próprio canal).

- Título: a palavra-chave principal logo no início, com até 60 caracteres de preferência e 65 no máximo (o código
  recusa mais: títulos acima de 70 saem cortados no celular), claro e fiel ao vídeo. Padrão: **pergunta ou detalhe
  surpreendente + o fato**, ex.: "Por que Roma tinha 8 dias por semana?", "O navio que afundou com o ouro de Portugal",
  "A primeira foto do Brasil tem 2 segundos de exposição". Sempre fiel ao conteúdo; clickbait enganoso é penalizado.
- Nomes, datas e lugares no título e nas tags, do jeito que se escreve nos livros didáticos ("Dom Pedro I",
  "Revolução Francesa", "Império Romano").
- Descrição: a primeira frase repete a palavra-chave e resume o benefício (ela aparece como trecho na busca).
  Depois, o que a pessoa vai aprender, em frases naturais com variações da busca (o YouTube transcreve a
  narração e entende o assunto; repetir palavras não ajuda).
- Comentário fixado sugerido: uma pergunta simples e direta, fácil de responder em uma palavra ou número
  ("Quantas vezes você conseguiu? 👇"); comentários pesam na distribuição e o canal ainda não recebe nenhum.
- Não repita palavras-chave artificialmente, não use clickbait enganoso e não afirme como certo o que é
  controverso entre historiadores (diga "segundo a tradição", "há debate").
- A assinatura com o Instagram do canal é acrescentada pelo sistema NO FIM da descrição (desde 2026-10-14; antes
  abria a descrição e era o que aparecia na busca em vez da palavra-chave); não a escreva. A primeira linha da
  descrição é sua e precisa trazer a palavra-chave.

## Hashtags

- Exatamente 5 por vídeo e por idioma (decisão de 2026-10-04: listas longas parecem spam; antes eram 20), sem
  espaços, sem acentos problemáticos e sem repetição. O código recusa mais de 5.
- Escolha, com base no assunto do vídeo e no seu conhecimento das hashtags mais usadas em português (sem consultar
  nada), as 5 de MAIOR ALCANCE possível: em geral 1 ampla do nicho (#saude, #bemestar, #vidaativa), 1 do público
  (#depoisdos60, #terceiraidade, #maturidade, #menopausa ou #andropausa quando o tema permitir) e 3 do assunto
  específico (#equilibrio, #exerciciosemcasa, #forca...).
- As três mais fortes primeiro: o YouTube mostra as três primeiras acima do título.
- Não use `#Shorts` (decisão de publicação do usuário).
- Toda hashtag precisa ter relação real com o vídeo: o YouTube pode remover vídeos com hashtags enganosas ou sem
  relação, e ignora todas se passarem de 60.
- Tags de busca: mesmo público, em frases que as pessoas digitam.

## Traduções (francês e inglês)

- Esta seção só vale para idiomas ativos na linha `- idiomas:` de `fluxo_aprovacao.md` (hoje `pt`: NÃO produza francês nem inglês; as ferramentas recusam). Idioma ativo: francês da França (`_FRA`) ou inglês americano (`_ENG`), mesmo vídeo, só a voz muda.
- Francês: tratamento "vous", termos usados na França, sem traduzir ao pé da letra.
- Inglês: termos usados nos EUA, tom direto e caloroso, sem traduzir ao pé da letra.
- Traduza título, descrição, hashtags, tags, comentário fixado e a narração de cada cena (`scene` igual ao número
  da cena no roteiro).
- Cada narração traduzida com tamanho parecido com a original (ela precisa caber no mesmo trecho do vídeo) e
  números por extenso.
- Gênero de quem narra: o do personagem do vídeo (`visualProfile` do roteiro; o campo `genero` dele está em
  `personagens.md`). Personagem feminino: feminino quando ela fala de si ("je suis prête"); masculino: "je suis prêt".

## Vídeo horizontal (`"format": "video"` no roteiro)

- Título e descrição pensando em busca de vídeo comum.
- Nas versões em francês e inglês (só se ativas), inclua `thumbnail_text` (2 a 5 palavras, tradução do `thumbnailText` do
  roteiro) para a capa daquele idioma.

## Publicação

- O agente nunca publica, agenda nem autentica em conta do YouTube. Quem envia é o painel, depois que o usuário
  aprova o vídeo.
