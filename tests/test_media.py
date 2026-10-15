import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from influencer_mcp import media
from influencer_mcp.pose_validation import PoseAssessment


class NarrationEngineTests(unittest.TestCase):
    def test_kokoro_is_the_default_voice_and_piper_remains_available(self) -> None:
        commands: list[list[str]] = []
        envs: list = []

        def capture(command, *, timeout, input_text=None, extra_env=None):
            commands.append(command)
            envs.append(extra_env)

        with tempfile.TemporaryDirectory() as temp_dir:
            model = Path(temp_dir) / "voz.onnx"
            model.write_bytes(b"x")
            with (
                patch.object(media, "_run_checked", side_effect=capture),
                patch.object(media, "_get_executable", side_effect=lambda _, default: default),
                patch.dict("os.environ", {"PIPER_MODEL_PATH": str(model)}, clear=False),
            ):
                import os
                for key in ("TTS_ENGINE", "KOKORO_VOICE", "KOKORO_SPEED"):
                    os.environ.pop(key, None)
                media.synthesize_narration("Olá, ação!", Path(temp_dir) / "a.wav")
                self.assertEqual(media.narration_voice(), "kokoro:pm_santa:1.0")
                with patch.dict("os.environ", {"TTS_ENGINE": "piper"}):
                    media.synthesize_narration("Olá", Path(temp_dir) / "b.wav")
                    self.assertEqual(media.narration_voice(), "piper:voz.onnx")
        self.assertTrue(commands[0][1].endswith("kokoro_tts.py"))
        self.assertEqual(commands[0][commands[0].index("--voice") + 1], "pm_santa")
        self.assertIn("--model", commands[1])
        # Kokoro sempre na CPU, com núcleos limitados (na GPU disputava memória com o ComfyUI); o Piper não muda.
        self.assertEqual(envs[0]["CUDA_VISIBLE_DEVICES"], "")
        self.assertEqual(envs[0]["OMP_NUM_THREADS"], "4")
        self.assertIsNone(envs[1])


class LocalMediaTests(unittest.TestCase):
    def test_render_short_validates_requested_pose_before_assembling_each_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            avatar_dir = root / "historiador"
            avatar_dir.mkdir()
            avatar_path = avatar_dir / "avatar.png"
            Image.new("RGB", (120, 120), "orange").save(avatar_path)
            voice_model = root / "voice.onnx"
            voice_model.write_bytes(b"test model")
            script = {
                "slug": "movimento-seguro",
                "scenes": [
                    {
                        "narration": f"Orientação da cena {index}.",
                        "caption": f"Cena {index}",
                        "visualPrompt": "Exercício em casa ao lado de uma cadeira.",
                        "pose": pose,
                    }
                    for index, pose in enumerate(("standing", "squat", "plank"), 1)
                ],
            }

            def fake_command(command: list[str], *, timeout: int, input_text: str | None = None, extra_env=None):
                if "--output_file" in command:
                    output = Path(command[command.index("--output_file") + 1])
                else:
                    output = Path(command[-1])
                output.write_bytes(b"test output")
                return None

            rendered_scenes: list[tuple[str, str]] = []

            def fake_controlled_scene(prompt: str, pose_name: str, destination: Path) -> None:
                rendered_scenes.append((prompt, pose_name))
                Image.new("RGB", (576, 1024), "blue").save(destination)

            results = iter((False, True, True, True))

            def fake_pose_assessment(_image: Path, pose: str) -> PoseAssessment:
                passed = next(results)
                return PoseAssessment(
                    expected_pose=pose,
                    detected_people=1,
                    confidence=0.9,
                    passed=passed,
                    reason="pose compatível" if passed else "pose recusada no primeiro candidato",
                    measurements={},
                )

            video_dir = root / "historiador" / "2026-09-27-2300" / "movimento-seguro"
            video_dir.mkdir(parents=True)
            stale_attempt = video_dir / "scene-3-attempt-3.png"
            stale_attempt.write_bytes(b"old candidate")
            (video_dir / "youtube-metadata.json").write_text("{}", encoding="utf-8")

            with (
                patch.object(media, "GENERATED_ROOT", root),
                patch.dict("os.environ", {"PIPER_MODEL_PATH": str(voice_model)}),
                patch.object(media, "_get_executable", side_effect=lambda _, default: default),
                patch.object(media, "_run_checked", side_effect=fake_command),
                patch.object(media, "_audio_duration", return_value=3.0),
                patch.object(media, "_generate_controlled_scene", side_effect=fake_controlled_scene),
                patch.object(
                    media,
                    "validate_pose_image",
                    side_effect=fake_pose_assessment,
                ) as validate_pose,
            ):
                result = media.render_short(
                    influencer_name="Historiador",
                    batch_id="2026-09-27-2300",
                    script=script,
                )

            self.assertEqual(result["sceneCount"], 3)
            self.assertEqual(result["durationSeconds"], 3.0)
            self.assertTrue(Path(str(result["videoPath"])).is_file())
            self.assertEqual(
                [pose for _, pose in rendered_scenes],
                ["standing", "standing", "squat", "plank"],
            )
            self.assertTrue(all(prompt == "Exercício em casa ao lado de uma cadeira." for prompt, _ in rendered_scenes))
            self.assertEqual(validate_pose.call_count, 4)
            validation = json.loads(
                (root / "historiador" / "2026-09-27-2300" / "movimento-seguro" / "pose-validation.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual([item["expected_pose"] for item in validation], ["standing", "squat", "plank"])
            self.assertEqual(len(validation[0]["attempts"]), 2)
            self.assertFalse(validation[0]["attempts"][0]["passed"])
            self.assertTrue(validation[0]["attempts"][1]["passed"])
            self.assertEqual(validation[0]["selected_image"], "scene-1.png")
            self.assertFalse(stale_attempt.exists())
            self.assertFalse((video_dir / "youtube-metadata.json").exists())
            with Image.open(result["contactSheetPath"]) as sheet:
                self.assertGreater(sheet.width, sheet.height)

    def test_render_short_stops_when_mediapipe_rejects_a_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            avatar_dir = root / "historiador"
            avatar_dir.mkdir()
            Image.new("RGB", (120, 120), "orange").save(avatar_dir / "avatar.png")
            voice_model = root / "voice.onnx"
            voice_model.write_bytes(b"test model")
            script = {
                "slug": "postura-incoerente",
                "scenes": [
                    {
                        "narration": "Demonstração breve.",
                        "caption": "Movimento",
                        "visualPrompt": "One person doing a plank.",
                        "pose": "plank",
                    }
                ] * 3,
            }

            def fake_scene(_prompt: str, _pose: str, destination: Path) -> None:
                Image.new("RGB", (576, 1024), "blue").save(destination)

            with (
                patch.object(media, "GENERATED_ROOT", root),
                patch.dict("os.environ", {"PIPER_MODEL_PATH": str(voice_model)}),
                patch.object(media, "_get_executable", side_effect=lambda _, default: default),
                patch.object(
                    media,
                    "_generate_controlled_scene",
                    side_effect=fake_scene,
                ) as generate_scene,
                patch.object(
                    media,
                    "validate_pose_image",
                    return_value=PoseAssessment(
                        expected_pose="plank",
                        detected_people=1,
                        confidence=0.91,
                        passed=False,
                        reason="a linha do tronco até os tornozelos não está aproximadamente horizontal",
                        measurements={"body_line_height": 0.4},
                    ),
                ),
                patch.object(media, "_run_checked") as run_media_command,
            ):
                with self.assertRaisesRegex(RuntimeError, "rejeitada pelo filtro de pose MediaPipe"):
                    media.render_short(
                        influencer_name="Historiador",
                        batch_id="2026-09-27-2300",
                        script=script,
                    )

            run_media_command.assert_not_called()
            self.assertEqual(generate_scene.call_count, media.MAX_POSE_GENERATION_ATTEMPTS)
            output = root / "historiador" / "2026-09-27-2300" / "postura-incoerente"
            validation = json.loads((output / "pose-validation.json").read_text(encoding="utf-8"))
            self.assertFalse(validation[0]["passed"])
            self.assertEqual(len(validation[0]["attempts"]), media.MAX_POSE_GENERATION_ATTEMPTS)
            self.assertFalse((output / "scene-1-captioned.png").exists())

    def test_controlled_workflow_uses_pose_control_and_realistic_pencil_style(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "COMFYUI_SCENE_CHECKPOINT": "realistic-test.safetensors",
                "COMFYUI_OPENPOSE_CONTROLNET": "openpose-test.safetensors",
                "COMFYUI_SCENE_LORA": "pencil-test.safetensors",
                "COMFYUI_SCENE_LORA_STRENGTH": "0.6",
            },
        ):
            workflow = media._controlled_scene_workflow(
                prompt="An adult performs a bodyweight squat beside a chair.",
                pose_name="squat",
                pose_image="scene-pose.png",
                seed=123,
            )

        self.assertEqual(workflow["1"]["inputs"]["ckpt_name"], "realistic-test.safetensors")
        self.assertEqual(workflow["4"]["inputs"]["control_net_name"], "openpose-test.safetensors")
        self.assertEqual(workflow["5"]["inputs"]["image"], "scene-pose.png")
        self.assertEqual(workflow["6"]["inputs"]["image"], ["5", 0])
        self.assertEqual(workflow["6"]["inputs"]["strength"], 1.0)
        self.assertEqual(workflow["6"]["inputs"]["end_percent"], 1.0)
        self.assertEqual(workflow["8"]["inputs"]["positive"], ["6", 0])
        self.assertEqual(workflow["8"]["inputs"]["seed"], 123)
        self.assertIn("graphite pencil sketch", workflow["2"]["inputs"]["text"])
        self.assertIn("Pencil Art", workflow["2"]["inputs"]["text"])
        self.assertIn("cross-hatching", workflow["2"]["inputs"]["text"])
        self.assertIn("bodyweight squat beside a chair", workflow["2"]["inputs"]["text"])
        self.assertLess(workflow["2"]["inputs"]["text"].index("bodyweight squat"), workflow["2"]["inputs"]["text"].index("graphite pencil sketch"))
        self.assertIn("fully dressed adult man", workflow["2"]["inputs"]["text"])
        self.assertIn("Detailed face looking straight ahead", workflow["2"]["inputs"]["text"])
        self.assertIn("looking up", workflow["3"]["inputs"]["text"])
        self.assertIn("opaque navy short-sleeved crew-neck T-shirt:1.4) covering chest", workflow["2"]["inputs"]["text"])
        self.assertIn("(shirtless:1.7)", workflow["3"]["inputs"]["text"])
        self.assertIn("tank top", workflow["3"]["inputs"]["text"])
        self.assertIn("headless", workflow["3"]["inputs"]["text"])
        self.assertIn("Average natural build", workflow["2"]["inputs"]["text"])
        self.assertLessEqual(len(workflow["2"]["inputs"]["text"]), 450)
        self.assertIn("plain white background", workflow["2"]["inputs"]["text"])
        self.assertEqual(workflow["7"]["inputs"]["width"], 768)
        self.assertEqual(workflow["7"]["inputs"]["height"], 1344)
        self.assertEqual(workflow["8"]["inputs"]["steps"], 35)
        self.assertEqual(workflow["8"]["inputs"]["sampler_name"], "dpmpp_2m")
        self.assertEqual(workflow["8"]["inputs"]["scheduler"], "karras")
        self.assertIn("duplicate person", workflow["3"]["inputs"]["text"])
        self.assertEqual(workflow["11"]["class_type"], "LoraLoader")
        self.assertEqual(workflow["11"]["inputs"]["lora_name"], "pencil-test.safetensors")
        self.assertEqual(workflow["11"]["inputs"]["strength_model"], 0.6)
        self.assertEqual(workflow["2"]["inputs"]["clip"], ["11", 1])
        self.assertEqual(workflow["8"]["inputs"]["model"], ["11", 0])

    def test_controlled_workflow_defaults_to_matching_sdxl_models(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            workflow = media._controlled_scene_workflow(
                prompt="An adult performs a bodyweight squat.",
                pose_name="squat",
                pose_image="scene-pose.png",
                seed=123,
            )

        self.assertEqual(
            workflow["1"]["inputs"]["ckpt_name"],
            "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
        )
        self.assertEqual(
            workflow["4"]["inputs"]["control_net_name"],
            "xinsir-openpose-sdxl.safetensors",
        )
        self.assertEqual(workflow["7"]["inputs"]["width"], 768)
        self.assertEqual(workflow["7"]["inputs"]["height"], 1344)
        self.assertNotIn("11", workflow)

    def test_horizontal_scene_workflow_uses_landscape_canvas(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            workflow = media._controlled_scene_workflow(
                prompt="An adult holds a strict forearm plank.",
                pose_name="plank",
                pose_image="scene-pose.png",
                seed=123,
            )

        self.assertEqual(workflow["7"]["inputs"]["width"], 1344)
        self.assertEqual(workflow["7"]["inputs"]["height"], 768)

    def test_rerender_scenes_regenerates_only_selected_scenes_and_keeps_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "historiador").mkdir()
            Image.new("RGB", (120, 120), "orange").save(root / "historiador" / "avatar.png")
            voice_model = root / "voice.onnx"
            voice_model.write_bytes(b"test model")
            script = {
                "slug": "movimento-seguro",
                "scenes": [
                    {"narration": f"Cena {index}.", "caption": f"Cena {index}",
                     "visualPrompt": "Standing upright", "pose": "standing"}
                    for index in range(1, 4)
                ],
            }
            piper_calls: list[str] = []

            def fake_command(command: list[str], *, timeout: int, input_text: str | None = None, extra_env=None):
                if "--output_file" in command:
                    piper_calls.append(input_text or "")
                    output = Path(command[command.index("--output_file") + 1])
                else:
                    output = Path(command[-1])
                output.write_bytes(b"test output")

            rendered: list[str] = []

            def fake_scene(prompt: str, pose_name: str, destination: Path) -> None:
                rendered.append(destination.name)
                Image.new("RGB", (576, 1024), "blue").save(destination)

            def passing(_image: Path, pose: str) -> PoseAssessment:
                return PoseAssessment(pose, 1, 0.9, True, "ok", {})

            with (
                patch.object(media, "GENERATED_ROOT", root),
                patch.dict("os.environ", {"PIPER_MODEL_PATH": str(voice_model)}),
                patch.object(media, "_get_executable", side_effect=lambda _, default: default),
                patch.object(media, "_run_checked", side_effect=fake_command),
                patch.object(media, "_audio_duration", return_value=3.0),
                patch.object(media, "_generate_controlled_scene", side_effect=fake_scene),
                patch.object(media, "validate_pose_image", side_effect=passing),
            ):
                media.render_short(influencer_name="Historiador", batch_id="lote", script=script)
                rendered.clear()
                piper_calls.clear()
                result = media.rerender_scenes(
                    influencer_name="Historiador",
                    batch_id="lote",
                    video_slug="movimento-seguro",
                    scene_numbers=[2],
                )

            self.assertEqual(rendered, ["scene-2-attempt-2.png"])
            self.assertEqual(piper_calls, [])
            self.assertEqual(result["rerenderedScenes"], [2])
            validation = json.loads(Path(str(result["poseValidationPath"])).read_text(encoding="utf-8"))
            self.assertEqual(validation[1]["rejectedInReview"], ["scene-2-attempt-1.png"])
            self.assertEqual([a["attempt"] for a in validation[1]["attempts"]], [1, 2])

    def test_render_short_accepts_long_shorts_but_limits_scene_count(self) -> None:
        scene = {"narration": "Movimento.", "caption": "Cena", "visualPrompt": "Standing upright", "pose": "standing"}
        with self.assertRaisesRegex(ValueError, "3 a 14 cenas"):
            media.render_short(
                influencer_name="Historiador",
                batch_id="lote",
                script={"slug": "longo", "scenes": [scene] * 15},
            )
        long_narration = {**scene, "narration": " ".join(["palavra"] * 60)}
        with self.assertRaisesRegex(ValueError, "passa de 180 segundos"):
            media.render_short(
                influencer_name="Historiador",
                batch_id="lote",
                script={"slug": "longo", "scenes": [long_narration] * 12},
            )

    def test_render_short_rejects_missing_or_unsupported_scene_pose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            media, "GENERATED_ROOT", Path(temp_dir)
        ):
            scene = {"narration": "Uma dica.", "visualPrompt": "Em casa."}
            script = {"slug": "teste", "scenes": [scene, scene, scene]}
            with self.assertRaisesRegex(ValueError, "pose ilustrável"):
                media.render_short(
                    influencer_name="Historiador",
                    batch_id="2026-09-27-2300",
                    script=script,
                )

            invalid_script = {
                "slug": "teste",
                "scenes": [{**scene, "pose": "backflip"} for _ in range(3)],
            }
            with self.assertRaisesRegex(ValueError, "não é compatível"):
                media.render_short(
                    influencer_name="Historiador",
                    batch_id="2026-09-27-2300",
                    script=invalid_script,
                )

    def test_captioned_image_adds_avatar_and_outputs_short_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            illustration = root / "scene.png"
            avatar = root / "avatar.png"
            output = root / "captioned.png"
            Image.new("RGB", (576, 1024), "blue").save(illustration)
            Image.new("RGB", (300, 300), "orange").save(avatar)

            media._captioned_image(illustration, output, "Agachamento com boa postura", avatar)

            with Image.open(output) as result:
                self.assertEqual(result.size, (1080, 1920))
                self.assertEqual(result.mode, "RGB")

    def test_captioned_image_preserves_full_width_horizontal_illustrations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            illustration = root / "landscape-scene.png"
            avatar = root / "avatar.png"
            output = root / "captioned.png"
            image = Image.new("RGB", (1400, 800), "white")
            for x in range(12):
                for y in range(800):
                    image.putpixel((x, y), (0, 0, 255))
                    image.putpixel((1399 - x, y), (0, 0, 255))
            image.save(illustration)
            Image.new("RGB", (300, 300), "orange").save(avatar)

            media._captioned_image(illustration, output, "Prancha", avatar)

            with Image.open(output) as result:
                self.assertEqual(result.size, (1080, 1920))
                self.assertEqual(result.getpixel((2, 960)), (0, 0, 255))
                self.assertEqual(result.getpixel((1077, 960)), (0, 0, 255))

    def test_avatar_is_not_regenerated_unless_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_dir = Path(temp_dir) / "luna"
            avatar_dir.mkdir()
            existing = avatar_dir / "avatar.png"
            existing.write_bytes(b"approved avatar")

            with patch.object(media, "GENERATED_ROOT", Path(temp_dir)), patch.object(
                media, "_generate_image"
            ) as generate:
                result = media.create_avatar("Luna", "friendly educator", "curly hair")

            self.assertEqual(result.read_bytes(), b"approved avatar")
            generate.assert_not_called()

    def test_metadata_includes_ready_to_upload_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_dir = root / "Luna" / "2026-09-27-1200" / "mobilidade"
            video_dir.mkdir(parents=True)
            (video_dir / "short.mp4").write_bytes(b"video")

            with patch.object(media, "GENERATED_ROOT", root):
                result = media.save_publish_metadata(
                    influencer_name="Luna",
                    batch_id="2026-09-27-1200",
                    video_slug="mobilidade",
                    title="Melhore sua mobilidade",
                    description="Uma dica simples para começar.",
                    hashtags=["#mobilidade", "exercicio"],
                    tags=["mobilidade", "exercício"],
                    pinned_comment="Qual exercício você quer ver?",
                )

            metadata = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(
                metadata["descriptionForUpload"],
                "Uma dica simples para começar.\n\n#mobilidade #exercicio",
            )
            self.assertEqual(metadata["publication"], "manual")


# Os testes deste arquivo usam o canal fictício "Luna" (check_influencer só aceita o canal configurado).
_env_patch = None


def setUpModule():
    global _env_patch
    from unittest.mock import patch as _patch
    _env_patch = _patch.dict("os.environ", {"INFLUENCER_NAME": "Luna"})
    _env_patch.start()


def tearDownModule():
    _env_patch.stop()


if __name__ == "__main__":
    unittest.main()
