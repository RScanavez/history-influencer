import json
import re
import unittest
from pathlib import Path

import quickjs

PAGE = Path(__file__).resolve().parents[1] / "influencer_mcp" / "painel.html"


def page_scripts(html: str) -> list[str]:
    return re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)


def syntax_error(source: str) -> str | None:
    """Compila o JavaScript sem executar; devolve a mensagem de erro de sintaxe, se houver."""
    try:
        quickjs.Context().eval(f"new Function({json.dumps(source)})")
    except quickjs.JSException as error:
        return str(error)
    return None


class PainelHtmlTests(unittest.TestCase):
    """Um erro de sintaxe no script derruba a página inteira (painel vazio em 2026-10-04: `const remote` duplicado)."""

    def test_panel_script_has_no_syntax_error(self):
        scripts = page_scripts(PAGE.read_text(encoding="utf-8"))
        self.assertTrue(scripts, "painel.html sem <script>")
        for source in scripts:
            self.assertIsNone(syntax_error(source))

    def test_checker_catches_duplicate_declarations(self):
        self.assertIsNotNone(syntax_error("function f() { const a = 1; const a = 2; }"))
        self.assertIsNone(syntax_error("function f() { const a = 1; const b = a?.x ?? 2; return `${b}`; }"))


    def test_remote_gpu_option_is_never_left_disabled(self):
        html = PAGE.read_text(encoding="utf-8")
        self.assertNotIn('option value="remota" disabled', html)  # ficou cinza em 2026-10-04
        self.assertIn("remoteOption.disabled = false", html)


if __name__ == "__main__":
    unittest.main()
