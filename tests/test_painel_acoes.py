import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import media, painel_acoes as acoes


def make_video(root: Path, scenes: int = 4) -> Path:
    folder = root / "historiador" / "2026-10-01-1214" / "video-teste"
    folder.mkdir(parents=True)
    script = {"slug": "video-teste", "scenes": [{"narration": f"fala {n}", "keyframePrompt": f"p{n}", "seed": n} for n in range(1, scenes + 1)]}
    (folder / "motion-script.json").write_text(json.dumps(script), encoding="utf-8")
    for n in range(1, scenes + 1):
        (folder / f"scene-{n:02d}").mkdir()
        (folder / f"scene-{n:02d}" / "marker.txt").write_text(str(n), encoding="utf-8")
    (folder / "translation-fr.json").write_text(json.dumps(
        {"language": "fr", "scenes": [{"scene": n, "narration": f"fr {n}"} for n in range(1, scenes + 1)]}), encoding="utf-8")
    return folder


class PainelAcoesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for item in (patch.object(acoes, "GENERATED_ROOT", self.root), patch.object(acoes, "_active_jobs", return_value=[])):
            item.start()
            self.addCleanup(item.stop)

    def test_rejects_invalid_identifiers(self):
        with self.assertRaises(ValueError):
            acoes.video_folder("../x", "video-teste")
        with self.assertRaises(ValueError):
            acoes.video_folder("2026-10-01-1214", "Video Teste")

    def test_remove_scene_renumbers_folders_script_and_translations(self):
        folder = make_video(self.root)
        with patch.object(acoes, "start_job") as start:
            result = acoes.remover_cena("2026-10-01-1214", "video-teste", 2)
        start.assert_not_called()  # sem short.mp4 não há o que remontar
        self.assertIn("3 cenas", result["message"])
        script = json.loads((folder / "motion-script.json").read_text(encoding="utf-8"))
        self.assertEqual([scene["narration"] for scene in script["scenes"]], ["fala 1", "fala 3", "fala 4"])
        self.assertEqual([(folder / f"scene-{n:02d}" / "marker.txt").read_text() for n in (1, 2, 3)], ["1", "3", "4"])
        self.assertFalse((folder / "scene-04").exists())
        self.assertEqual(len(list((folder / "removidas").iterdir())), 1)
        translation = json.loads((folder / "translation-fr.json").read_text(encoding="utf-8"))
        self.assertEqual([(item["scene"], item["narration"]) for item in translation["scenes"]], [(1, "fr 1"), (2, "fr 3"), (3, "fr 4")])

    def test_remove_scene_reassembles_finished_video(self):
        folder = make_video(self.root)
        (folder / "short.mp4").write_bytes(b"")
        with patch.object(acoes, "start_job") as start, patch.object(acoes, "remontar_video") as remount:
            result = acoes.remover_cena("2026-10-01-1214", "video-teste", 4)
        start.assert_not_called()  # sem GPU nem fila: só junta os trechos já animados
        remount.assert_called_once_with(folder)
        self.assertIsNone(result["jobId"])
        self.assertIn("sem GPU", result["message"])

    def test_remove_scene_waives_minimum_duration_but_keeps_maximum(self):
        folder = make_video(self.root)
        path = folder / "motion-script.json"
        script = json.loads(path.read_text(encoding="utf-8"))
        script["targetSeconds"] = [52, 59]
        path.write_text(json.dumps(script), encoding="utf-8")
        acoes.remover_cena("2026-10-01-1214", "video-teste", 2)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["targetSeconds"], [0, 59])

    def test_remove_scene_keeps_minimum(self):
        make_video(self.root, scenes=acoes.MIN_SCENES)
        with self.assertRaises(ValueError):
            acoes.remover_cena("2026-10-01-1214", "video-teste", 1)

    def test_redo_keyframes_drops_fixed_seed_of_selected_scenes(self):
        make_video(self.root)
        with patch.object(acoes, "start_job", return_value={"id": "k1"}) as start:
            acoes.refazer_cenas("2026-10-01-1214", "video-teste", [3, 3])
        kind, params = start.call_args.args
        self.assertEqual(kind, "keyframes")
        self.assertEqual(params["scene_numbers"], [3])
        self.assertNotIn("seed", params["script"]["scenes"][2])
        self.assertEqual(params["script"]["scenes"][0]["seed"], 1)

    def test_redo_scenes_of_finished_video_chains_animation(self):
        folder = make_video(self.root)
        with patch.object(acoes, "start_job", return_value={"id": "k1"}) as start:
            acoes.refazer_cenas("2026-10-01-1214", "video-teste", [2])
        self.assertFalse(start.call_args.args[1]["thenAnimate"])  # vídeo ainda não pronto: só as imagens
        (folder / "short.mp4").write_bytes(b"")
        with patch.object(acoes, "start_job", return_value={"id": "k2"}) as start:
            result = acoes.refazer_cenas("2026-10-01-1214", "video-teste", [2])
        self.assertTrue(start.call_args.args[1]["thenAnimate"])
        self.assertIn("remontado", result["message"])

    def test_busy_video_refuses_actions(self):
        make_video(self.root)
        with patch.object(acoes, "_active_jobs", return_value=[{"id": "x"}]):
            with self.assertRaises(RuntimeError):
                acoes.aprovar_imagens("2026-10-01-1214", "video-teste")

    def test_approve_requires_finished_video_and_records_history(self):
        folder = make_video(self.root)
        with self.assertRaises(RuntimeError):
            acoes.definir_estado("2026-10-01-1214", "video-teste", "Aprovado")
        (folder / "short.mp4").write_bytes(b"")
        acoes.definir_estado("2026-10-01-1214", "video-teste", "Aprovado", "ok")
        state = acoes.read_state(folder)
        self.assertEqual(state["stage"], "Aprovado")
        self.assertEqual(state["history"][-1]["action"], "Aprovado")

    def test_batch_validation_and_command(self):
        with self.assertRaises(ValueError):
            acoes.iniciar_lote(9, "x")
        with (
            patch.object(acoes, "LOTES_DIR", self.root / "lotes"),
            patch.object(acoes, "claude_executable", return_value="claude"),
            patch.object(acoes, "_spawn") as spawn,
        ):
            spawn.return_value.pid = 0
            result = acoes.iniciar_lote(2, "equilíbrio\n e cadeira")
        args = spawn.call_args.args[0]
        self.assertEqual(args[:3], ["claude", "-p", "/gerar-lote 2 equilíbrio e cadeira"])
        self.assertIn("--strict-mcp-config", args)
        with (
            patch.object(acoes, "LOTES_DIR", self.root / "lotes"),
            patch.object(acoes, "claude_executable", return_value="claude"),
            patch.object(acoes, "_spawn") as spawn_video,
        ):
            spawn_video.return_value.pid = 0
            acoes.iniciar_lote(1, "postura", "video")
        self.assertEqual(spawn_video.call_args.args[0][2], "/gerar-lote 1 video postura")
        self.assertEqual(spawn.call_args.kwargs["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"], "0")
        self.assertIn("gerar-lote", result["message"])

    def test_parallel_batches_get_distinct_batch_ids(self):
        from datetime import datetime
        lotes = self.root / "lotes"
        with (
            patch.object(acoes, "LOTES_DIR", lotes),
            patch.object(acoes, "claude_executable", return_value="claude"),
            patch.object(acoes, "_spawn") as spawn,
            patch.object(acoes, "list_lotes", return_value=[{"running": True}]),
        ):
            spawn.return_value.pid = 0
            first = acoes.iniciar_lote(1, "a")
            second = acoes.iniciar_lote(1, "b")
        ids = [json.loads((lotes / f"{r['id']}.json").read_text(encoding="utf-8"))["batchId"] for r in (first, second)]
        self.assertNotEqual(ids[0], ids[1])
        self.assertTrue(spawn.call_args.args[0][-1].startswith(f"Use exatamente o batch_id {ids[1]} neste lote."))
        record = json.loads((lotes / f"{second['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["character"], "Mulher 50+")
        (self.root / "historiador" / "2026-10-02-0115").mkdir(parents=True)
        self.assertEqual(acoes.free_batch_id(datetime(2026, 10, 2, 1, 15)), "2026-10-02-0116")

    def test_delete_video_removes_folder_jobs_outputs_and_empty_batch(self):
        folder = make_video(self.root)
        out = self.root / "output"
        out.mkdir()
        (folder / "output-export.json").write_text(json.dumps({"base": "Video Teste"}), encoding="utf-8")
        (out / "Video Teste.mp4").write_bytes(b"")
        (out / "Video Teste.txt").write_bytes(b"")
        (out / "Outro.mp4").write_bytes(b"")
        jobs = self.root / "jobs"
        jobs.mkdir()
        (jobs / "j1.json").write_text(json.dumps({"id": "j1", "state": "done", "params": {"batch_id": "2026-10-01-1214", "video_slug": "video-teste"}}), encoding="utf-8")
        (jobs / "j2.json").write_text(json.dumps({"id": "j2", "state": "done", "params": {"batch_id": "2026-10-01-1214", "video_slug": "outro"}}), encoding="utf-8")
        lotes = self.root / "lotes"
        lotes.mkdir()
        (lotes / "l1.json").write_text(json.dumps({"batchId": "2026-10-01-1214", "pid": 0}), encoding="utf-8")
        from influencer_mcp import output_ok
        with patch.object(acoes, "JOBS_DIR", jobs), patch.object(acoes, "LOTES_DIR", lotes),                 patch.object(output_ok, "GENERATED_ROOT", self.root / "generated"),                 patch.object(output_ok, "output_root", return_value=out):
            result = acoes.excluir_video("2026-10-01-1214", "video-teste")
        self.assertFalse(folder.exists())
        self.assertFalse(folder.parent.exists())
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["Outro.mp4"])
        self.assertEqual(sorted(p.name for p in jobs.iterdir()), ["j2.json"])
        self.assertFalse((lotes / "l1.json").exists())
        self.assertEqual(result["removedJobs"], ["j1"])

    def test_delete_refuses_videos_sent_to_youtube(self):
        folder = make_video(self.root)
        (folder / "publicacao.json").write_text(json.dumps({"status": "cancelado", "videoId": "abc"}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "YouTube"):
            acoes.excluir_video("2026-10-01-1214", "video-teste")
        self.assertTrue(folder.exists())


class RejectedCleanupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.folder = make_video(self.root)
        from influencer_mcp import youtube_publish
        for item in (patch.object(acoes, "GENERATED_ROOT", self.root),
                     patch.object(youtube_publish, "video_folders", return_value=[self.folder]),
                     patch.object(acoes, "_stop_jobs", return_value=[])):
            item.start()
            self.addCleanup(item.stop)

    def reject(self, at):
        state = {"stage": "Rejeitado", "history": [{"at": at, "action": "Rejeitado", "note": ""}], "updatedAt": at}
        (self.folder / "painel.json").write_text(json.dumps(state), encoding="utf-8")

    def test_deleted_only_after_five_seconds(self):
        from datetime import datetime
        self.reject("2026-10-03T10:00:00+00:00")
        with patch.object(acoes, "excluir_video") as delete:
            self.assertEqual(acoes.limpar_rejeitados(datetime.fromisoformat("2026-10-03T10:00:04+00:00")), [])
            delete.assert_not_called()
            self.assertEqual(acoes.limpar_rejeitados(datetime.fromisoformat("2026-10-03T10:00:06+00:00")), ["video-teste"])
            delete.assert_called_once_with("2026-10-01-1214", "video-teste")

    def test_changing_state_cancels_deletion(self):
        from datetime import datetime
        self.reject("2026-10-03T10:00:00+00:00")
        acoes.write_state(self.folder, "Ajustes", "", "Ajustes")
        with patch.object(acoes, "excluir_video") as delete:
            acoes.limpar_rejeitados(datetime.fromisoformat("2026-10-03T11:00:00+00:00"))
        delete.assert_not_called()

    def test_videos_on_youtube_are_kept_and_flagged(self):
        from datetime import datetime
        self.reject("2026-10-03T10:00:00+00:00")
        with patch.object(acoes, "excluir_video", side_effect=RuntimeError("já está no YouTube")):
            self.assertEqual(acoes.limpar_rejeitados(datetime.fromisoformat("2026-10-03T11:00:00+00:00")), [])
        self.assertTrue(self.folder.exists())
        self.assertIn("YouTube", acoes.read_state(self.folder)["autoDeleteBlocked"])


class JobButtonsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        from influencer_mcp import motion_jobs
        for item in (patch.object(acoes, "JOBS_DIR", self.jobs), patch.object(motion_jobs, "JOBS_DIR", self.jobs),
                     patch.object(acoes, "GENERATED_ROOT", self.root)):
            item.start()
            self.addCleanup(item.stop)
        job = {"id": "abcdef123456", "kind": "animation", "state": "queued",
               "params": {"batch_id": "2026-10-01-1214", "video_slug": "video-teste"}}
        (self.jobs / "abcdef123456.json").write_text(json.dumps(job), encoding="utf-8")

    def test_pause_and_resume_toggle_pause_file(self):
        acoes.pausar_job("abcdef123456")
        self.assertTrue((self.jobs / "abcdef123456.pause").is_file())
        acoes.retomar_job("abcdef123456")
        self.assertFalse((self.jobs / "abcdef123456.pause").exists())

    def test_cancel_stops_job_and_deletes_video(self):
        with patch.object(acoes, "_stop_jobs") as stop, patch.object(acoes, "excluir_video") as delete:
            acoes.cancelar_job("abcdef123456")
        stop.assert_called_once_with("2026-10-01-1214", "video-teste")
        delete.assert_called_once_with("2026-10-01-1214", "video-teste")

    def test_cancel_keeps_files_of_videos_on_youtube(self):
        with patch.object(acoes, "_stop_jobs"), patch.object(acoes, "excluir_video", side_effect=RuntimeError("no YouTube")):
            self.assertIn("mantidos", acoes.cancelar_job("abcdef123456")["message"])

    def test_rejects_bad_job_ids(self):
        with self.assertRaises(ValueError):
            acoes.pausar_job("../x")


class AutoSeoTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        from influencer_mcp import youtube_publish
        self.root = Path(tempfile.mkdtemp())
        self.folder = make_video(self.root)
        (self.folder / "translation-fr.json").unlink()
        self.spawned = []
        for item in (patch.object(acoes, "GENERATED_ROOT", self.root), patch.object(acoes, "SEO_DIR", self.root / "seo"),
                     patch.object(acoes, "LOTES_DIR", self.root / "lotes"),
                     patch.object(youtube_publish, "video_folders", return_value=[self.folder]),
                     patch.object(acoes, "enabled_languages", return_value=["fr", "en"]),
                     patch.object(acoes, "claude_executable", return_value="claude"),
                     patch.object(acoes, "pid_alive_since", return_value=False),
                     patch.object(acoes, "_spawn", side_effect=lambda args, log, env=None: (self.spawned.append(args), SimpleNamespace(pid=42))[1])):
            item.start()
            self.addCleanup(item.stop)

    def test_starts_seo_for_video_missing_metadata(self):
        self.assertEqual(acoes.seo_missing(self.folder), ["pt", "fr", "en"])
        self.assertEqual(acoes.gerar_seo_pendente(), ["video-teste"])
        self.assertIn("/seo-video 2026-10-01-1214 video-teste", self.spawned[0])
        self.assertEqual(acoes.seo_status(self.folder)["state"], "falta")  # processo de teste não está vivo

    def test_only_portuguese_when_flag_is_pt(self):
        with patch.object(acoes, "enabled_languages", return_value=[]):
            self.assertEqual(acoes.seo_missing(self.folder), ["pt"])

    def test_waits_before_retrying_and_gives_up_after_three(self):
        from datetime import datetime, timedelta
        acoes.gerar_seo_pendente()
        record = json.loads((self.root / "seo" / "2026-10-01-1214__video-teste.json").read_text(encoding="utf-8"))
        started = datetime.fromisoformat(record["startedAt"])
        self.assertEqual(acoes.gerar_seo_pendente(started + timedelta(minutes=5)), [])
        self.assertEqual(acoes.gerar_seo_pendente(started + timedelta(minutes=21)), ["video-teste"])
        record = json.loads((self.root / "seo" / "2026-10-01-1214__video-teste.json").read_text(encoding="utf-8"))
        record["attempts"] = 3
        (self.root / "seo" / "2026-10-01-1214__video-teste.json").write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(acoes.gerar_seo_pendente(started + timedelta(hours=3)), [])
        self.assertEqual(acoes.seo_status(self.folder)["state"], "erro")

    def test_script_with_seo_inside_is_saved_by_code_without_claude(self):
        """2026-10-14: o SEO vem dentro do roteiro; o vigia salva pelo código e não dispara o Claude."""
        script = json.loads((self.folder / "motion-script.json").read_text(encoding="utf-8"))
        script["seo"] = {"title": "Jantar tarde depois dos 45?", "description": "Por que e o que fazer.",
                         "hashtags": ["saude", "depoisdos50"], "tags": ["jantar tarde"], "pinnedComment": "Você janta depois das 21h?"}
        (self.folder / "motion-script.json").write_text(json.dumps(script), encoding="utf-8")
        with patch.object(acoes, "enabled_languages", return_value=[]), patch.object(media, "GENERATED_ROOT", self.root):
            self.assertEqual(acoes.gerar_seo_pendente(), ["video-teste"])
            self.assertEqual(acoes.seo_status(self.folder), {"state": "ok"})
        self.assertEqual(self.spawned, [])
        saved = json.loads((self.folder / "youtube-metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["hashtags"], ["#saude", "#depoisdos50"])

    def test_complete_seo_or_running_batch_is_left_alone(self):
        with patch.object(acoes, "_batch_running", return_value=True):
            self.assertEqual(acoes.gerar_seo_pendente(), [])
        with patch.object(acoes, "seo_missing", return_value=[]):
            self.assertEqual(acoes.gerar_seo_pendente(), [])


class CharacterChoiceTests(unittest.TestCase):
    def test_man_choice_is_recorded_and_sent_to_the_batch(self):
        root = Path(tempfile.mkdtemp())
        lotes = root / "lotes"
        with (
            patch.object(acoes, "GENERATED_ROOT", root), patch.object(acoes, "LOTES_DIR", lotes),
            patch.object(acoes, "claude_executable", return_value="claude"), patch.object(acoes, "_spawn") as spawn,
        ):
            spawn.return_value.pid = 0
            result = acoes.iniciar_lote(1, "tema", "short", "ilustracao-homem")
            with self.assertRaises(ValueError):
                acoes.iniciar_lote(1, "tema", "short", "inexistente")
        record = json.loads((lotes / f"{result['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual((record["profile"], record["character"]), ("ilustracao-homem", "Homem 50+"))
        self.assertIn("masculino", spawn.call_args.args[0][-1])
        from influencer_mcp import perfis
        self.assertEqual(perfis.profile_for_batch(record["batchId"], lotes), "ilustracao-homem")
        self.assertIsNone(perfis.profile_for_batch("2000-01-01-0000", lotes))
        self.assertIn("Homem 50+", result["message"])

    def test_visual_identity_choice_limits_characters_and_is_recorded(self):
        root = Path(tempfile.mkdtemp())
        lotes = root / "lotes"
        with (
            patch.object(acoes, "GENERATED_ROOT", root), patch.object(acoes, "LOTES_DIR", lotes),
            patch.object(acoes, "claude_executable", return_value="claude"), patch.object(acoes, "_spawn") as spawn,
            patch.object(acoes, "videos_started_today", return_value=0),
        ):
            spawn.return_value.pid = 0
            result = acoes.iniciar_lote(2, "tema", "short", "auto", identity="3d")
            # Página antiga, sem o seletor de identidade: o personagem 3D já diz a identidade.
            old_page = acoes.iniciar_lote(1, "tema", "short", "3d-mulher")
            with self.assertRaises(ValueError):
                acoes.iniciar_lote(1, "tema", "short", "auto", identity="inexistente")
            with self.assertRaises(ValueError):
                acoes.iniciar_lote(1, "tema", "short", "inexistente", identity="3d")
        self.assertEqual(json.loads((lotes / f"{old_page['id']}.json").read_text(encoding="utf-8"))["identity"], "3d")
        record = json.loads((lotes / f"{result['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["identity"], "3d")
        self.assertTrue(record["profile"].startswith("3d-"))
        instruction = spawn.call_args.args[0][-1]
        self.assertNotIn("aquarela-mulher", instruction)
        self.assertNotIn("ilustracao-homem", instruction)


# Os testes de lote não dependem de haver GPU NVIDIA na máquina que roda a suíte.
_gpu_patch = None


def setUpModule():
    global _gpu_patch
    from influencer_mcp import ambiente
    _gpu_patch = patch.object(ambiente, "local_gpu", return_value={"available": True, "gpus": ["teste"], "problems": []})
    _gpu_patch.start()


def tearDownModule():
    _gpu_patch.stop()


if __name__ == "__main__":
    unittest.main()


class MoveGpuTests(unittest.TestCase):
    def test_queued_job_moves_to_remote_keeping_the_video(self):
        root = Path(tempfile.mkdtemp())
        folder = make_video(root)
        jobs = root / "jobs"
        jobs.mkdir()
        job = {"id": "abcdef123456", "kind": "animation", "state": "queued", "pid": 1, "progress": [],
               "params": {"influencer_name": "Historiador", "batch_id": "2026-10-01-1214", "video_slug": "video-teste",
                          "gpu": "local"}}
        (jobs / "abcdef123456.json").write_text(json.dumps(job), encoding="utf-8")
        with patch.object(acoes, "GENERATED_ROOT", root), patch.object(acoes, "JOBS_DIR", jobs), \
                patch.object(acoes, "_stop_jobs") as stop, \
                patch.object(acoes, "start_job", return_value={"id": "fedcba654321"}) as start:
            result = acoes.mover_gpu("abcdef123456", "remota")
        stop.assert_called_once_with("2026-10-01-1214", "video-teste")
        self.assertEqual(start.call_args.args, ("animation", {**job["params"], "gpu": "remota"}))
        self.assertEqual(result["jobId"], "fedcba654321")
        self.assertEqual(json.loads((folder / "motion-script.json").read_text(encoding="utf-8"))["gpu"], "remota")
        job["state"] = "running"
        (jobs / "abcdef123456.json").write_text(json.dumps(job), encoding="utf-8")
        with patch.object(acoes, "GENERATED_ROOT", root), patch.object(acoes, "JOBS_DIR", jobs):
            with self.assertRaises(RuntimeError):
                acoes.mover_gpu("abcdef123456", "remota")


class CharacterPlanTests(unittest.TestCase):
    def test_mixed_batch_tells_each_video_its_character(self):
        characters = {"aquarela-mulher": {"label": "Mulher 50+", "gender": "feminino"},
                      "ilustracao-homem": {"label": "Homem 50+", "gender": "masculino"}}
        text = acoes._character_instruction(["aquarela-mulher", "ilustracao-homem"], characters)
        self.assertIn('vídeo 2: Homem 50+ ("visualProfile": "ilustracao-homem", narração no masculino)', text)
        single = acoes._character_instruction(["aquarela-mulher"] * 2, characters)
        self.assertIn('"visualProfile": "aquarela-mulher"', single)


class DailyLimitTests(unittest.TestCase):
    def test_videos_requested_today_are_counted_from_the_batches(self):
        from datetime import UTC, datetime, timedelta
        lotes = Path(tempfile.mkdtemp())
        now = datetime.now(UTC)
        for name, when, qty in (("a", now, 2), ("b", now - timedelta(days=2), 5)):
            (lotes / f"{name}.json").write_text(json.dumps({"startedAt": when.isoformat(), "quantity": qty}), encoding="utf-8")
        with patch.object(acoes, "LOTES_DIR", lotes):
            self.assertEqual(acoes.videos_started_today(), 2)
        self.assertEqual(acoes.DEFAULT_DAILY_LIMIT, 3)

    def test_daily_limit_can_be_turned_off(self):
        root = Path(tempfile.mkdtemp())
        with patch.object(media, "PROJECT_ROOT", root):
            for line, expected in (("sem limite", None), ("ilimitado", None), ("0", None), ("9", 9), ("abc", 3)):
                (root / "fluxo_aprovacao.md").write_text(f"- videos-por-dia: {line}" + chr(10), encoding="utf-8")
                self.assertEqual(acoes.daily_limit(), expected, line)


class DesligarComfyuiTests(unittest.TestCase):
    def test_refuses_while_a_local_job_is_active(self):
        job = {"id": "x", "state": "running", "params": {}}
        with patch("influencer_mcp.motion_jobs.list_jobs", return_value=[job]), \
                patch("influencer_mcp.comfyui.stop") as stop:
            with self.assertRaisesRegex(RuntimeError, "jobs na GPU local"):
                acoes.desligar_comfyui()
            stop.assert_not_called()

    def test_remote_jobs_do_not_block(self):
        job = {"id": "x", "state": "running", "params": {"gpu": "remota"}}
        with patch("influencer_mcp.motion_jobs.list_jobs", return_value=[job]), \
                patch("influencer_mcp.comfyui.stop", return_value=True) as stop:
            self.assertIn("desligado", acoes.desligar_comfyui()["message"])
            stop.assert_called_once()

    def test_reports_when_already_off(self):
        with patch("influencer_mcp.motion_jobs.list_jobs", return_value=[]), \
                patch("influencer_mcp.comfyui.stop", return_value=False):
            self.assertIn("já estava desligado", acoes.desligar_comfyui()["message"])
