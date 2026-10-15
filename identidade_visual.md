# Identidade visual

Identidades visuais dos vídeos: cada seção `## ...` é uma identidade (estilo do desenho, modelo, LoRA, negativos e
vídeo) que vale para os personagens dela (os personagens ficam em `personagens.md`, com `- identidade: <id>`).
O painel escolhe a identidade em "Novo lote → Identidade visual" e mostra só os personagens dela.
O código lê este arquivo a cada job (`influencer_mcp/perfis.py`). Mudanças valem para as imagens geradas daqui em diante.

Identidade nova: copie uma seção, troque o `id` (minúsculas e hífens), o `nome` e os textos, e crie em
`personagens.md` os personagens dela (com `selecionavel: sim`). Não apague nem renomeie o `id` de uma identidade
que já tem vídeos.

Chaves:
- `id`: identificador (a seção "Estilo global" é a `aquarela`). `nome`: como aparece no painel.
- `modelo`: checkpoint SDXL do ComfyUI (pasta `tools/ComfyUI/models/checkpoints`).
- `lora`, `lora-forca` (0 a 1), `lora-gatilho`: LoRA de estilo aplicado às imagens-chave e a palavra que o ativa.
- `estilo`: descrição do estilo do desenho (em inglês), acrescentada a toda imagem-chave.
- `negativo`: o que nunca deve aparecer (em inglês). Mantenha `(dumbbells:1.5)` escrito assim: cenas com
  `allowObjects: dumbbells` removem esse termo.
- `video-estilo`, `video-negativo`: o mesmo para a animação (Wan 2.2).

## Estilo global

Identidade atual: lápis com aquarela.

- id: aquarela
- nome: Aquarela (lápis)
- modelo: Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors
- lora: Canopus-Pencil-Art-LoRA.safetensors
- lora-forca: 0.5
- lora-gatilho: Pencil Art
- estilo: hand-drawn graphite pencil illustration with soft watercolor tint, clean confident lines, warm natural light, detailed face, natural proportions, candid natural body language, relaxed natural pose, fully illustrated detailed background environment that matches the scene
- negativo: photograph, photorealistic, 3d render, text, watermark, (logo:1.5), (brand logo:1.6), (logo on clothing:1.6), (logo on shoes:1.6), (swoosh:1.6), (shirtless:1.6), nudity, headless, faceless, blank face, (looking up:1.3), (barefoot:1.5), (standing on a chair:1.7), (standing on furniture:1.6), (cleavage:1.4), (low-cut top:1.4), revealing clothing, (stiff pose:1.3), (posing for the camera:1.2), (jumping:1.5), (leaping:1.5), (feet off the ground:1.4), (plain background:1.3), empty background, (glasses:1.5), (eyeglasses:1.5), duplicate person, extra limbs, extra legs, malformed hands, distorted anatomy, cropped head, blurry, (holding a pencil:1.4), (pen:1.3), (paper:1.3), (paintbrush:1.3), back view, bathroom, bathtub, (bare chest:1.5), towel wrapped around body, (hands in pockets:1.9), (hand in pocket:1.9), (hands in trouser pockets:1.8), (thumbs in pockets:1.5), (cane:1.6), (walking stick:1.6), (walking cane:1.6), (dumbbells:1.5), (bodybuilder:1.4), oversized bottles
- video-estilo: 
- video-negativo: static image, frozen, jitter, flicker, morphing, distorted face, extra limbs, extra fingers, duplicate person, text, watermark, subtitles, photorealistic, sudden camera cut, overexposed, blurry, hands in pockets, putting hands into pockets, hands hidden

## 3D

Teste de identidade visual (2026-10-11): 3D estilizado (Pixar) com paleta quente. Personagens: `3d-mulher` e `3d-homem`.

- id: 3d
- nome: 3D (estilo Pixar)
- modelo: Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors
- lora: Canopus-Pixar-Art.safetensors
- lora-forca: 0.8
- lora-gatilho: 3d image, in the style of Pixar
- estilo: 3d image, Pixar-style 3D animated movie character, stylized 3D render, smooth subsurface skin, proportional expressive eyes, vibrant warm saturated colors, golden orange and yellow lighting, cinematic soft light, fully detailed 3D environment that matches the scene
- negativo: text, watermark, (logo:1.5), (brand logo:1.6), (logo on clothing:1.6), (logo on shoes:1.6), (swoosh:1.6), (shirtless:1.6), nudity, headless, faceless, blank face, (looking up:1.3), (barefoot:1.5), (standing on a chair:1.7), (standing on furniture:1.6), (cleavage:1.4), (low-cut top:1.4), revealing clothing, (stiff pose:1.3), (posing for the camera:1.2), (jumping:1.5), (leaping:1.5), (feet off the ground:1.4), (plain background:1.3), empty background, (glasses:1.5), (eyeglasses:1.5), duplicate person, extra limbs, extra legs, malformed hands, distorted anatomy, cropped head, blurry, (holding a pencil:1.4), (pen:1.3), (paper:1.3), (paintbrush:1.3), back view, bathroom, bathtub, (bare chest:1.5), towel wrapped around body, (hands in pockets:1.9), (hand in pocket:1.9), (hands in trouser pockets:1.8), (thumbs in pockets:1.5), (cane:1.6), (walking stick:1.6), (walking cane:1.6), (dumbbells:1.5), (bodybuilder:1.4), oversized bottles, pencil drawing, sketch, watercolor, monochrome, grayscale, (realistic photo:1.4), (huge eyes:1.3)
- video-estilo: dynamic energetic lively motion, expressive animated body language, smooth 3D animation, vibrant warm colors
- video-negativo: static image, frozen, jitter, flicker, morphing, distorted face, extra limbs, extra fingers, duplicate person, text, watermark, subtitles, photorealistic, sudden camera cut, overexposed, blurry, hands in pockets, putting hands into pockets, hands hidden

## Graphic novel

Identidade de 2026-10-13 (pedido do usuário, depois do teste de 10 LoRAs): ilustração de graphic novel com contorno
de nanquim, cores chapadas com sombra (cel shading) e luz quente. LoRA blink7630/graphic-novel-illustration
(Hugging Face) mais fraco (0,75) e negativos contra o físico de super-herói que ele puxa. Personagens: `gn-homem` e
`gn-mulher`.

- id: graphic-novel
- nome: Graphic novel (quadrinhos)
- modelo: Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors
- lora: Graphic-Novel-Illustration-XL.safetensors
- lora-forca: 0.75
- lora-gatilho: graphic novel illustration
- estilo: graphic novel illustration, western comic book art, european bande dessinée style, realistic adult proportions, bold clean ink outlines, cel shading, rich warm colors, soft dramatic lighting, detailed expressive face, natural proportions, candid natural body language, fully illustrated detailed background environment that matches the scene
- negativo: photograph, photorealistic, 3d render, text, watermark, (logo:1.5), (brand logo:1.6), (logo on clothing:1.6), (logo on shoes:1.6), (swoosh:1.6), (shirtless:1.6), nudity, headless, faceless, blank face, (looking up:1.3), (barefoot:1.5), (standing on a chair:1.7), (standing on furniture:1.6), (cleavage:1.4), (low-cut top:1.4), revealing clothing, (stiff pose:1.3), (posing for the camera:1.2), (jumping:1.5), (leaping:1.5), (feet off the ground:1.4), (plain background:1.3), empty background, (glasses:1.5), (eyeglasses:1.5), duplicate person, extra limbs, extra legs, malformed hands, distorted anatomy, cropped head, blurry, (holding a pencil:1.4), (pen:1.3), (paper:1.3), (paintbrush:1.3), back view, bathroom, bathtub, (bare chest:1.5), towel wrapped around body, (hands in pockets:1.9), (hand in pocket:1.9), (hands in trouser pockets:1.8), (thumbs in pockets:1.5), (cane:1.6), (walking stick:1.6), (walking cane:1.6), (dumbbells:1.5), (bodybuilder:1.4), oversized bottles, (superhero:1.4), (superhero costume:1.4), (exaggerated muscles:1.4), (huge muscles:1.4), (manga:1.6), (anime:1.6), (manga style:1.6), (anime style:1.5), (japanese comic:1.4), (chibi:1.5), (big anime eyes:1.5), (large glossy eyes:1.4), (cel-shaded anime face:1.3), (screentone:1.3), (speed lines:1.3), (halftone dots:1.2), (black and white manga:1.5), speech bubble, panel borders, multiple panels, comic page layout, dark gritty, horror
- video-estilo: smooth natural animation, western graphic novel illustration style, consistent bold ink outlines and cel shading
- video-negativo: static image, frozen, jitter, flicker, morphing, distorted face, extra limbs, extra fingers, duplicate person, text, watermark, subtitles, photorealistic, sudden camera cut, overexposed, blurry, hands in pockets, putting hands into pockets, hands hidden, manga style, anime style, big anime eyes, chibi, speed lines
