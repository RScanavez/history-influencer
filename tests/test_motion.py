import tempfile
import unittest
from pathlib import Path

from PIL import Image

from influencer_mcp import motion


class MotionPlanTests(unittest.TestCase):
    def test_clip_plan_covers_narration_with_valid_wan_lengths(self) -> None:
        for duration in (4.0, 10.2, 12.4, 20.0):
            with self.subTest(duration=duration):
                plan = motion.clip_plan(duration, ["a", "b"])
                for _, frames in plan:
                    self.assertLessEqual(frames, motion.WAN_MAX_FRAMES)
                    self.assertEqual((frames - 1) % 4, 0)
                generated_seconds = sum(frames - 1 for _, frames in plan) / motion.WAN_FPS
                self.assertGreaterEqual(generated_seconds * motion.MAX_SLOWDOWN, duration - 0.1)

    def test_clip_plan_reuses_last_motion_when_more_clips_are_needed(self) -> None:
        plan = motion.clip_plan(20.0, ["levanta"])
        self.assertGreater(len(plan), 1)
        self.assertTrue(all(prompt == "levanta" for prompt, _ in plan))


class MotionWorkflowTests(unittest.TestCase):
    def test_wan_workflow_chains_high_and_low_noise_experts(self) -> None:
        workflow = motion.wan_i2v_workflow(start_image="start.png", prompt="acena", frames=81, seed=7, prefix="t")
        high, low = workflow["13"]["inputs"], workflow["14"]["inputs"]
        self.assertEqual((high["start_at_step"], high["end_at_step"]), (0, 2))
        self.assertEqual(low["latent_image"], ["13", 0])
        self.assertEqual(workflow["12"]["inputs"]["length"], 81)
        self.assertEqual(workflow["11"]["inputs"]["image"], "start.png")
        self.assertIn("acena", workflow["8"]["inputs"]["text"])
        self.assertEqual(workflow["16"]["inputs"]["filename_prefix"], "t-raw")
        self.assertEqual(workflow["20"]["inputs"]["images"], ["19", 0])

    def test_wan_prompt_keeps_the_keyframe_outfit(self) -> None:
        dressed = motion.wan_i2v_workflow(start_image="a.png", prompt="acena", frames=81, seed=1, prefix="t",
                                          outfit="a white gi with a white belt.")
        self.assertIn("wearing a white gi with a white belt the whole time", dressed["8"]["inputs"]["text"])
        self.assertNotIn("navy blue crew-neck t-shirt", dressed["8"]["inputs"]["text"])  # roupa padrão sai
        plain = motion.wan_i2v_workflow(start_image="a.png", prompt="acena", frames=81, seed=1, prefix="t")
        self.assertIn("navy blue crew-neck t-shirt", plain["8"]["inputs"]["text"])
        scenery = motion.wan_i2v_workflow(start_image="a.png", prompt="mar", frames=81, seed=1, prefix="t",
                                          with_character=False, outfit="a white gi")
        self.assertNotIn("gi", scenery["8"]["inputs"]["text"])

    def test_subject_scene_blocks_people_in_the_animation(self) -> None:
        scenery = motion.wan_i2v_workflow(start_image="a.png", prompt="mar", frames=81, seed=1, prefix="t", with_character=False)
        self.assertIn("hand entering the frame", scenery["9"]["inputs"]["text"])
        person = motion.wan_i2v_workflow(start_image="a.png", prompt="acena", frames=81, seed=1, prefix="t")
        self.assertNotIn("hand entering the frame", person["9"]["inputs"]["text"])

    def test_subject_scene_camera_rotates_and_replaces_written_camera(self) -> None:
        cameras = [motion.subject_camera({}, number) for number in range(1, 5)]
        self.assertEqual(len(set(cameras)), 4)  # roteiro antigo sem enquadramento: rodízio de quatro movimentos
        self.assertNotIn("orbita", motion.CAMERA_MOVES)  # saiu em 2026-10-13: deformava os objetos
        self.assertEqual(motion.subject_camera({"camera": "grua"}, 1), "grua")
        self.assertEqual(motion.subject_camera({"camera": "orbita"}, 2), cameras[1])
        prompt = motion.subject_clip_prompt("slow camera push-in toward the bowl, steam rising gently", "lateral")
        self.assertTrue(prompt.startswith(motion.CAMERA_MOVES["lateral"]))
        self.assertIn("steam rising gently", prompt)
        self.assertNotIn("push-in", prompt)

    def test_subject_scenes_get_varied_framing_and_camera_from_it(self) -> None:
        scenes = [{"showCharacter": False}, {}, {"showCharacter": False}, {"showCharacter": False, "framing": "detalhe"},
                  {"showCharacter": False}]
        script = motion.assign_framings({"slug": "barulho-rua", "scenes": scenes})
        framings = [scene.get("framing") for scene in script["scenes"]]
        self.assertIsNone(framings[1])  # cena com personagem não recebe enquadramento
        self.assertEqual(framings[3], "detalhe")  # o do roteiro vale
        self.assertEqual(len({framings[0], framings[2], framings[4]}), 3)  # sem repetir no mesmo vídeo
        starts = {motion.assign_framings({"slug": slug, "scenes": [{"showCharacter": False}]})["scenes"][0]["framing"]
                  for slug in ("a", "b", "c", "d", "e", "f", "g", "h")}
        self.assertGreater(len(starts), 1)  # vídeos diferentes começam em enquadramentos diferentes
        top = {"showCharacter": False, "framing": "de-cima", "keyframePrompt": "a bowl of oats"}
        self.assertTrue(motion.subject_prompt(top).startswith("top-down flat lay"))
        self.assertTrue(motion.subject_prompt(top).endswith("a bowl of oats"))
        self.assertEqual(motion.subject_camera(top, 1), "grua")
        self.assertEqual(motion.subject_camera({**top, "framing": "ambiente"}, 1), "afastamento")
        with self.assertRaisesRegex(ValueError, "framing"):
            base = {"narration": "a", "caption": "a", "keyframePrompt": "p", "motions": ["m"]}
            motion.validate_motion_script({"slug": "x", "title": "t", "scenes": [{**base, "framing": "close"}, base, base]})

    def test_prompts_keep_hands_out_of_pockets(self) -> None:
        keyframe = motion.keyframe_workflow(prompt="standing in a living room", seed=1, pose_image=None)
        video = motion.wan_i2v_workflow(start_image="a.png", prompt="waves", frames=81, seed=1, prefix="t")
        self.assertIn("without pockets", keyframe["2"]["inputs"]["text"])
        self.assertIn("(hands in pockets:1.9)", keyframe["3"]["inputs"]["text"])
        self.assertIn("(cargo shorts:1.4)", keyframe["3"]["inputs"]["text"])
        self.assertIn("hands in pockets", video["9"]["inputs"]["text"])

    def test_keyframe_workflow_applies_style_lora_from_visual_identity(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import perfis

        settings = perfis.get(perfis.LEGACY)
        with patch.dict(settings, {"lora": None}):
            self.assertNotIn("11", motion.keyframe_workflow(prompt="no parque", seed=1, pose_image=None))
        with patch.dict(settings, {"lora": {"file": "canopus.safetensors", "strength": 0.6, "trigger": "Pencil Art"}}):
            workflow = motion.keyframe_workflow(prompt="no parque", seed=1, pose_image="pose.png")
        self.assertEqual(workflow["11"]["inputs"]["lora_name"], "canopus.safetensors")
        self.assertEqual(workflow["11"]["inputs"]["strength_model"], 0.6)
        self.assertEqual(workflow["8"]["inputs"]["model"], ["11", 0])
        self.assertTrue(workflow["2"]["inputs"]["text"].startswith("Pencil Art,"))
        self.assertEqual(workflow["3"]["inputs"]["clip"], ["11", 1])

    def test_keyframe_workflow_uses_pose_control_only_when_requested(self) -> None:
        free = motion.keyframe_workflow(prompt="no parque", seed=1, pose_image=None)
        guided = motion.keyframe_workflow(prompt="marcha", seed=1, pose_image="pose.png")
        self.assertNotIn("6", free)
        self.assertEqual(guided["8"]["inputs"]["positive"], ["6", 0])

    def test_caption_overlay_is_transparent_full_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            avatar = root / "avatar.png"
            Image.new("RGB", (100, 100), "orange").save(avatar)
            result = motion.caption_overlay("Legenda de teste", avatar, root / "overlay.png")
            with Image.open(result) as overlay:
                self.assertEqual(overlay.mode, "RGBA")
                self.assertEqual(overlay.size, (motion.FRAME_WIDTH, motion.FRAME_HEIGHT))
                self.assertEqual(overlay.getpixel((10, 10))[3], 0)


def _motion_script(scene_count: int = 3) -> dict:
    return {
        "slug": "teste-movimento",
        "topic": "Teste",
        "scenes": [
            {
                "narration": f"Narração da cena {index}.",
                "caption": f"Cena {index}",
                "keyframePrompt": "standing in a living room",
                "motions": ["waves at the viewer"],
            }
            for index in range(1, scene_count + 1)
        ],
    }


class MotionScriptTests(unittest.TestCase):
    def test_validate_requires_visual_plan_fields(self) -> None:
        script = _motion_script()
        self.assertEqual(len(motion.validate_motion_script(script)), 3)
        del script["scenes"][1]["motions"]
        with self.assertRaisesRegex(ValueError, "motions"):
            motion.validate_motion_script(script)
        script = _motion_script()
        script["scenes"][0]["pose"] = "backflip"
        with self.assertRaisesRegex(ValueError, "pose"):
            motion.validate_motion_script(script)

    def test_prepare_keyframes_invalidates_only_changed_scenes(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "taumer").mkdir()
            Image.new("RGB", (50, 50), "orange").save(root / "taumer" / "avatar.png")
            voice = root / "voice.onnx"
            voice.write_bytes(b"x")
            generated: list[str] = []

            def fake_keyframe(scene, destination, *, seed=None, fmt="short", **_):
                generated.append(destination.parent.name)
                Image.new("RGB", (64, 112), "blue").save(destination)
                return destination

            def fake_piper(command, *, timeout, input_text=None):
                Path(command[command.index("--output_file") + 1]).write_bytes(b"wav")

            with (
                patch.object(motion, "GENERATED_ROOT", root),
                patch.dict("os.environ", {"PIPER_MODEL_PATH": str(voice)}),
                patch.object(motion, "_get_executable", side_effect=lambda _, default: default),
                patch.object(motion, "synthesize_narration",
                             side_effect=lambda text, dest, **_: fake_piper(["x", "--output_file", str(dest)], timeout=1)),
                patch.object(motion, "_audio_duration", return_value=5.0),
                patch.object(motion, "FORMAT_TARGETS", {"short": (0, 999), "video": (0, 999)}),
                patch.object(motion, "check_subject_scenes"),
                patch.object(motion, "check_scene_count"),
                patch.object(motion, "check_engagement"),
                patch("influencer_mcp.historico.check_new_script"),
                patch.object(motion, "generate_keyframe", side_effect=fake_keyframe),
            ):
                script = _motion_script()
                result = motion.prepare_motion_keyframes(influencer_name="Taumer", batch_id="lote", script=script)
                self.assertEqual(generated, ["scene-01", "scene-02", "scene-03"])
                clip = root / "taumer" / "lote" / "teste-movimento" / "scene-02" / "clip-1"
                clip.mkdir()
                generated.clear()
                script["scenes"][1]["keyframePrompt"] = "walking in a park"
                motion.prepare_motion_keyframes(influencer_name="Taumer", batch_id="lote", script=script)

            self.assertEqual(generated, ["scene-02"])
            self.assertFalse(clip.exists())
            self.assertTrue(Path(str(result["keyframeSheetPath"])).is_file())
            self.assertEqual(len(list(clip.parent.glob("keyframe-rejeitada-*.png"))), 1)


class MotionJobTests(unittest.TestCase):
    def test_job_without_live_process_is_reported_as_failed(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import motion_jobs

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(motion_jobs, "JOBS_DIR", Path(temp_dir)):
            job = motion_jobs.start_job("animation", {"video_slug": "x"}, spawn=False)
            self.assertEqual(job["state"], "queued")
            status = motion_jobs.job_status(job["id"])
            self.assertEqual(status["state"], "failed")
            self.assertIn("retoma", status["error"])
            with self.assertRaises(ValueError):
                motion_jobs.start_job("upload", {}, spawn=False)
            with self.assertRaises(ValueError):
                motion_jobs.job_status("../fora")


class ComfyReconnectTests(unittest.TestCase):
    def test_workflow_is_resubmitted_after_comfyui_restart(self) -> None:
        from unittest.mock import patch

        calls = iter([RuntimeError("Falha ao comunicar com o ComfyUI em x: recusado"), [{"filename": "ok.png"}]])

        def flaky(base, workflow, *, timeout):
            result = next(calls)
            if isinstance(result, Exception):
                raise result
            return result

        with (
            patch.object(motion, "_run_workflow_once", side_effect=flaky),
            patch.object(motion, "_wait_for_comfyui", return_value=True) as wait,
        ):
            self.assertEqual(motion._run_workflow("http://x", {}, timeout=10), [{"filename": "ok.png"}])
        wait.assert_called_once()

    def test_workflow_errors_are_not_retried(self) -> None:
        from unittest.mock import patch

        with patch.object(motion, "_run_workflow_once", side_effect=RuntimeError("O ComfyUI falhou no workflow")):
            with self.assertRaisesRegex(RuntimeError, "falhou no workflow"):
                motion._run_workflow("http://x", {}, timeout=10)


class MotionJobFileTests(unittest.TestCase):
    def test_job_file_write_retries_while_another_process_reads_it(self) -> None:
        import os as real_os
        from unittest.mock import patch

        from influencer_mcp import motion_jobs

        original_replace = real_os.replace
        failures = iter([PermissionError("em uso"), PermissionError("em uso")])

        def busy_then_free(source, destination):
            error = next(failures, None)
            if error:
                raise error
            original_replace(source, destination)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "job.json"
            with patch.object(motion_jobs.os, "replace", side_effect=busy_then_free), \
                    patch.object(motion_jobs.time, "sleep"):
                motion_jobs._write(path, {"state": "running"})
            self.assertIn("running", path.read_text(encoding="utf-8"))


class MotionQueueOrderTests(unittest.TestCase):
    def test_jobs_created_in_the_same_second_run_in_creation_order(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import motion_jobs

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(motion_jobs, "JOBS_DIR", Path(temp_dir)),
            patch.object(motion_jobs, "_pid_alive", return_value=True),
        ):
            ids = [motion_jobs.start_job("keyframes", {}, spawn=False)["id"] for _ in range(3)]
            self.assertTrue(motion_jobs._oldest_queued(ids[0]))
            self.assertFalse(motion_jobs._oldest_queued(ids[1]))
            self.assertFalse(motion_jobs._oldest_queued(ids[2]))


class OutputExportTests(unittest.TestCase):
    def test_video_and_txt_are_exported_with_title_as_name(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import media

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_dir = root / "generated" / "taumer" / "lote" / "video"
            video_dir.mkdir(parents=True)
            (video_dir / "motion-script.json").write_text("{}", encoding="utf-8")
            with patch.object(media, "GENERATED_ROOT", root / "generated"):
                save = lambda title: media.save_publish_metadata(
                    influencer_name="Taumer", video_slug="video", batch_id="lote", title=title,
                    description="Descrição.", hashtags=["#Shorts"], tags=["treino"], pinned_comment="Oi",
                )
                save("Equilíbrio depois dos 50: 5 exercícios?")
                self.assertFalse((root / "output").exists())
                (video_dir / "short.mp4").write_bytes(b"video")
                media.export_to_output(video_dir)
                shorts = root / "output" / "shorts"  # roteiro sem "format" = short
                exported = sorted(path.name for path in shorts.iterdir())
                self.assertEqual(exported, ["Equilíbrio depois dos 50 - 5 exercícios.mp4",
                                            "Equilíbrio depois dos 50 - 5 exercícios.txt"])
                save("Título novo")
                exported = sorted(path.name for path in shorts.iterdir())
                self.assertEqual(exported, ["Título novo.mp4", "Título novo.txt"])
                self.assertIn("TÍTULO\nTítulo novo", (shorts / "Título novo.txt").read_text(encoding="utf-8-sig"))
                # Formato vídeo: vai para output/videos, com a capa.
                (video_dir / "motion-script.json").write_text('{"format": "video"}', encoding="utf-8")
                (video_dir / "thumbnail.jpg").write_bytes(b"jpg")
                media.export_to_output(video_dir)
                videos = sorted(path.name for path in (root / "output" / "videos").iterdir())
                self.assertEqual(videos, ["Título novo.jpg", "Título novo.mp4", "Título novo.txt"])


class MotionMetadataTests(unittest.TestCase):
    def test_metadata_can_be_saved_while_motion_video_is_rendering(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import media

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_dir = root / "taumer" / "lote" / "teste-movimento"
            video_dir.mkdir(parents=True)
            (video_dir / "motion-script.json").write_text("{}", encoding="utf-8")
            with patch.object(media, "GENERATED_ROOT", root):
                path = media.save_publish_metadata(
                    influencer_name="Taumer", video_slug="teste-movimento", batch_id="lote", title="Título",
                    description="Descrição.", hashtags=["#Shorts"], tags=["treino"], pinned_comment="Oi",
                )
            self.assertIn('"videoReady": false', path.read_text(encoding="utf-8"))
            text = (path.parent / "youtube-metadata.txt").read_text(encoding="utf-8-sig")
            self.assertIn("TÍTULO\nTítulo", text)
            self.assertIn("Descrição.\n\n#Shorts", text)
            self.assertIn("TAGS (separadas por vírgula)\ntreino", text)
            self.assertIn("ainda em produção", text)


class CaptionOverlayTests(unittest.TestCase):
    def test_empty_caption_draws_no_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            alpha = Image.open(motion.caption_overlay("", folder / "sem-avatar.png", folder / "o.png")).getchannel("A")
            self.assertIsNone(alpha.getbbox())
            alpha = Image.open(motion.caption_overlay("Legenda", folder / "sem-avatar.png", folder / "o.png")).getchannel("A")
            self.assertIsNotNone(alpha.getbbox())



class AllowObjectsTests(unittest.TestCase):
    def test_dumbbells_are_forbidden_by_default_and_released_per_scene(self):
        self.assertIn("(dumbbells:1.5)", motion.keyframe_negative())
        released = motion.keyframe_negative(["dumbbells"])
        self.assertNotIn("dumbbells", released)
        self.assertIn("(tank top:1.4)", released)
        workflow = motion.keyframe_workflow(prompt="x", seed=1, pose_image=None, allowed_objects=["dumbbells"])
        self.assertNotIn("dumbbells", workflow["3"]["inputs"]["text"])

    def test_unknown_allowed_object_is_rejected(self):
        scene = {"narration": " ".join(["palavra"] * 26), "caption": "c", "keyframePrompt": "p",
                 "motions": ["a", "b"], "allowObjects": ["cane"]}
        with self.assertRaisesRegex(ValueError, "allowObjects"):
            motion.validate_motion_script({"slug": "s", "scenes": [dict(scene) for _ in range(6)]})


class FormatTests(unittest.TestCase):
    def test_keyframe_and_wan_sizes_follow_format(self):
        short = motion.keyframe_workflow(prompt="x", seed=1, pose_image=None)
        video = motion.keyframe_workflow(prompt="x", seed=1, pose_image=None, fmt="video")
        self.assertEqual((short["7"]["inputs"]["width"], short["7"]["inputs"]["height"]), (768, 1344))
        self.assertEqual((video["7"]["inputs"]["width"], video["7"]["inputs"]["height"]), (1344, 768))
        self.assertIn("vertical composition", short["2"]["inputs"]["text"])
        self.assertIn("16:9", video["2"]["inputs"]["text"])
        wan = motion.wan_i2v_workflow(start_image="a.png", prompt="m", frames=33, seed=1, prefix="p", fmt="video")
        self.assertEqual((wan["12"]["inputs"]["width"], wan["12"]["inputs"]["height"]), (832, 480))

    def test_pose_guide_is_widened_for_video(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp_dir:
            guide = Path(temp_dir) / "g.png"
            Image.new("RGB", (512, 896), (0, 0, 0)).save(guide)
            motion.widen_pose_guide(guide)
            with Image.open(guide) as opened:
                self.assertEqual(opened.size, (896, 512))

    def test_overlay_matches_frame_and_invalid_format_fails(self):
        from PIL import Image
        from influencer_mcp import formats
        with tempfile.TemporaryDirectory() as temp_dir:
            out = motion.caption_overlay("", Path(temp_dir) / "sem-avatar.png", Path(temp_dir) / "o.png", fmt="video")
            with Image.open(out) as opened:
                self.assertEqual(opened.size, (1920, 1080))
        self.assertEqual(formats.normalize("Vídeo"), "video")
        with self.assertRaises(ValueError):
            formats.normalize("reels")

    def test_thumbnail_is_1280x720(self):
        from PIL import Image
        from influencer_mcp.thumbnails import compose_thumbnail
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "b.png"
            Image.new("RGB", (1344, 768), (90, 120, 160)).save(base)
            out = compose_thumbnail(base, "Saúde mental depois dos 50", Path(temp_dir) / "t.jpg")
            with Image.open(out) as opened:
                self.assertEqual(opened.size, (1280, 720))


class NoAvatarTests(unittest.TestCase):
    def test_overlay_without_avatar_is_fully_transparent(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar = Path(temp_dir) / "avatar.png"
            Image.new("RGB", (300, 300), (255, 0, 0)).save(avatar)
            out = motion.caption_overlay("", None, Path(temp_dir) / "o.png")
            with Image.open(out) as opened:
                self.assertIsNone(opened.getbbox())  # nada desenhado


# Os testes deste arquivo usam o canal fictício "Taumer" (check_influencer só aceita o canal configurado).
_env_patch = None


def setUpModule():
    global _env_patch
    from unittest.mock import patch as _patch
    _env_patch = _patch.dict("os.environ", {"INFLUENCER_NAME": "Taumer"})
    _env_patch.start()


def tearDownModule():
    _env_patch.stop()


if __name__ == "__main__":
    unittest.main()


class QueueWaitTimeoutTests(unittest.TestCase):
    def test_timeout_counts_only_after_the_workflow_leaves_the_comfyui_queue(self):
        from unittest.mock import patch

        from influencer_mcp import motion

        clock = iter(range(0, 10_000, 100))
        pending = iter([True] * 5 + [False] * 50)

        def fake_request(url, payload=None, timeout=30):
            if url.endswith("/prompt"):
                return {"prompt_id": "abc"}
            if "/history/" in url:
                return {}
            return {"queue_pending": [[1, "abc"]] if next(pending) else []}

        with patch.object(motion, "_request_json", side_effect=fake_request), \
                patch.object(motion.time, "monotonic", side_effect=lambda: next(clock)), \
                patch.object(motion.time, "sleep"):
            with self.assertRaises(TimeoutError):
                motion._run_workflow_once("http://x", {}, timeout=250)
        # 5 voltas esperando na fila + ~2 rodando antes de estourar (sem a regra seriam só ~2 voltas)
        self.assertGreaterEqual(next(clock), 1300)


class PrenarrationTests(unittest.TestCase):
    """Narração na CPU enquanto o job espera a GPU (pedido de 2026-10-05)."""

    def _patches(self, root, narrated, seconds=5.0):
        from unittest.mock import patch

        def fake_tts(text, dest, **_):
            narrated.append(dest.parent.name)
            Path(dest).write_bytes(b"wav")

        return (
            patch.object(motion, "GENERATED_ROOT", root),
            patch.object(motion, "_get_executable", side_effect=lambda _, default: default),
            patch.object(motion, "synthesize_narration", side_effect=fake_tts),
            patch.object(motion, "_audio_duration", return_value=seconds),
            patch.object(motion, "FORMAT_TARGETS", {"short": (10, 20), "video": (10, 20)}),
            patch.object(motion, "check_subject_scenes"),
            patch.object(motion, "check_scene_count"),
            patch.object(motion, "check_engagement"),
            patch("influencer_mcp.historico.check_new_script"),
            patch.object(motion, "generate_keyframe",
                         side_effect=lambda scene, dest, **_: Image.new("RGB", (8, 8)).save(dest)),
        )

    def test_gpu_step_reuses_narration_made_while_waiting(self) -> None:
        from contextlib import ExitStack

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            narrated: list[str] = []
            for item in self._patches(Path(temp_dir), narrated):
                stack.enter_context(item)
            script = _motion_script()
            self.assertEqual(motion.prenarrate_keyframes(influencer_name="Taumer", batch_id="lote", script=script), 15.0)
            self.assertEqual(narrated, ["scene-01", "scene-02", "scene-03"])
            narrated.clear()
            motion.prepare_motion_keyframes(influencer_name="Taumer", batch_id="lote", script=script)
            self.assertEqual(narrated, [])  # nada narrado de novo na vez da GPU

    def test_wrong_duration_fails_before_the_gpu(self) -> None:
        from contextlib import ExitStack

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            for item in self._patches(Path(temp_dir), [], seconds=9.0):
                stack.enter_context(item)
            with self.assertRaisesRegex(ValueError, "27.0s"):
                motion.prenarrate_keyframes(influencer_name="Taumer", batch_id="lote", script=_motion_script())
            self.assertFalse((Path(temp_dir) / "taumer" / "lote" / "teste-movimento" / "motion-script.json").exists())

    def test_keyframe_job_fails_without_taking_the_gpu(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import motion_jobs

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(motion_jobs, "JOBS_DIR", Path(temp_dir)), \
                patch.object(motion_jobs, "LOCK_PATH", Path(temp_dir) / "gpu.lock"):
            job = motion_jobs.start_job("keyframes", {"influencer_name": "T", "batch_id": "b", "script": {}}, spawn=False)
            path = Path(temp_dir) / f"{job['id']}.json"
            with patch.object(motion_jobs, "_gpu_free_now", return_value=False), \
                    patch.object(motion, "prenarrate_keyframes", side_effect=ValueError("A narração tem 61.0s")), \
                    patch.object(motion_jobs, "_acquire_gpu") as acquire:
                motion_jobs.run_job(path)
            acquire.assert_not_called()
            self.assertEqual(motion_jobs._read(path)["state"], "failed")
            self.assertIn("61.0s", motion_jobs._read(path)["error"])


class ThenAnimateTests(unittest.TestCase):
    def test_redone_keyframes_of_finished_video_queue_the_animation(self) -> None:
        from unittest.mock import patch

        from influencer_mcp import motion_jobs

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(motion_jobs, "JOBS_DIR", Path(temp_dir)), \
                patch.object(motion_jobs, "LOCK_PATH", Path(temp_dir) / "gpu.lock"):
            job = motion_jobs.start_job("keyframes", {"influencer_name": "T", "batch_id": "b", "gpu": "local",
                                                      "script": {"slug": "v"}, "scene_numbers": [3, 4],
                                                      "thenAnimate": True}, spawn=False)
            started = []
            real_start = motion_jobs.start_job
            with patch.object(motion_jobs, "_gpu_free_now", return_value=True), \
                    patch.object(motion_jobs, "_acquire_gpu"), \
                    patch("influencer_mcp.comfyui.ensure_running"), \
                    patch.object(motion, "prepare_motion_keyframes", return_value={}), \
                    patch.object(motion_jobs, "start_job",
                                 side_effect=lambda kind, params: started.append((kind, params)) or {"id": "a1"}):
                motion_jobs.run_job(Path(temp_dir) / f"{job['id']}.json")
            self.assertEqual(motion_jobs._read(Path(temp_dir) / f"{job['id']}.json")["state"], "done")
            self.assertEqual(started, [("animation", {"influencer_name": "T", "batch_id": "b", "video_slug": "v",
                                                      "gpu": "local", "scene_numbers": [3, 4], "urgent": False})])
            del real_start


class EngagementRulesTests(unittest.TestCase):
    """Análise do canal (2026-10-06): pergunta no meio e movimento já na cena 1."""

    def _scenes(self):
        return [{"narration": "Gancho forte.", "exercise": "biceps_curl"}, {"narration": "Explicação."},
                {"narration": "Você consegue? Responde 1, 2 ou 3."}, {"narration": "Fim. Conta aqui?"}]

    def test_question_in_the_middle_and_movement_in_the_first_scene(self):
        motion.check_engagement(self._scenes())
        no_middle = self._scenes()
        no_middle[2]["narration"] = "Sem pergunta."
        with self.assertRaisesRegex(ValueError, "MEIO"):
            motion.check_engagement(no_middle)  # a pergunta só no fim não conta
        late = self._scenes()
        del late[0]["exercise"]
        late[1]["exercise"] = "biceps_curl"
        with self.assertRaisesRegex(ValueError, "cena 1"):
            motion.check_engagement(late)


class LibraryOnlyTests(unittest.TestCase):
    """Pedido de 2026-10-07: o vídeo só sugere exercícios da biblioteca."""

    def scenes(self, *narrations, exercise=None):
        return [{"narration": text, "exercise": exercise} for text in narrations]

    def test_exercises_outside_the_library_are_refused(self):
        for text in ("Torço a toalha como quem torce roupa.", "Encolho os ombros devagar.",
                     "Dou um passo à frente: afundo curto.", "Trote leve de vinte segundos.",
                     "Pé inteiro no degrau, empurro e subo.", "Alongo a panturrilha no poste.",
                     "Gire o tronco devagar.", "Agachamento parado na parede."):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "biblioteca"):
                motion.check_library_only(self.scenes(text))

    def test_library_exercise_named_without_exercise_field_is_refused(self):
        with self.assertRaisesRegex(ValueError, "exercise"):
            motion.check_library_only(self.scenes("Agora a rosca: cotovelos colados no corpo."))
        motion.check_library_only(self.scenes("Agora a rosca: cotovelos colados no corpo.", exercise="biceps_curl"))

    def test_library_and_everyday_scenes_pass(self):
        motion.check_library_only(
            self.scenes("Suba na ponta dos pés devagar.", exercise="standing_calf_raise")
            + self.scenes("Alongamento lateral: braço direito acima da cabeça.", exercise="side_stretch")
            + self.scenes("Ando pelo corredor depois do café, com a coluna longa."))


class ParallelAnimationTests(unittest.TestCase):
    """A GPU só é liberada depois que todos os quadros desceram; cada cena é montada em segundo plano."""

    def run_short(self, compose_error=None):
        import json
        import threading
        from concurrent.futures import Future
        from unittest.mock import patch

        root = Path(tempfile.mkdtemp())
        scenes = [{"narration": "a", "caption": "a", "keyframePrompt": "k", "motions": ["m"]} for _ in range(3)]
        (root / "motion-script.json").write_text(json.dumps({"slug": "v", "scenes": scenes}), encoding="utf-8")
        for index in range(1, 4):
            (root / f"scene-{index:02d}").mkdir()
            (root / f"scene-{index:02d}" / "keyframe.png").write_bytes(b"")
            (root / f"scene-{index:02d}" / "narration.wav").write_bytes(b"")
        events, lock = [], threading.Lock()

        def fake_animate(scene, keyframe, duration, work_dir, *, background, pending, **_):
            def download():
                with lock:
                    events.append(f"download {work_dir.name}")
            pending.append(background.submit(download))
            return [work_dir / "hd-0001.png"]

        def fake_compose(frames, narration, overlay, segment, ffmpeg, fmt):
            if compose_error:
                raise compose_error
            with lock:
                events.append(f"compose {segment.parent.name}")

        def gpu_done():
            with lock:
                events.append("gpu_done")

        with patch.object(motion, "motion_output_dir", return_value=root), \
                patch.object(motion, "validate_motion_script", return_value=scenes), \
                patch.object(motion, "_get_executable", return_value="x"), \
                patch.object(motion, "_audio_duration", return_value=8.0), \
                patch.object(motion, "check_duration"), patch.object(motion, "animate_scene", side_effect=fake_animate), \
                patch.object(motion, "caption_overlay", return_value=root / "o.png"), \
                patch.object(motion, "compose_scene", side_effect=fake_compose), \
                patch.object(motion, "_join_segments", return_value=(root / "short.mp4", 24.0, root / "r.png", None)), \
                patch.object(motion, "localize_if_ready", return_value=None):
            result = motion.animate_motion_short(influencer_name="T", batch_id="b", video_slug="v", gpu_done=gpu_done)
        return events, result

    def test_gpu_released_after_every_download_and_all_scenes_composed(self):
        events, result = self.run_short()
        gpu = events.index("gpu_done")
        self.assertTrue(all(events.index(f"download scene-0{n}") < gpu for n in (1, 2, 3)))
        self.assertEqual(sorted(e for e in events if e.startswith("compose")), [f"compose scene-0{n}" for n in (1, 2, 3)])
        self.assertEqual(len(result["scenes"]), 3)

    def test_composition_error_reaches_the_job(self):
        with self.assertRaises(RuntimeError):
            self.run_short(compose_error=RuntimeError("ffmpeg falhou"))
