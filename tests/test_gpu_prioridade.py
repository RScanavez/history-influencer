import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import motion_jobs as mj


class GpuPriorityTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for item in (patch.object(mj, "JOBS_DIR", self.dir), patch.object(mj, "LOCK_PATH", self.dir / "gpu.lock"),
                     patch.object(mj, "_job_alive", return_value=True)):
            item.start()
            self.addCleanup(item.stop)

    def write(self, job_id, kind, state="queued", queued_at=1, **fields):
        job = {"id": job_id, "kind": kind, "params": {}, "state": state, "pid": 111, "progress": [],
               "queuedAtNs": queued_at, **fields}
        (self.dir / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")
        return job

    def test_keyframes_jump_ahead_of_older_animations(self):
        self.write("anim", "animation", queued_at=1)
        self.write("kf", "keyframes", queued_at=5)
        self.assertTrue(mj._oldest_queued("kf"))
        self.assertFalse(mj._oldest_queued("anim"))

    def test_fifo_within_same_kind(self):
        self.write("kf1", "keyframes", queued_at=1)
        self.write("kf2", "keyframes", queued_at=2)
        self.assertTrue(mj._oldest_queued("kf1"))

    def test_animation_pauses_releases_gpu_and_resumes(self):
        job = self.write("anim", "animation", state="running")
        (self.dir / "gpu.lock").write_text(str(mj.os.getpid()))
        messages = []
        waiting = iter([True, True, False])  # há imagens-chave; ainda há; acabaram
        with patch.object(mj, "_must_yield", side_effect=lambda job: next(waiting)), patch.object(mj.time, "sleep"):
            mj._pause_for_keyframes(self.dir / "anim.json", job, messages.append)
        self.assertEqual(len(messages), 2)
        self.assertIn("pausado", messages[0])
        self.assertFalse(job["pausedForKeyframes"])
        self.assertEqual((self.dir / "gpu.lock").read_text(), str(mj.os.getpid()))

    def test_any_keyframes_outrank_even_urgent_animation(self):
        self.write("kf", "keyframes", queued_at=9)
        self.write("anim", "animation", state="running", queued_at=2)
        self.write("urgent", "animation", queued_at=1, params={"urgent": True})
        self.assertTrue(mj._oldest_queued("kf"))
        self.assertFalse(mj._oldest_queued("urgent"))
        anim = json.loads((self.dir / "anim.json").read_text(encoding="utf-8"))
        kf = json.loads((self.dir / "kf.json").read_text(encoding="utf-8"))
        urgent = json.loads((self.dir / "urgent.json").read_text(encoding="utf-8"))
        self.assertTrue(mj._must_yield(anim))
        self.assertTrue(mj._must_yield(urgent))
        self.assertFalse(mj._must_yield(kf))

    def test_urgent_keyframes_go_first_among_keyframes(self):
        self.write("kf", "keyframes", queued_at=1)
        self.write("urgent", "keyframes", queued_at=9, params={"urgent": True})
        self.assertTrue(mj._oldest_queued("urgent"))
        self.assertFalse(mj._oldest_queued("kf"))

    def test_no_pause_when_no_keyframes_waiting(self):
        job = self.write("anim", "animation", state="running")
        messages = []
        mj._pause_for_keyframes(self.dir / "anim.json", job, messages.append)
        self.assertEqual(messages, [])

    def test_paused_animation_resumes_before_new_animations(self):
        self.write("paused", "animation", state="running", pausedForKeyframes=True)
        self.write("new", "animation", queued_at=9)
        self.assertTrue(mj._paused_animation("new"))
        self.assertFalse(mj._paused_animation("paused"))

    def test_paused_queued_job_lets_others_go_first(self):
        self.write("kf1", "keyframes", queued_at=1)
        self.write("kf2", "keyframes", queued_at=2)
        mj.pause_path("kf1").write_text("x")
        self.assertTrue(mj._oldest_queued("kf2"))
        mj.pause_path("kf1").unlink()
        self.assertTrue(mj._oldest_queued("kf1"))

    def test_running_job_releases_gpu_while_paused_and_resumes(self):
        job = self.write("anim", "animation", state="running")
        (self.dir / "gpu.lock").write_text(str(mj.os.getpid()))
        mj.pause_path("anim").write_text("x")
        messages = []

        def sleep(_):
            self.assertFalse((self.dir / "gpu.lock").exists())  # GPU livre durante a pausa
            self.assertEqual(mj._read(self.dir / "anim.json")["state"], "queued")  # volta para a fila
            mj.pause_path("anim").unlink(missing_ok=True)

        with patch.object(mj.time, "sleep", side_effect=sleep), patch.object(mj, "keyframes_waiting", return_value=False),                 patch.object(mj, "_acquire_gpu", side_effect=lambda *_: mj._try_lock()):
            mj._checkpoint(self.dir / "anim.json", job, lambda m: (messages.append(m), mj._write(self.dir / "anim.json", job)))
        self.assertIn("pausado", messages[0])
        self.assertIn("retomado", messages[-1])
        self.assertEqual(job["state"], "running")
        self.assertEqual((self.dir / "gpu.lock").read_text(), str(mj.os.getpid()))

    def test_user_paused_job_does_not_block_the_queue(self):
        self.write("paused", "animation", state="running", pausedForKeyframes=True)
        mj.pause_path("paused").write_text("x")
        self.write("old", "animation", queued_at=1)
        self.assertFalse(mj._paused_animation("old"))


    def test_non_job_json_in_jobs_folder_is_ignored(self):
        self.write("kf1", "keyframes", queued_at=1)
        (self.dir / "gpu-remota.json").write_text('{"lastActivity": "x", "tunnelPid": 1}')
        self.assertTrue(mj._oldest_queued("kf1"))  # antes: KeyError 'state' derrubava a fila inteira
        self.assertEqual([job["id"] for job in mj._live_jobs()], ["kf1"])


if __name__ == "__main__":
    unittest.main()
