import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import exercicios, media, motion


class BibliotecaFlagTests(unittest.TestCase):
    def enabled(self, text):
        root = Path(tempfile.mkdtemp())
        if text is not None:
            (root / "fluxo_aprovacao.md").write_text(text, encoding="utf-8")
        with patch.object(media, "PROJECT_ROOT", root):
            return exercicios.library_enabled()

    def test_nao_desliga(self):
        for value in ("não", "nao", "Não", "desligada"):
            self.assertFalse(self.enabled(f"- biblioteca-exercicios: {value}\n"))

    def test_sim_ou_sem_linha_liga(self):
        self.assertTrue(self.enabled("- biblioteca-exercicios: sim\n"))
        self.assertTrue(self.enabled("nada\n"))

    def test_roteiro_novo_com_exercise_e_recusado_quando_desligada(self):
        scenes = [{"narration": "a", "exercise": "biceps_curl"}, {"narration": "b"}]
        with patch.object(exercicios, "library_enabled", return_value=False), \
                patch.object(motion, "validate_motion_script", return_value=scenes), \
                patch.object(motion, "check_engagement"), patch.object(motion, "check_subject_scenes"), \
                patch.object(motion, "check_scene_count"):
            with self.assertRaisesRegex(ValueError, "biblioteca de exercícios está desligada"):
                motion.check_new_video({}, "b")

    def test_roteiro_sem_exercise_passa_sem_regras_da_biblioteca(self):
        scenes = [{"narration": "Faça a rosca com a garrafa, oito vezes.", "movement": "biceps_curl"}]
        with patch.object(exercicios, "library_enabled", return_value=False), \
                patch.object(motion, "validate_motion_script", return_value=scenes), \
                patch.object(motion, "check_engagement"), patch.object(motion, "check_subject_scenes"), \
                patch.object(motion, "check_scene_count"), patch("influencer_mcp.historico.check_new_script"):
            motion.check_new_video({}, "b")


if __name__ == "__main__":
    unittest.main()
