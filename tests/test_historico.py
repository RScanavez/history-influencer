import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import historico
from influencer_mcp import historico as h


class RepeatTests(unittest.TestCase):
    def test_same_topic_same_angle_is_a_repeat(self):
        self.assertTrue(historico.is_repeat("Pernas fortes depois dos 60: 5 exercícios com cadeira",
                                            "Pernas fortes depois dos 50: 5 exercícios com uma cadeira"))
        self.assertTrue(historico.is_repeat("Mobilidade do quadril todos os dias depois dos 60",
                                            "Melhore a mobilidade de seu quadril diariamente!"))

    def test_same_topic_new_angle_is_not_a_word_repeat(self):
        self.assertFalse(historico.is_repeat("Pernas fracas depois dos 60? 3 exercícios na cadeira para subir escada",
                                             "Pernas fortes depois dos 50: 5 exercícios com uma cadeira"))
        self.assertFalse(historico.is_repeat("Evitar quedas no banho depois dos 60: equilíbrio com apoio na pia",
                                             "Exercícios para evitar quedas"))


class SameProblemTests(unittest.TestCase):
    """2026-10-11: o mesmo problema do dia a dia nunca volta, em qualquer data e com qualquer solução."""

    def test_same_everyday_problem_is_a_repeat_at_any_date(self):
        self.assertTrue(h.same_problem("Difícil levantar do sofá depois dos 60? Veja este exercício",
                                       "Dificuldade para levantar do sofá? Halteres leves ajudam"))
        self.assertFalse(h.same_problem("Degrau difícil de subir depois dos 60? Fortaleça a coxa",
                                        "Difícil levantar do sofá depois dos 60? Veja este exercício"))
        old = {"source": "youtube:x", "date": "2025-01-01", "problem": "Dificuldade para levantar do sofá",
               "title": "Dificuldade para levantar do sofá? Halteres leves ajudam"}
        with patch.object(h, "history", return_value=[old]):
            hit = h.find_repeat("Difícil levantar do sofá depois dos 60? Veja este exercício", "outro ângulo")
        self.assertEqual(hit["reason"], "mesmo problema")


class SubjectListTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "assuntos.md"
        patcher = patch.object(h, "SUBJECTS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_list_is_built_once_and_the_channel_is_read_at_most_once_a_day(self):
        local = [{"source": "2026-10-11-0900/azia", "title": "Azia à noite? Jante cedo #sono", "topic": "", "date": "2026-10-11"}]
        channel = [{"source": "youtube:a", "title": "Azia à noite? Jante cedo #sono", "topic": "", "date": "2026-10-12"},
                   {"source": "youtube:b", "title": "Ronca e acorda cansado? Quem dorme ao lado percebe", "topic": "",
                    "date": "2026-10-10"}]
        now = datetime(2026, 10, 12, 12, tzinfo=UTC)
        with patch.object(h, "local_videos", return_value=local), \
                patch.object(h, "channel_videos", return_value=channel) as api:
            rows = h.sync_subjects(now=now)
            self.assertEqual(api.call_count, 1)
            h.sync_subjects(now=now + timedelta(hours=5))
            self.assertEqual(api.call_count, 1)  # menos de um dia: só a lista
            h.sync_subjects(now=now + timedelta(hours=25))
            self.assertEqual(api.call_count, 2)
        # O vídeo local publicado no canal (mesmo título) não entra duas vezes; hashtags saem.
        self.assertEqual(sorted(row["source"] for row in rows), ["2026-10-11-0900/azia", "youtube:b"])
        self.assertEqual(h.read_subjects()[0][1]["problem"], "Azia à noite")

    def test_accepted_script_reserves_the_subject_and_deleting_frees_it(self):
        script = {"slug": "unha", "title": "Unha encravada depois dos 50? Corte reto", "topic": "unhas"}
        with patch.object(h, "local_videos", return_value=[]), patch.object(h, "channel_videos", return_value=[]):
            h.check_new_script(script, "2026-10-12-1000")
            self.assertIn("Unha encravada depois dos 50", self.path.read_text(encoding="utf-8"))
            # O mesmo vídeo reenviado não bate nele mesmo; outro vídeo com o mesmo problema é recusado.
            h.check_new_script(script, "2026-10-12-1000")
            with self.assertRaisesRegex(ValueError, "Tema repetido.*Unha encravada"):
                h.check_new_script({**script, "slug": "outro", "title": "Unha encravada depois dos 50? Use algodão"},
                                   "2026-10-12-1100")
            h.unregister_subject("2026-10-12-1000", "unha")
            h.check_new_script({**script, "slug": "outro"}, "2026-10-12-1100")

    def test_user_can_force_a_repeated_subject_for_one_script(self):
        """2026-10-14: "permitirTemaRepetido" no roteiro é decisão explícita do usuário; nunca entra por padrão."""
        script = {"slug": "unha", "title": "Unha encravada depois dos 50? Corte reto", "topic": "unhas"}
        with patch.object(h, "local_videos", return_value=[]), patch.object(h, "channel_videos", return_value=[]):
            h.check_new_script(script, "2026-10-12-1000")
            forced = {**script, "slug": "outro", "title": "Unha encravada depois dos 50? Use algodão"}
            with self.assertRaisesRegex(ValueError, "Tema repetido"):
                h.check_new_script(forced, "2026-10-12-1100")
            h.check_new_script({**forced, "permitirTemaRepetido": True}, "2026-10-12-1100")


if __name__ == "__main__":
    unittest.main()
