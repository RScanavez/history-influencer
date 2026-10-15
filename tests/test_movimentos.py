import unittest

from influencer_mcp import movimentos, motion, historico


class MovimentosTests(unittest.TestCase):
    def test_todo_movimento_tem_textos_completos(self):
        for movement in movimentos.MOVEMENTS.values():
            for field in (movement.setup, movement.go, movement.back, movement.kinesiology, movement.safety):
                self.assertGreater(len(field), 30, movement.id)
            self.assertGreaterEqual(len(movement.cues), 3, movement.id)

    def test_apply_enriquece_prompt_e_motions_de_forma_idempotente(self):
        scene = {"narration": "x", "keyframePrompt": "wide shot, kitchen", "motions": ["a", "b"], "movement": "bodyweight_squat"}
        out = movimentos.apply(scene)
        self.assertIn("feet shoulder-width apart", out["keyframePrompt"])
        self.assertEqual(len(out["motions"]), 2)
        self.assertIn("parallel to the floor", out["motions"][0])
        self.assertIn("rise back", out["motions"][1])
        self.assertEqual(movimentos.apply(out)["keyframePrompt"], out["keyframePrompt"])

    def test_cena_sem_personagem_ou_sem_movement_nao_muda(self):
        scene = {"narration": "x", "keyframePrompt": "p", "motions": ["a"]}
        self.assertEqual(movimentos.apply(scene), scene)
        self.assertEqual(movimentos.apply({**scene, "movement": "biceps_curl", "showCharacter": False}),
                         {**scene, "movement": "biceps_curl", "showCharacter": False})

    def test_serie_na_fala_exige_movement(self):
        scenes = [{"narration": "Repita oito vezes.", "keyframePrompt": "p", "motions": ["a"]}]
        with self.assertRaisesRegex(ValueError, "movement"):
            motion.check_movement_scenes(scenes)
        motion.check_movement_scenes([{**scenes[0], "movement": "biceps_curl"}])

    def test_pausa_de_exercicios_esta_desligada_no_historiador(self):
        # A pausa de marcha/cadeira era do projeto Influencer; aqui PAUSED_UNTIL fica no passado.
        historico.check_paused({"title": "Marcha sobre Roma", "scenes": [{"movement": "standing_march"}]}, today="2026-10-10")

    def test_negativo_de_exercicio_em_pe_proibe_sentar(self):
        self.assertIn("sitting", movimentos.avoid(movimentos.get("calf_raise")))

    def test_nenhum_movimento_usa_parede_cadeira_ou_degrau(self):
        for movement in movimentos.MOVEMENTS.values():
            text = " ".join([movement.setup, movement.go, movement.back, *movement.cues]).lower()
            for word in ("wall", "chair", "railing", "a step", "stool", "rail "):
                self.assertNotIn(word, text, movement.id)
        self.assertIn("hand on a wall", movimentos.avoid(movimentos.get("calf_raise")))


if __name__ == "__main__":
    unittest.main()
