import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from influencer_mcp import exercicios as ex
from influencer_mcp import motion


def _angles_of(exercise, which):
    return ex.angles(exercise.points(which))


class LibraryTests(unittest.TestCase):
    def test_every_exercise_renders_both_guides(self):
        folder = Path(tempfile.mkdtemp())
        for exercise in ex.EXERCISES.values():
            for which in ("start", "end"):
                guide = ex.render_guide(exercise, which, folder / f"{exercise.id}-{which}.png")
                with Image.open(guide) as image:
                    self.assertEqual(image.size, (512, 896))
                    self.assertIsNotNone(image.getbbox(), exercise.id)

    def test_prompts_describe_every_limb_and_follow_gender(self):
        exercise = ex.get("seated_knee_extension")
        prompt = ex.keyframe_prompt(exercise, "end", "living room with plants, wearing a blue top.")
        self.assertTrue(prompt.startswith(exercise.end_body))
        for limb in ("right arm:", "left arm:", "right leg:", "left leg:"):
            self.assertIn(limb, prompt)
        self.assertIn("right leg: thigh resting horizontally on the chair seat, knee straight, foot off the floor", prompt)
        self.assertIn("left leg: thigh resting horizontally on the chair seat, knee bent about 90 degrees, "
                      "foot flat on the floor", prompt)
        self.assertIn("living room with plants", prompt)
        self.assertTrue(ex.motion_prompt(exercise, "up", "feminino").startswith("She slowly straightens the right knee"))
        self.assertTrue(ex.motion_prompt(exercise, "down", "masculino").startswith("He slowly bends the right knee"))
        full = ex.motion_prompt(exercise, "rep", "feminino")
        self.assertIn(exercise.up, full)
        self.assertIn(exercise.down, full)
        self.assertIn("left foot stays flat on the floor", full)
        self.assertIn("Static camera", full)

    def test_unknown_exercise_lists_the_library(self):
        with self.assertRaisesRegex(ValueError, "sit_to_stand"):
            ex.get("prancha")


class BiomechanicsTests(unittest.TestCase):
    """O esqueleto vem dos ângulos de cada articulação (2026-10-06): membros com comprimento fixo, pés no chão."""

    def test_limbs_keep_their_length_through_the_whole_repetition(self):
        from influencer_mcp import biomecanica as bio
        for exercise in ex.EXERCISES.values():
            lengths = None
            for t in (0, 0.25, 0.5, 0.75, 1):
                p = bio.skeleton(bio.interpolate_pose(exercise.start_pose, exercise.end_pose, t))
                now = [round(sum((p[a][i] - p[b][i]) ** 2 for i in range(3)) ** 0.5, 4)
                       for a, b in (("shoulder_r", "elbow_r"), ("elbow_r", "wrist_r"), ("hip_r", "knee_r"),
                                    ("knee_r", "ankle_r"), ("hip_l", "knee_l"), ("knee_l", "ankle_l"))]
                lengths = lengths or now
                self.assertEqual(now, lengths, exercise.id)

    def test_supporting_feet_stay_on_the_floor_and_lifted_feet_leave_it(self):
        from influencer_mcp import biomecanica as bio
        march = ex.get("standing_march")
        for t in (0, 0.5, 1):
            p = bio.skeleton(bio.interpolate_pose(march.start_pose, march.end_pose, t))
            self.assertAlmostEqual(p["ankle_l"][1], bio.ANKLE_H, places=4)
            self.assertAlmostEqual(p["ankle_l"][2], 0.0, places=4)  # o pé de apoio não escorrega
        self.assertGreater(bio.skeleton(march.end_pose)["ankle_r"][1], bio.ANKLE_H + 0.15)
        squat = bio.skeleton(ex.get("mini_squat").end_pose)
        self.assertAlmostEqual(squat["ankle_r"][1], bio.ANKLE_H, places=4)
        self.assertLess(squat["hip_r"][2], -0.05)  # quadril para trás, pés no lugar

    def test_wall_push_up_keeps_the_hands_on_the_wall(self):
        from influencer_mcp import biomecanica as bio
        wall = ex.get("wall_push_up")
        start, end = bio.skeleton(wall.start_pose), bio.skeleton(wall.end_pose)
        gap = sum((start["wrist_r"][i] - end["wrist_r"][i]) ** 2 for i in range(3)) ** 0.5
        self.assertLess(gap, 0.02)
        self.assertGreater(wall.end_pose.arm_r.elbow, 60)

    def test_seated_heel_raise_lifts_the_knees_not_the_body(self):
        from influencer_mcp import biomecanica as bio
        calf = ex.get("seated_calf_raise")
        start, end = bio.skeleton(calf.start_pose), bio.skeleton(calf.end_pose)
        self.assertAlmostEqual(start["hip_r"][1], end["hip_r"][1], places=4)
        self.assertGreater(end["knee_r"][1], start["knee_r"][1] + 0.05)

    def test_three_quarter_view_shows_depth_and_the_face(self):
        curl = ex.get("biceps_curl")
        self.assertEqual(curl.view, "three_quarter")
        start, end = curl.points("start"), curl.points("end")
        self.assertIsNotNone(start[14])  # olho direito visível
        self.assertIsNotNone(start[15])  # olho esquerdo visível
        self.assertGreater(abs(end[4][0] - start[4][0]), 40)  # a mão vai para a frente e isso aparece no quadro
        self.assertIn("three-quarter view", curl.start_body)

    def test_skeleton_video_is_fitted_over_the_detected_person(self):
        model = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0)] + [None] * 14
        detected = [(5.0, 5.0), (25.0, 5.0), (5.0, 25.0), (25.0, 25.0)] + [None] * 14
        self.assertEqual(ex.fit_to(model, detected), (2.0, 5.0, 5.0))


class CheckTests(unittest.TestCase):
    def test_library_pose_passes_for_its_side_and_fails_for_the_other(self):
        for exercise in ex.EXERCISES.values():
            start, end = ex.expected_angles(exercise)
            if not ex.moving_joints(start, end):
                continue
            self.assertTrue(ex.check_pose(_angles_of(exercise, "start"), exercise, "start")["ok"], exercise.id)
            self.assertTrue(ex.check_pose(_angles_of(exercise, "end"), exercise, "end")["ok"], exercise.id)
            self.assertFalse(ex.check_pose(_angles_of(exercise, "start"), exercise, "end")["ok"], exercise.id)

    def test_sitting_person_fails_a_standing_exercise_even_if_the_arms_are_right(self):
        # Primeiro teste real (2026-10-06): desenvolvimento com os braços certos, mas sentada na cadeira, passava.
        seated = ex.angles(ex.get("sit_to_stand").points("start"))
        press = _angles_of(ex.get("overhead_press"), "start")
        mixed = {**press, "knee": seated["knee"], "hip": seated["hip"]}
        result = ex.check_pose(mixed, ex.get("overhead_press"), "start")
        self.assertFalse(result["ok"])
        self.assertIn("parado", result["reason"])

    def test_standing_exercises_forbid_sitting_in_the_image(self):
        self.assertIn("(sitting on a chair:1.6)", ex.get("overhead_press").avoid("start"))
        self.assertNotIn("(sitting:", ex.get("seated_march").avoid("start"))
        self.assertIn("not sitting", ex.get("single_leg_balance").end_body)
        self.assertIn("half squat", ex.get("mini_squat").end_body)

    def test_free_hand_exercises_block_objects_and_body_type_is_covered(self):
        self.assertIn("(holding a ball:1.4)", ex.get("mini_squat").avoid("end"))
        self.assertNotIn("holding a ball", ex.get("biceps_curl").avoid("end"))  # garrafa em cada mão pode
        for term in ("(crop top:1.5)", "(abs:1.5)", "(barefoot:1.5)"):
            self.assertIn(term, ex.get("overhead_press").avoid("start"))
        prompt = ex.keyframe_prompt(ex.get("mini_squat"), "end", "living room, wearing a lilac t-shirt")
        self.assertIn("(living room, wearing a lilac t-shirt:1.25)", prompt)

    def test_same_outfit_is_added_to_every_scene_with_the_character(self):
        scene = {"keyframePrompt": "full body, kitchen with fruit"}
        self.assertEqual(motion.with_outfit(scene, "a lilac t-shirt and gray leggings")["keyframePrompt"],
                         "full body, kitchen with fruit, (wearing a lilac t-shirt and gray leggings:1.3)")
        self.assertIs(motion.with_outfit({**scene, "showCharacter": False}, "a lilac t-shirt")["keyframePrompt"],
                      scene["keyframePrompt"])
        self.assertEqual(motion.with_outfit(scene, None), scene)

    def test_no_person_fails(self):
        self.assertFalse(ex.check_pose(None, ex.get("biceps_curl"), "start")["ok"])

    def test_rep_progress_needs_to_reach_the_end_position(self):
        exercise = ex.get("lateral_raise")
        start, end = ex.expected_angles(exercise)
        still = [start] * 5
        self.assertFalse(ex.rep_progress(still, start, end)["ok"])
        moving = [ex.angles(exercise.points_at(t)) for t in ex.rep_curve(9)]
        result = ex.rep_progress(moving, start, end)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["progress"], 0.95)

    def test_rep_curve_goes_to_the_end_and_back(self):
        curve = ex.rep_curve(81)
        self.assertEqual(curve[0], 0)
        self.assertEqual(curve[-1], 0)
        self.assertEqual(max(curve), 1)


class WorkflowTests(unittest.TestCase):
    def test_exercise_clip_uses_three_samplers_with_cfg_on_the_first(self):
        workflow = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=41, seed=1, prefix="x",
                                           profile="aquarela-mulher", end_image="b.png", exercise=True)
        self.assertEqual(workflow["12"]["class_type"], "WanFirstLastFrameToVideo")
        self.assertEqual(workflow["12"]["inputs"]["end_image"], ["24", 0])
        first, middle, last = workflow["13"]["inputs"], workflow["25"]["inputs"], workflow["14"]["inputs"]
        self.assertEqual((first["model"], first["cfg"], first["end_at_step"]), (["21", 0], 3.5, 2))
        self.assertEqual(workflow["21"]["inputs"]["model"], ["1", 0])  # sem o acelerador
        self.assertEqual((middle["start_at_step"], middle["end_at_step"], middle["cfg"]), (2, 3, 1.0))
        self.assertEqual((last["latent_image"], last["start_at_step"], last["steps"]), (["25", 0], 3, 6))
        self.assertIn("extra limbs", workflow["9"]["inputs"]["text"])

    def test_normal_clip_is_unchanged(self):
        workflow = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x",
                                           profile="aquarela-mulher")
        self.assertEqual(workflow["12"]["class_type"], "WanImageToVideo")
        self.assertNotIn("25", workflow)
        self.assertEqual(workflow["13"]["inputs"]["cfg"], 1.0)

    def test_skeleton_video_uses_fun_control_models(self):
        fun = ("fun-high.gguf", "fun-low.gguf", {"class_type": "UnetLoaderGGUF", "inputs": {}})
        workflow = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x",
                                           profile="aquarela-mulher", control_video="rep.mp4", fun_models=fun,
                                           exercise=True)
        self.assertEqual(workflow["12"]["class_type"], "Wan22FunControlToVideo")
        self.assertEqual(workflow["22"]["inputs"]["file"], "rep.mp4")
        self.assertEqual(workflow["1"]["inputs"]["unet_name"], "fun-high.gguf")
        self.assertEqual(workflow["2"]["inputs"]["unet_name"], "fun-low.gguf")
        with self.assertRaises(ValueError):
            motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x",
                                    control_video="rep.mp4")

    def test_end_keyframe_is_redrawn_from_the_start_keyframe(self):
        workflow = motion.keyframe_workflow(prompt="p", seed=1, pose_image="g.png", profile="aquarela-mulher",
                                            init_image="start.png", denoise=0.65, pose_strength=1.0)
        self.assertNotIn("7", workflow)
        self.assertEqual(workflow["8"]["inputs"]["latent_image"], ["31", 0])
        self.assertEqual(workflow["8"]["inputs"]["denoise"], 0.65)
        self.assertEqual(workflow["6"]["inputs"]["strength"], 1.0)

    def test_fun_control_is_used_only_where_the_model_exists(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "models" / "unet").mkdir(parents=True)
        with patch("influencer_mcp.comfyui.comfyui_dir", return_value=folder), patch.dict("os.environ", {"INFLUENCER_GPU": "local"}):
            self.assertIsNone(motion.fun_control_models())
            for name in ("Wan2.2-Fun-A14B-Control_HighNoise-Q4_K_M.gguf", "Wan2.2-Fun-A14B-Control_LowNoise-Q4_K_M.gguf"):
                (folder / "models" / "unet" / name).write_bytes(b"x")
            self.assertEqual(motion.fun_control_models()[2]["class_type"], "UnetLoaderGGUF")
        remote = {"fun_high": None, "fun_low": None}
        with patch("influencer_mcp.gpu_remota.settings", return_value=remote), patch.dict("os.environ", {"INFLUENCER_GPU": "remota"}):
            self.assertIsNone(motion.fun_control_models())
            remote.update(fun_high="h.safetensors", fun_low="l.safetensors")
            self.assertEqual(motion.fun_control_models()[:2], ("h.safetensors", "l.safetensors"))


class ScriptRulesTests(unittest.TestCase):
    def _scene(self, **extra):
        return {"narration": "Repita oito vezes, devagar.", "caption": "c", "keyframePrompt": "sala",
                "motions": ["m"], **extra}

    def test_series_scene_must_name_a_library_exercise(self):
        with self.assertRaisesRegex(ValueError, "exercise"):
            motion.check_exercise_scenes([self._scene()])
        motion.check_exercise_scenes([self._scene(exercise="seated_march")])
        motion.check_exercise_scenes([self._scene(showCharacter=False)])

    def test_unknown_exercise_is_refused(self):
        script = {"slug": "x", "scenes": [self._scene(exercise="burpee")] * 5}
        with self.assertRaisesRegex(ValueError, "biblioteca"):
            motion.validate_motion_script(script)


class AnimateExerciseTests(unittest.TestCase):
    def test_frames_mode_animates_up_and_down_once_and_loops_to_cover_the_narration(self):
        folder = Path(tempfile.mkdtemp())
        keyframe = folder / "keyframe.png"
        Image.new("RGB", (8, 8)).save(keyframe)
        Image.new("RGB", (8, 8)).save(motion.exercise_end_path(keyframe))
        calls = []

        def fake_run(base, workflow, timeout):
            calls.append(workflow)
            prefix = workflow["20"]["inputs"]["filename_prefix"]
            return [{"filename": f"{prefix}_{i:05d}.png"} for i in range(81)]

        def fake_download(base, items, target):
            target.mkdir(parents=True, exist_ok=True)
            names = [f"hd-{i:04d}.png" for i in range(1, len(items) + 1)]
            for name in names:
                (target / name).write_bytes(b"x")
            return names

        class NoDetector:
            available = False

            def angles(self, path):
                return None

            def close(self):
                pass

        with patch.object(motion, "fun_control_models", return_value=None), \
                patch.object(motion, "_comfyui_url", return_value="http://x"), \
                patch.object(motion, "_upload_comfy_image", side_effect=lambda base, path, *a: path.name), \
                patch.object(motion, "_run_workflow", side_effect=fake_run), \
                patch.object(motion, "_download_frames", side_effect=fake_download), \
                patch.object(ex, "Detector", NoDetector):
            frames = motion.animate_exercise_scene({"exercise": "biceps_curl", "narration": "x"}, keyframe, 10.0,
                                                   folder, seed=1, profile="aquarela-mulher")
            again = motion.animate_exercise_scene({"exercise": "biceps_curl", "narration": "x"}, keyframe, 10.0,
                                                  folder, seed=1, profile="aquarela-mulher")
        self.assertEqual(len(calls), 2)  # subida e descida; a segunda chamada reaproveita
        self.assertEqual([c["12"]["class_type"] for c in calls], ["WanFirstLastFrameToVideo"] * 2)
        rep = 81 + 80  # 161 quadros HD = ~5 s a 32 fps; 10 s de fala = 2 repetições
        self.assertEqual(len(frames), rep + (rep - 1))
        self.assertEqual(frames, again)


if __name__ == "__main__":
    unittest.main()
