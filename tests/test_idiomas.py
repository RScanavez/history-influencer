import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import media, localize


class IdiomasTests(unittest.TestCase):
    def enabled(self, text):
        root = Path(tempfile.mkdtemp())
        if text is not None:
            (root / "fluxo_aprovacao.md").write_text(text, encoding="utf-8")
        with patch.object(media, "PROJECT_ROOT", root):
            return localize.enabled_languages()

    def test_pt_desliga_fr_e_en(self):
        self.assertEqual(self.enabled("- idiomas: pt\n"), [])

    def test_lista_liga_os_listados(self):
        self.assertEqual(self.enabled("- idiomas: pt, en\n"), ["en"])
        self.assertEqual(self.enabled("- idiomas: pt, fr, en\n"), ["fr", "en"])

    def test_sem_linha_vale_tudo(self):
        self.assertEqual(self.enabled("nada\n"), ["fr", "en"])

    def test_idioma_desligado_e_recusado(self):
        with patch.object(localize, "enabled_languages", return_value=["en"]):
            localize.require_enabled("en")
            with self.assertRaises(ValueError):
                localize.require_enabled("fr")
            with self.assertRaises(ValueError):
                localize.save_translation(Path(tempfile.mkdtemp()), {"language": "fr", "title": "t", "description": "d"})

    def test_arquivamento_ignora_idioma_desligado(self):
        from influencer_mcp import output_ok

        folder = Path(tempfile.mkdtemp())
        (folder / "fr").mkdir()
        (folder / "fr" / "output-export.json").write_text('{"base": "x"}', encoding="utf-8")
        archived = lambda _folder, language=None: language is None  # noqa: E731 - só o PT está arquivado
        with patch.object(output_ok, "archived_in_output", archived):
            with patch.object(localize, "enabled_languages", return_value=[]):
                self.assertTrue(output_ok.fully_archived(folder))
            with patch.object(localize, "enabled_languages", return_value=["fr"]):
                self.assertFalse(output_ok.fully_archived(folder))


if __name__ == "__main__":
    unittest.main()
