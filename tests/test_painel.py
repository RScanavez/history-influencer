import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import painel


class PainelTests(unittest.TestCase):
    def test_publish_slots_read_from_flow_markdown(self):
        text = "# Fluxo\n\n## Grade de publicação\n\n- horarios: 09:00, 18:00\n- fuso: America/Sao_Paulo\n\n## Outra\n- horarios: 01:00\n"
        self.assertEqual(painel.publish_slots(text), {"timezone": "America/Sao_Paulo", "times": ["09:00", "18:00"], "languages": None, "audit": None, "approvedSince": None, "playlist": None, "playlistActive": None, "delayMinutes": None, "commentSince": None})
        self.assertEqual(painel.publish_slots("sem grade")["times"], [])

    def test_clip_seconds_uses_median_of_recorded_clips(self):
        jobs = [{"kind": "animation", "progress": ["t scene-01: clipe 1/2 em 300s", "t scene-01: clipe 2/2 em 500s", "t scene-02: clipe 1/2 em 400s"]}]
        self.assertEqual(painel.clip_seconds(jobs), 400)
        self.assertEqual(painel.clip_seconds([]), painel.DEFAULT_CLIP_SECONDS)

    def test_running_animation_counts_only_pending_scenes_and_clips(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "motion-script.json").write_text(json.dumps({"scenes": [{"motions": ["a", "b"]}] * 3}), encoding="utf-8")
            (folder / "scene-01").mkdir()
            (folder / "scene-01" / "segment.mp4").write_bytes(b"")
            job = {"kind": "animation", "state": "running", "params": {},
                   "progress": ["t cena 1/3 montada", "t scene-02: clipe 1/2 em 400s"]}
            with patch.object(painel, "video_dir_for", return_value=folder):
                seconds = painel.remaining_seconds(job, clip=100)
            # cena 2: falta 1 clipe; cena 3: 2 clipes; + montagem de 2 cenas + finalização
            self.assertEqual(seconds, 3 * 100 + 2 * painel.SCENE_ASSEMBLY_SECONDS + painel.FINISH_SECONDS)

    def test_queue_chains_expected_times(self):
        now = datetime(2026, 10, 1, 12, tzinfo=UTC)
        jobs = [
            {"id": "b", "kind": "keyframes", "state": "queued", "queuedAtNs": 2, "params": {"script": {"slug": "x", "scenes": [{}]}}},
            {"id": "a", "kind": "keyframes", "state": "running", "queuedAtNs": 1, "startedAt": now.isoformat(),
             "params": {"script": {"slug": "y", "scenes": [{}, {}]}}, "progress": []},
        ]
        with patch.object(painel, "video_dir_for", return_value=None):
            rows = painel.queue(jobs, now)
        self.assertEqual([row["id"] for row in rows], ["a", "b"])
        self.assertEqual(rows[1]["expectedStart"], rows[0]["expectedFinish"])

    def test_pedido_de_ajustes_vencido_nao_prende_o_cartao(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "video"
            folder.mkdir()
            (folder / "painel.json").write_text('{"stage": "Ajustes", "updatedAt": "2026-10-08T08:00:00+00:00"}', encoding="utf-8")
            failed = {"kind": "keyframes", "state": "failed", "createdAt": "2026-10-08T07:00:00+00:00"}
            # Nada para ajustar (o job falhou antes de gerar imagens): não é "Ajustes".
            self.assertEqual(painel.video_stage(folder, [failed], "Ajustes"), "Falhou")
            (folder / "keyframes-sheet.png").write_bytes(b"")
            self.assertEqual(painel.video_stage(folder, [failed], "Ajustes"), "Ajustes")
            # Job mais novo que o pedido (refeito fora do painel): o pedido já foi atendido.
            newer = {"kind": "keyframes", "state": "done", "createdAt": "2026-10-08T09:00:00+00:00"}
            self.assertEqual(painel.video_stage(folder, [failed, newer], "Ajustes"), "Revisão das imagens-chave")

    def test_stage_from_jobs_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "video"
            folder.mkdir()
            self.assertEqual(painel.video_stage(folder, [{"kind": "keyframes", "state": "running"}]), "Em produção (imagens-chave)")
            (folder / "keyframes-sheet.png").write_bytes(b"")
            self.assertEqual(painel.video_stage(folder, []), "Revisão das imagens-chave")
            self.assertEqual(painel.video_stage(folder, [{"kind": "animation", "state": "queued"}]), "Na fila da animação")
            (folder / "short.mp4").write_bytes(b"")
            self.assertEqual(painel.video_stage(folder, []), "Revisão do vídeo")
            # Cena refeita (sem segmento): o vídeo pronto é o antigo; falta aprovar as imagens novas.
            for name, segment in (("scene-01", True), ("scene-02", False)):
                (folder / name).mkdir()
                if segment:
                    (folder / name / "segment.mp4").write_bytes(b"")
            self.assertEqual(painel.video_stage(folder, []), "Revisão das imagens-chave")
            (folder / "scene-02" / "segment.mp4").write_bytes(b"")
            self.assertEqual(painel.video_stage(folder, []), "Revisão do vídeo")


class PendingVideoTests(unittest.TestCase):
    def test_queued_keyframes_job_shows_card_before_folder_exists(self):
        import tempfile
        from unittest.mock import patch
        root = Path(tempfile.mkdtemp())
        job = {"id": "a", "kind": "keyframes", "state": "queued", "createdAt": "x",
               "params": {"batch_id": "2026-10-03-1252", "influencer_name": "Historiador",
                          "script": {"slug": "hidratacao", "topic": "Hidratação", "format": "video", "scenes": [{}, {}]}}}
        with patch.object(painel, "GENERATED_ROOT", root):
            rows = painel.videos([job], {})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["slug"], rows[0]["stage"], rows[0]["format"]), ("hidratacao", "Na fila das imagens-chave", "video"))
        self.assertTrue(rows[0]["pending"])


class RejectedVideoTests(unittest.TestCase):
    def test_rejected_video_leaves_the_grid_at_once(self):
        import json
        import tempfile
        from unittest.mock import patch
        root = Path(tempfile.mkdtemp())
        folder = root / "historiador" / "2026-10-09-0203" / "video"
        folder.mkdir(parents=True)
        (folder / "motion-script.json").write_text(json.dumps({"slug": "video", "scenes": [{}]}), encoding="utf-8")
        with patch.object(painel, "GENERATED_ROOT", root), patch.object(painel, "seo_status", return_value={"state": "ok"}):
            self.assertEqual(len(painel.videos([], {})), 1)
            (folder / "painel.json").write_text(json.dumps({"stage": "Rejeitado", "history": []}), encoding="utf-8")
            self.assertEqual(painel.videos([], {}), [])


class RemoteEtaTests(unittest.TestCase):
    def test_remote_queue_uses_h100_clip_time_without_history(self):
        now = datetime(2026, 10, 4, 16, 30, tzinfo=UTC)
        local = {"id": "l", "kind": "animation", "state": "running", "params": {"gpu": "local"}, "startedAt": now.isoformat(),
                 "progress": [f"{now.isoformat()} scene-01: clipe 1/2 em 450s"]}
        remote = {"id": "r", "kind": "animation", "state": "running", "params": {"gpu": "remota"}, "startedAt": now.isoformat(),
                  "progress": [f"{now.isoformat()} GPU remota: ComfyUI respondendo."]}
        with patch.object(painel, "remaining_seconds", side_effect=lambda job, clip: clip):
            rows = {row["id"]: row for row in painel.queue([local, remote], now)}
        self.assertEqual(rows["l"]["remainingSeconds"], 450)
        self.assertEqual(rows["r"]["remainingSeconds"], painel.REMOTE_DEFAULT_CLIP_SECONDS)


if __name__ == "__main__":
    unittest.main()
