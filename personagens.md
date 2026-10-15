# Personagens

Personagens dos vídeos (o estilo do desenho, o modelo e o LoRA vêm da identidade visual do personagem, em
`identidade_visual.md`). O código lê este arquivo a cada job (`influencer_mcp/perfis.py`). Os personagens com
`selecionavel: sim` aparecem no seletor "Personagem" do Novo lote, no painel, quando a identidade deles está escolhida.

Como editar:
- Cada personagem é uma seção `## Nome` com linhas `- chave: valor` (uma linha por chave; valor vazio é permitido).
- Personagem novo: copie uma seção e troque o `id` (minúsculas e hífens), o nome e os textos.
- Cada vídeo guarda o `id` do personagem com que foi criado: não apague nem renomeie o `id` de quem já tem vídeos.
- Textos de imagem (`personagem`, `negativo`, `olhar`, `uma-pessoa`) em inglês, que é o que o gerador entende.

Chaves:
- `identidade`: id da identidade visual em `identidade_visual.md` (vazio = `aquarela`).
- `nome`: como aparece no painel. `genero`: masculino ou feminino (concordância da narração).
- `estilo`, `lora`, `lora-forca`, `lora-gatilho`, `negativo-global`, `video-estilo`, `video-negativo`, `modelo`: opcionais;
  se presentes, valem no lugar dos da identidade (só para este personagem).
- `personagem`: descrição física (e a roupa, se ela for fixa; sem roupa aqui, ela vem do prompt de cada cena).
- `negativo`: termos que só este personagem precisa barrar (somados ao negativo global).
- `uma-pessoa`: reforço de uma pessoa só, usado na capa.
- `referencia`: imagem do rosto na pasta `personagens/` (vazio = rosto não fixo); `referencia-peso-rosto` (0 a 1),
  `referencia-peso-aparencia` (0 = roupa e cabelo não são copiados), `referencia-inicio` (0 a 1: em que ponto da
  geração o rosto começa a agir; mais tarde = a cena manda mais no enquadramento).
- `olhar`: opções separadas por ` | `, sorteadas por cena (vazio = sem instrução).
- `roupas`: opções separadas por ` | ` (em inglês). Cada vídeo NOVO recebe uma, diferente das dos vídeos recentes do
  personagem, e usa a mesma em todas as cenas (campo `outfit` do roteiro). Uma roupa sugerida pelo roteiro vale se
  não repetir uma recente. Vazio = a roupa vem do roteiro (ou da descrição do personagem).
- `voz-pt`, `voz-fr`, `voz-en`: vozes da Kokoro (vazio = KOKORO_VOICE, hoje pm_santa).
- `estilo-peso`: peso do estilo do desenho no prompt (vazio = sem peso; 1.3 segura o lápis/aquarela quando a cena vem primeiro).
- `prompt-primeiro`: a cena vem antes da descrição do personagem no prompt (ajuda a mostrar cenário e objetos).
- `pose-sempre`: esqueleto OpenPose em toda cena (nao = só nas cenas com repetições ou séries).

## Configuração

- padrao: aquarela-mulher

## Mulher 50+

Personagem atual (desde 2026-10-03; "Mulher 60+" até 2026-10-09, quando o público passou a ser 45+): rosto fixo pela referência (candidata 3), roupa definida no prompt de cada cena, sem instrução de olhar.

- id: aquarela-mulher
- identidade: aquarela
- nome: Mulher 50+
- genero: feminino
- selecionavel: sim
- personagem: an elegant slim 50-year-old woman with a healthy natural figure, soft feminine arms, fair skin, youthful skin for her age, well-groomed chin-length silver-gray bob haircut, refined upright posture, expressive face with a touch of humor
- negativo: (man:1.4), (beard:1.4), (young woman:1.3), (elderly:1.3), (old woman:1.3), (red hair:1.3), (blonde hair:1.3), (brown hair:1.2), (photograph:1.5), (photorealistic:1.5), (realistic skin texture:1.3), (muscular:1.5), (defined muscles:1.5), (toned arms:1.4), (veiny arms:1.4), (abs:1.4), (fitness model:1.4), (tanned skin:1.4), (back view:1.6), (from behind:1.6), (rear view:1.6), (two chairs:1.3)
- uma-pessoa: (one single woman, solo:1.4)
- referencia: aquarela-mulher.png
- referencia-peso-rosto: 0.65
- referencia-peso-aparencia: 0
- referencia-inicio: 0.25
- olhar: 
- voz-pt: ff_siwis
- voz-fr: ff_siwis
- voz-en: af_heart
- estilo-peso: 1.3
- prompt-primeiro: sim
- pose-sempre: nao

## Homem 50+

O personagem original (até 2026-10-02), com roupa fixa; cerca de 50 anos desde 2026-10-09 (público 45+; antes, 60); forte, com músculos aparentes sem exagero, desde 2026-10-09 (pedido do usuário). Vídeos antigos sem personagem indicado usam este.

- id: ilustracao-homem
- identidade: aquarela
- nome: Homem 50+
- genero: masculino
- selecionavel: sim
- personagem: a friendly fit man around 50 years old with short salt-and-pepper hair, a neatly trimmed salt-and-pepper beard and a strong, solid build: broad shoulders, firm chest and naturally defined arm muscles visible under the sleeves, the fit body of a man who trains regularly, not a bodybuilder, no glasses, wearing a fitted navy blue crew-neck t-shirt, charcoal gray athletic gym shorts without pockets and navy sneakers
- negativo: (young man:1.3), (elderly man:1.3), (skinny:1.3), (thin arms:1.3), (huge muscles:1.4), (veiny arms:1.3), (oiled skin:1.3), (long sleeves:1.4), sweater, long pants, (cargo shorts:1.4), shorts with pockets, (tank top:1.4), sleeveless shirt, hoodie, jacket
- uma-pessoa: (one single man, solo:1.4)
- referencia: 
- referencia-peso-rosto: 
- referencia-peso-aparencia: 
- referencia-inicio: 
- olhar: 
- voz-pt: 
- voz-fr: 
- voz-en: 
- estilo-peso: 1.3
- prompt-primeiro: sim
- pose-sempre: nao

## Mulher 50+ (3D)

Personagem da identidade visual 3D (teste de 2026-10-11; estilo em `identidade_visual.md`, seção "3D"). Rosto: candidata "longo 101". Desde 2026-10-11 (pedido do usuário): a roupa muda a cada Short (lista `roupas`), igual em todas as cenas do Short.

- id: 3d-mulher
- identidade: 3d
- nome: Mulher 50+ (3D)
- genero: feminino
- selecionavel: sim
- estilo-peso: 1.2
- prompt-primeiro: sim
- pose-sempre: nao
- personagem: an elegant slim 50-year-old woman with a healthy natural figure, soft feminine arms, fair skin, long straight silver-gray hair flowing past her shoulders, (long gray hair:1.4), refined upright posture, expressive face with a touch of humor
- negativo: (man:1.4), (beard:1.4), (young woman:1.3), (elderly:1.3), (old woman:1.3), (short hair:1.5), (bob haircut:1.5), (red hair:1.3), (blonde hair:1.3), (brown hair:1.2), (muscular:1.5), (defined muscles:1.5), (toned arms:1.4), (veiny arms:1.4), (abs:1.4), (fitness model:1.4), (tanned skin:1.4), (back view:1.6), (from behind:1.6), (rear view:1.6), (two chairs:1.3)
- uma-pessoa: (one single woman, solo:1.4)
- roupas: a sunny yellow short-sleeved t-shirt, teal full-length leggings and white sneakers | a coral pink short-sleeved blouse, light blue jeans and white sneakers | a mint green short-sleeved sports t-shirt, charcoal gray full-length leggings and gray running shoes | a lavender short-sleeved knit top, cream wide-leg trousers and beige flats | a terracotta orange short-sleeved t-shirt, navy full-length yoga pants and navy sneakers | a sky blue short-sleeved button-up blouse, white full-length trousers and tan sandals with straps | a raspberry red short-sleeved sports top with a round neckline, black full-length leggings and pink running shoes | a soft peach cardigan over a white short-sleeved t-shirt, light gray full-length trousers and white sneakers | an emerald green short-sleeved wrap blouse with a modest neckline, dark blue jeans and brown ankle boots | a turquoise short-sleeved polo shirt, beige full-length chino trousers and white sneakers
- referencia: 3d-mulher.png
- referencia-peso-rosto: 0.65
- referencia-peso-aparencia: 0
- referencia-inicio: 0.25
- olhar: 
- voz-pt: ff_siwis
- voz-fr: ff_siwis
- voz-en: af_heart

## Homem 50+ (3D)

Personagem da identidade visual 3D (teste de 2026-10-11). Rosto: candidata "cavaleiro 301", com cabelo grisalho pedido no prompt (a referência tem cabelo castanho). Desde 2026-10-11 (pedido do usuário): barba não é exigida no prompt e a roupa muda a cada Short (lista `roupas`), igual em todas as cenas do Short.

- id: 3d-homem
- identidade: 3d
- nome: Homem 50+ (3D)
- genero: masculino
- selecionavel: sim
- estilo-peso: 1.2
- prompt-primeiro: sim
- pose-sempre: nao
- personagem: a ruggedly handsome 50-year-old man with a noble heroic prince-knight look, strong square jaw, (silver-gray hair:1.5), mature masculine face, strong solid build with broad shoulders and naturally defined arm muscles under the sleeves, not a bodybuilder, no glasses
- negativo: (young man:1.3), (elderly man:1.3), (brown hair:1.6), (black hair:1.5), (skinny:1.3), (thin arms:1.3), (huge muscles:1.4), (veiny arms:1.3), (cargo shorts:1.4), shorts with pockets, (tank top:1.4), sleeveless shirt
- uma-pessoa: (one single man, solo:1.4)
- roupas: a fitted navy blue crew-neck t-shirt, charcoal gray athletic jogger pants and navy sneakers | a burnt orange polo shirt, khaki chino trousers and brown leather sneakers | a forest green henley shirt with short sleeves, dark blue jeans and white sneakers | a mustard yellow crew-neck t-shirt, dark gray athletic shorts without pockets and gray running shoes | a light blue short-sleeved button-up shirt, beige chino trousers and tan suede sneakers | a deep red quarter-zip athletic top with short sleeves, black track pants and red-and-white sneakers | a teal short-sleeved sports t-shirt, navy athletic shorts without pockets and white running shoes | a cream linen short-sleeved shirt, olive green trousers and brown loafers | a heather gray crew-neck t-shirt under an open plaid flannel overshirt, dark jeans and brown boots | a cobalt blue short-sleeved polo shirt, light gray jogger pants and white sneakers
- referencia: 3d-homem.png
- referencia-peso-rosto: 0.6
- referencia-peso-aparencia: 0
- referencia-inicio: 0.25
- olhar: 
- voz-pt: 
- voz-fr: 
- voz-en: 

## Mulher 50+ (Graphic novel)

Personagem da identidade Graphic novel (2026-10-13). Desde 2026-10-14 (pedido do usuário, com o público real de 55+):
aparenta mais de 60 anos, em ótima forma física; em 2026-10-15 o usuário pediu para envelhecer mais (cerca de 70).
Roupa nova a cada Short (lista `roupas`), igual em todas as cenas.
Rosto: `personagens/gn-mulher.png` (escolhido pelo usuário em 2026-10-15: `candidatos/gn-mulher-70-1.png`, versão mais
velha do rosto de 2026-10-14, com rugas na testa, pés de galinha e sulcos ao lado da boca; os rostos anteriores estão
em `anteriores/`).

- id: gn-mulher
- identidade: graphic-novel
- nome: Mulher 60+ (Graphic novel)
- genero: feminino
- selecionavel: sim
- personagem: a vibrant fit 70-year-old woman in excellent physical shape, lean toned body, toned arms and legs, upright confident posture, fair skin with visible wrinkles, crow's feet, forehead lines and nasolabial folds, silver-gray hair tied back in a low ponytail, senior face with natural smile lines, expressive face with a touch of humor
- negativo: (man:1.4), (beard:1.4), (young woman:1.5), (middle-aged woman:1.2), (smooth young skin:1.3), (frail:1.4), (hunched:1.3), (red hair:1.3), (blonde hair:1.3), (brown hair:1.2), (bodybuilder:1.5), (veiny arms:1.4), (abs:1.3), (back view:1.6), (from behind:1.6), (rear view:1.6)
- uma-pessoa: (one single woman, solo:1.4)
- roupas: a sunny yellow short-sleeved t-shirt, teal full-length leggings and white sneakers | a coral pink short-sleeved blouse, light blue jeans and white sneakers | a mint green short-sleeved sports t-shirt, charcoal gray full-length leggings and gray running shoes | a lavender short-sleeved knit top, cream wide-leg trousers and beige flats | a terracotta orange short-sleeved t-shirt, navy full-length yoga pants and navy sneakers | a sky blue short-sleeved button-up blouse, white full-length trousers and tan sandals with straps | a raspberry red short-sleeved sports top with a round neckline, black full-length leggings and pink running shoes | a soft peach cardigan over a white short-sleeved t-shirt, light gray full-length trousers and white sneakers | an emerald green short-sleeved wrap blouse with a modest neckline, dark blue jeans and brown ankle boots | a turquoise short-sleeved polo shirt, beige full-length chino trousers and white sneakers
- referencia: gn-mulher.png
- referencia-peso-rosto: 0.65
- referencia-peso-aparencia: 0
- referencia-inicio: 0.25
- olhar:
- voz-pt: ff_siwis
- voz-fr: ff_siwis
- voz-en: af_heart
- estilo-peso: 1.2
- prompt-primeiro: sim
- pose-sempre: nao

## Homem 50+ (Graphic novel)

Personagem da identidade Graphic novel (2026-10-13). Desde 2026-10-14 (pedido do usuário, com o público real de 55+):
aparenta mais de 60 anos, forte e em ótima forma, sem físico de fisiculturista. Roupa nova a cada Short (lista
`roupas`), igual em todas as cenas. Rosto: `personagens/gn-homem.png` (escolhido pelo usuário em 2026-10-14 entre os
candidatos de 65+ gerados sem referência: `candidatos/gn-homem-03-esparta2-1.png`; barba longa e cheia, grisalha, bem
cuidada; o rosto anterior, de ~50, está em `anteriores/`).

- id: gn-homem
- identidade: graphic-novel
- nome: Homem 60+ (Graphic novel)
- genero: masculino
- selecionavel: sim
- personagem: a strong fit 65-year-old man in excellent physical shape, broad shoulders, a solid athletic build and naturally defined arm muscles under the sleeves, not a bodybuilder, short silver-gray hair, (long full thick silver-gray beard, neatly combed and well-groomed:1.3), thick mustache joined to the beard, weathered mature senior face with visible age lines and natural smile lines, friendly confident expression, no glasses
- negativo: (young man:1.5), (middle-aged man:1.2), (smooth young skin:1.3), (frail:1.4), (hunched:1.3), (skinny:1.3), (thin arms:1.3), (bodybuilder:1.5), (huge muscles:1.5), (veiny arms:1.3), (cargo shorts:1.4), shorts with pockets, (tank top:1.4), sleeveless shirt, (cape:1.5), (armor:1.5), (stubble:1.3), (short beard:1.2), (goatee:1.3), (clean-shaven:1.4)
- uma-pessoa: (one single man, solo:1.4)
- roupas: a fitted navy blue crew-neck t-shirt, charcoal gray athletic jogger pants and navy sneakers | a burnt orange polo shirt, khaki chino trousers and brown leather sneakers | a forest green henley shirt with short sleeves, dark blue jeans and white sneakers | a mustard yellow crew-neck t-shirt, dark gray athletic shorts without pockets and gray running shoes | a light blue short-sleeved button-up shirt, beige chino trousers and tan suede sneakers | a deep red quarter-zip athletic top with short sleeves, black track pants and red-and-white sneakers | a teal short-sleeved sports t-shirt, navy athletic shorts without pockets and white running shoes | a cream linen short-sleeved shirt, olive green trousers and brown loafers | a heather gray crew-neck t-shirt under an open plaid flannel overshirt, dark jeans and brown boots | a cobalt blue short-sleeved polo shirt, light gray jogger pants and white sneakers
- referencia: gn-homem.png
- referencia-peso-rosto: 0.6
- referencia-peso-aparencia: 0
- referencia-inicio: 0.25
- olhar:
- voz-pt:
- voz-fr:
- voz-en:
- estilo-peso: 1.2
- prompt-primeiro: sim
- pose-sempre: nao
