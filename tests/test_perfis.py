import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import motion, perfis


class PerfisTests(unittest.TestCase):
    def test_default_character_comes_from_personagens_md(self):
        self.assertEqual(perfis.active_name("- padrao: ilustracao-homem"), "ilustracao-homem")
        self.assertEqual(perfis.active_name(""), perfis.DEFAULT)
        with self.assertRaises(ValueError):
            perfis.active_name("- padrao: inexistente")

    def test_scripts_without_profile_are_legacy_and_removed_photo_maps_to_woman(self):
        self.assertEqual(perfis.of_script({}), perfis.LEGACY)
        self.assertEqual(perfis.of_script({"visualProfile": "foto-mulher"}), "aquarela-mulher")
        self.assertNotIn("foto-mulher", perfis.PROFILES)

    def test_characters_share_global_style_and_add_their_own_details(self):
        woman, man = perfis.get("aquarela-mulher"), perfis.get(perfis.LEGACY)
        self.assertEqual(woman["style"], man["style"])
        self.assertEqual(woman["lora"], man["lora"])
        self.assertIn("(beard:1.4)", woman["negative"])
        self.assertNotIn("(beard:1.4)", man["negative"])
        self.assertIn("(young man:1.3)", man["negative"])
        self.assertEqual([item["label"] for item in perfis.selectable("aquarela")],
                         ["Automático (2 mulheres : 1 homem)", "Mulher 50+", "Homem 50+"])

    def test_visual_identities_give_style_to_their_characters(self):
        self.assertEqual([item["id"] for item in perfis.identities()][0], "aquarela")
        self.assertIn("3d", [item["id"] for item in perfis.identities()])
        woman, woman3d = perfis.get("aquarela-mulher"), perfis.get("3d-mulher")
        self.assertEqual((woman["identity"], woman3d["identity"]), ("aquarela", "3d"))
        self.assertIn("Pixar", woman3d["style"])
        self.assertNotIn("Pixar", woman["style"])
        self.assertNotEqual(woman["lora"]["file"], woman3d["lora"]["file"])
        self.assertEqual({item["identity"] for item in perfis.selectable("3d")}, {"3d"})
        self.assertEqual(set(perfis.balanced_plan(3, recent=[], identity="3d")), {"3d-mulher", "3d-homem"})

    def test_3d_characters_get_a_new_outfit_per_video_and_the_man_needs_no_beard(self):
        man = perfis.get("3d-homem")
        self.assertNotIn("beard", man["character"])
        self.assertNotIn("wearing", man["character"])
        for name in ("3d-mulher", "3d-homem"):
            options = perfis.get(name)["outfits"]
            self.assertGreaterEqual(len(options), 5)
            # Sem histórico, a roupa sugerida pelo roteiro vale; repetida, troca por uma que não apareceu.
            self.assertEqual(perfis.outfit_for_new_video(name, "a red t-shirt.", recent=[]), "a red t-shirt")
            fresh = perfis.outfit_for_new_video(name, options[0], recent=[options[0], options[1]])
            self.assertIn(fresh, options[2:])
            # Todas usadas: a usada há mais tempo (última da lista, que vai do mais novo ao mais velho).
            self.assertEqual(perfis.outfit_for_new_video(name, None, recent=list(options)), options[-1])
        # Personagem sem lista: só a roupa do roteiro, como antes.
        self.assertEqual(perfis.outfit_for_new_video("aquarela-mulher", "a lilac top", recent=["a lilac top"]),
                         "a lilac top")
        self.assertIsNone(perfis.outfit_for_new_video("aquarela-mulher", None, recent=[]))

    def test_character_without_its_face_image_is_refused_and_hidden(self):
        import tempfile
        from unittest.mock import patch as _patch
        with _patch.object(perfis, "REFERENCE_DIR", Path(tempfile.mkdtemp())):
            with self.assertRaisesRegex(FileNotFoundError, "rosto fixo"):
                perfis.reference_image("3d-mulher")
            self.assertNotIn("3d-mulher", [item["id"] for item in perfis.selectable()])
            self.assertNotIn("3d", [item["id"] for item in perfis.identities()])
            # Sem referência declarada (personagem antigo), nada muda.
            self.assertIsNone(perfis.reference_image(perfis.LEGACY))
        self.assertTrue(perfis.reference_image("3d-mulher").is_file())

    def test_automatic_character_keeps_about_one_man_every_three_videos(self):
        woman, man = "aquarela-mulher", perfis.LEGACY
        self.assertEqual(perfis.balanced_plan(3, recent=[]), [woman, man, woman])
        self.assertEqual(perfis.balanced_plan(1, recent=[woman] * 5), [man])
        self.assertEqual(perfis.balanced_plan(2, recent=[man, woman]), [woman, woman])

    def test_build_reads_new_character_from_markdown(self):
        style = """## Estilo global

- estilo: pencil
- negativo: text, (dumbbells:1.5)
- lora: x.safetensors
"""
        characters = """## Configuração

- padrao: novo

## Novo

- id: novo
- nome: Novo 40+
- genero: masculino
- selecionavel: sim
- personagem: a man
- negativo: (beard:1.4)
- voz-pt: pm_alex
- olhar: a | b
"""
        profiles, default = perfis.build(style, characters)
        self.assertEqual(default, "novo")
        self.assertEqual(profiles["novo"]["negative"], "text, (dumbbells:1.5), (beard:1.4)")
        self.assertEqual(profiles["novo"]["voices"], {"p": "pm_alex"})
        self.assertEqual(profiles["novo"]["gaze"], ("a", "b"))
        self.assertEqual(profiles["novo"]["lora"]["file"], "x.safetensors")

    def test_voices_per_profile(self):
        self.assertEqual([perfis.voice("aquarela-mulher", lang) for lang in "pfa"], ["ff_siwis", "ff_siwis", "af_heart"])
        with patch.dict(os.environ, {"KOKORO_VOICE": "pm_santa"}):
            self.assertEqual(perfis.voice(perfis.LEGACY, "f"), "pm_santa")

    def test_allow_objects_works_in_all_profiles(self):
        for name in perfis.PROFILES:
            self.assertIn("(dumbbells:1.5)", motion.keyframe_negative(profile=name))
            self.assertNotIn("dumbbells", motion.keyframe_negative(["dumbbells"], profile=name))

    def test_video_workflow_follows_profile(self):
        video = motion.wan_i2v_workflow(start_image="a.png", prompt="walks", frames=81, seed=1, prefix="x",
                                        profile="aquarela-mulher")
        self.assertIn("50-year-old woman", video["8"]["inputs"]["text"])


class HashtagTests(unittest.TestCase):
    def test_five_hashtags_allowed_and_normalized(self):
        from influencer_mcp.media import clean_hashtags

        tags = [f"tag{n}" for n in range(5)]
        self.assertEqual(clean_hashtags(tags)[0], "#tag0")
        self.assertEqual(len(clean_hashtags(["#" + t for t in tags])), 5)
        for bad in (tags + ["extra"], ["duas palavras"], ["a", "#A"], [""]):
            with self.assertRaises(ValueError):
                clean_hashtags(bad)


class FormatLimitsTests(unittest.TestCase):
    def test_new_videos_follow_format_duration_and_scene_count(self):
        self.assertEqual(motion.FORMAT_TARGETS["short"], (25, 35))
        self.assertEqual(motion.FORMAT_TARGETS["video"], (45, 60))
        motion.check_scene_count([{}] * 4, "short")
        motion.check_scene_count([{}] * 5, "video")
        with self.assertRaises(ValueError):
            motion.check_scene_count([{}] * 9, "short")
        with self.assertRaises(ValueError):
            motion.check_scene_count([{}] * 10, "video")


class FormatLimitsDocsTests(unittest.TestCase):
    """Duração, cenas e palavras de cada formato têm uma fonte só (motion.FORMAT_TARGETS/FORMAT_SCENES): prompts e
    documentos mandam chamar describe_format_limits em vez de repetir os números, que já divergiram do código."""

    PATTERN = re.compile(
        r"\d+ a \d+ (?:cenas|segundos|palavras)|\d+ a \d+ ?s|entre \d+ e \d+ ?(?:s|segundos)", re.IGNORECASE)
    # 2 a 5 palavras da capa e outros usos que não são duração do vídeo
    ALLOWED = ("thumbnailText", "thumbnail_text")

    def test_limits_derive_from_the_two_constants(self):
        limits = motion.format_limits()
        for fmt in ("short", "video"):
            self.assertEqual(limits[fmt]["segundos"], list(motion.FORMAT_TARGETS[fmt]))
            self.assertEqual(limits[fmt]["cenas"], list(motion.FORMAT_SCENES[fmt]))
            for low, high in limits[fmt]["palavras_total"].values():
                self.assertLess(low, high)

    def test_prompts_and_docs_do_not_repeat_the_numbers(self):
        root = Path(__file__).resolve().parents[1]
        files = [*(root / ".claude").rglob("*.md"), root / "README.md", root / "seo.md"]
        found = []
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.PATTERN.search(line) and not any(word in line for word in self.ALLOWED):
                    found.append(f"{path.name}:{number}")
        self.assertEqual(found, [], "use describe_format_limits em vez de escrever duração/cenas/palavras")


class DurationTests(unittest.TestCase):
    def test_new_videos_must_be_between_60_and_90_seconds(self):
        script = {"targetSeconds": [60, 90]}
        motion.check_duration(script, 75)
        with self.assertRaisesRegex(ValueError, "acrescente cerca de"):
            motion.check_duration(script, 47)
        with self.assertRaisesRegex(ValueError, "retire cerca de"):
            motion.check_duration(script, 96)

    def test_old_scripts_without_target_are_not_checked(self):
        motion.check_duration({}, 120)


class ReferenceTests(unittest.TestCase):
    def test_reference_adds_face_adapter_after_lora(self):
        workflow = motion.keyframe_workflow(prompt="squat", seed=1, pose_image=None, profile="aquarela-mulher",
                                            reference_image="ref.png")
        self.assertEqual(workflow["23"]["inputs"]["image"], "ref.png")
        self.assertEqual(workflow["24"]["inputs"]["model"], ["11", 0])
        self.assertEqual(workflow["8"]["inputs"]["model"], ["24", 0])
        self.assertIn("face", workflow["21"]["inputs"]["ipadapter_file"])

    def test_no_reference_for_legacy_or_scenes_without_character(self):
        legacy = motion.keyframe_workflow(prompt="squat", seed=1, pose_image=None, reference_image="ref.png")
        empty = motion.keyframe_workflow(prompt="park", seed=1, pose_image=None, profile="foto-mulher",
                                         reference_image="ref.png", with_character=False)
        self.assertNotIn("24", legacy)
        self.assertNotIn("24", empty)


class WomanVoiceTests(unittest.TestCase):
    def test_woman_profiles_never_use_santa_or_male_voices(self):
        with patch.dict(os.environ, {"KOKORO_VOICE": "pm_santa"}):
            for name, settings in perfis.PROFILES.items():
                if "woman" not in settings["character"]:
                    continue
                for lang in "pfa":
                    voice = perfis.voice(name, lang)
                    self.assertNotIn("santa", voice, (name, lang))
                    self.assertFalse(voice.split("_")[0].endswith("m"), (name, lang, voice))


class AquarelaMulherTests(unittest.TestCase):
    def test_face_only_reference_no_gaze_and_clothes_from_prompt(self):
        workflow = motion.keyframe_workflow(prompt="wearing a light blue workout top, walking in a park", seed=1,
                                            pose_image=None, profile="aquarela-mulher", reference_image="ref.png",
                                            gaze=perfis.gaze("aquarela-mulher", 1))
        positive, negative = workflow["2"]["inputs"]["text"], workflow["3"]["inputs"]["text"]
        self.assertEqual(workflow["8"]["inputs"]["model"], ["24", 0])
        self.assertNotIn("25", workflow)
        self.assertEqual(workflow["24"]["inputs"]["model"], ["11", 0])  # rosto aplicado depois do LoRA
        self.assertNotIn("camera", positive)
        self.assertNotIn("teal", positive)
        self.assertIn("light blue workout top", positive)
        for term in ("long pants", "jacket", "(long sleeves"):
            self.assertNotIn(term, negative)
        self.assertIn("(cleavage:1.4)", negative)


class FullBodyTests(unittest.TestCase):
    def test_pose_only_when_script_asks(self):
        self.assertFalse(perfis.get("aquarela-mulher").get("always_pose"))

    def test_default_pose_from_action(self):
        self.assertEqual(perfis.default_pose("Sitting on a shaded park bench, holding a bottle"), "seated_posture")
        self.assertEqual(perfis.default_pose("Walking along a sunny park path"), "walk")
        self.assertEqual(perfis.default_pose("Standing in a kitchen holding a glass"), "standing")
        self.assertEqual(perfis.default_pose("Standing by the door, a pair of walking shoes on the floor"), "standing")

    def test_scene_first_and_face_only_late(self):
        workflow = motion.keyframe_workflow(prompt="Walking in a park holding a water bottle", seed=1, pose_image="p.png",
                                            profile="aquarela-mulher", reference_image="ref.png")
        positive = workflow["2"]["inputs"]["text"]
        self.assertLess(positive.index("water bottle"), positive.index("50-year-old woman"))
        self.assertNotIn("head to feet", positive)  # enquadramento vem do prompt da cena
        self.assertIn("natural pose", positive)
        self.assertEqual(workflow["24"]["inputs"]["start_at"], 0.25)
        self.assertIn("watercolor tint, ", positive)
        self.assertIn(":1.3)", positive)  # estilo com peso: a cena (1.2) não engole o desenho
        self.assertIn("6", workflow)  # ControlNet OpenPose aplicado


class SubjectSceneTests(unittest.TestCase):
    def test_subject_only_scenes_are_optional_with_a_ceiling(self):
        scenes = [{} for _ in range(7)]
        motion.check_subject_scenes(scenes)  # nenhuma: vale (desde 2026-10-13)
        scenes[2]["showCharacter"] = scenes[4]["showCharacter"] = False
        motion.check_subject_scenes(scenes)
        for scene in scenes[:5]:
            scene["showCharacter"] = False
        with self.assertRaisesRegex(ValueError, "no máximo 3"):
            motion.check_subject_scenes(scenes)

    def test_subject_scene_has_no_character_reference_or_people(self):
        workflow = motion.keyframe_workflow(prompt="close-up of folded cotton socks on a bench", seed=1, pose_image=None,
                                            profile="aquarela-mulher", reference_image="ref.png", with_character=False)
        self.assertNotIn("50-year-old", workflow["2"]["inputs"]["text"])
        self.assertNotIn("24", workflow)
        self.assertIn("(person:1.6)", workflow["3"]["inputs"]["text"])
        video = motion.wan_i2v_workflow(start_image="a.png", prompt="slow camera push-in", frames=81, seed=1, prefix="x",
                                        profile="aquarela-mulher", with_character=False)
        self.assertNotIn("50-year-old", video["8"]["inputs"]["text"])


class SeriesPoseTests(unittest.TestCase):
    def test_pose_only_when_narration_has_repetitions_or_series(self):
        self.assertTrue(motion.has_series("Sento e levanto da cadeira, devagar, oito a dez vezes."))
        self.assertTrue(motion.has_series("Faça três séries de doze repetições."))
        self.assertFalse(motion.has_series("Beba água antes de sair para caminhar."))
        # Frequência não é série (fala final recusada por engano em 2026-10-04).
        self.assertFalse(motion.has_series("Repita a sequência três vezes por semana."))
        self.assertFalse(motion.has_series("Faça duas vezes ao dia."))
        self.assertTrue(motion.has_series("Repita oito vezes de cada lado."))
        self.assertTrue(motion.has_series("Faço de oito a dez repetições de cada lado."))
        self.assertTrue(motion.has_series("Repito 12 vezes."))
        # Conselho sem contagem não é série (parava o lote cardio-sentado-cadeira em 2026-10-05).
        self.assertFalse(motion.has_series("Aumente as repetições pouco a pouco."))
        self.assertFalse(motion.has_series("Às vezes o joelho reclama."))
        self.assertFalse(motion.has_series("Às vezes o calor engana."))


if __name__ == "__main__":
    unittest.main()
