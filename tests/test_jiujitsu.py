import unittest

from influencer_mcp import motion


class JiuJitsuTests(unittest.TestCase):
    def test_detecta_tema(self):
        self.assertTrue(motion.is_jiujitsu({"title": "Jiu-Jitsu depois dos 60", "scenes": []}))
        self.assertTrue(motion.is_jiujitsu({"scenes": [{"narration": "treino de BJJ"}]}))
        self.assertFalse(motion.is_jiujitsu({"title": "Agachamento", "scenes": []}))

    def test_kimono_com_faixa_branca_e_negativo(self):
        scene = motion.with_outfit({"keyframePrompt": "man on a mat"}, motion.JIUJITSU_OUTFIT)
        self.assertIn("white belt", scene["keyframePrompt"])
        workflow = motion.keyframe_workflow(prompt=scene["keyframePrompt"], seed=1, pose_image=None)
        self.assertIn("black belt", workflow["3"]["inputs"]["text"])
        self.assertIn("shirtless", workflow["3"]["inputs"]["text"])


if __name__ == "__main__":
    unittest.main()
