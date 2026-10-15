import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import motion, motion_jobs, server
from influencer_mcp.media import save_script_seo

SEO = {"title": "Teste?", "description": "Descrição.", "hashtags": ["saude"], "tags": ["saúde"], "pinnedComment": "Você?"}
SCRIPT = {"slug": "teste", "title": "Teste?", "scenes": [], "seo": SEO}


def _submit(script=SCRIPT):
    fn = getattr(server.start_motion_keyframes, "fn", server.start_motion_keyframes)
    return fn("Historiador", "2026-10-12-1000", json.dumps(script))


class SubmitTests(unittest.TestCase):
    """2026-10-12: a duração é medida no envio; roteiro longo volta com erro e nada entra na fila.
    2026-10-14: o SEO vem dentro do roteiro e é salvo pelo código antes da narração."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        for target, value in ((motion, "validate_motion_script"), (motion, "check_new_video")):
            patcher = patch.object(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(motion, "motion_output_dir", return_value=folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(server, "save_script_seo")
        self.seo = patcher.start()
        self.addCleanup(patcher.stop)

    def test_long_narration_is_refused_before_the_queue_and_frees_the_subject(self):
        error = ValueError("A narração tem 61.4s; o vídeo precisa ter entre 30 e 59s: retire cerca de 17 palavras.")
        with patch.object(motion_jobs, "narrate_on_cpu", side_effect=error), \
                patch.object(motion_jobs, "start_job") as start, \
                patch("influencer_mcp.historico.unregister_subject") as free:
            with self.assertRaisesRegex(ValueError, "retire cerca de 17 palavras"):
                _submit()
        start.assert_not_called()
        free.assert_called_once_with("2026-10-12-1000", "teste")

    def test_accepted_narration_goes_to_the_queue_with_the_seo_saved(self):
        with patch.object(motion_jobs, "narrate_on_cpu", return_value=55.0) as narrate, \
                patch.object(motion_jobs, "start_job", return_value={"id": "abc", "state": "queued"}) as start:
            self.assertEqual(json.loads(_submit())["jobId"], "abc")
        narrate.assert_called_once()
        self.seo.assert_called_once_with("Historiador", "2026-10-12-1000", SCRIPT)
        self.assertEqual(start.call_args.args[0], "keyframes")

    def test_invalid_seo_is_refused_before_narrating(self):
        self.seo.side_effect = ValueError("O título deve ter entre 1 e 65 caracteres (tem 80).")
        with patch.object(motion_jobs, "narrate_on_cpu") as narrate, patch.object(motion_jobs, "start_job") as start, \
                patch("influencer_mcp.historico.unregister_subject") as free:
            with self.assertRaisesRegex(ValueError, "título"):
                _submit()
        narrate.assert_not_called()
        start.assert_not_called()
        free.assert_called_once_with("2026-10-12-1000", "teste")

    def test_new_video_without_seo_is_refused(self):
        with patch.object(server, "save_script_seo", save_script_seo), patch.object(motion_jobs, "narrate_on_cpu") as narrate:
            with self.assertRaisesRegex(ValueError, 'objeto "seo"'):
                _submit({"slug": "teste", "title": "Teste?", "scenes": []})
        narrate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
