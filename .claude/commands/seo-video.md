Gere e salve o SEO de UM vídeo já existente, nesta sessão e sem subagentes: português sempre; francês e inglês só se a linha `- idiomas:` de `fluxo_aprovacao.md` os listar (hoje `pt`: só português, sem FR/EN). Argumentos: `$ARGUMENTS` = `<batch_id> <slug>`.

1. Leia `seo.md` (regras) e o roteiro em `generated/historiador/<batch_id>/<slug>/motion-script.json` (tema, falas de cada cena, formato, `visualProfile`, `thumbnailText`). Se o roteiro já tiver o objeto `seo`, use-o como está. Sem pesquisa: nada de YouTube, Analytics ou web.
2. Salve com `save_youtube_metadata` (influenciador "Historiador", `batch_id`, `slug`). SOMENTE se `idiomas` incluir `fr`/`en`, salve também com `save_french_version` / `save_english_version` (tradução dos metadados e da narração de cada cena); com `idiomas: pt`, não chame.
3. Se uma ferramenta recusar (hashtag inválida, título longo), corrija e salve de novo.

Não altere o roteiro, as imagens nem a animação. Não publique, agende nem autentique em nenhuma conta do YouTube. Responda no fim com uma linha por idioma: salvo ou erro.
