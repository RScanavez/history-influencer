import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import motion_jobs as mj


class ReviveTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        item = patch.object(mj, "JOBS_DIR", self.dir)
        item.start()
        self.addCleanup(item.stop)

    def write(self, job_id, **fields):
        job = {"id": job_id, "kind": "keyframes", "params": {}, "state": "queued", "pid": 111, "progress": [],
               "error": None, "queuedAtNs": 1, **fields}
        (self.dir / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")

    def read(self, job_id):
        return json.loads((self.dir / f"{job_id}.json").read_text(encoding="utf-8"))

    def test_dead_queued_job_is_relaunched_and_keeps_queue_position(self):
        self.write("a")
        with patch.object(mj, "_pid_alive", return_value=False), patch.object(mj, "_launch", return_value=222) as launch:
            self.assertEqual(mj.revive_dead_jobs(), ["a"])
        launch.assert_called_once()
        job = self.read("a")
        self.assertEqual((job["state"], job["pid"], job["restarts"], job["queuedAtNs"]), ("queued", 222, 1, 1))

    def test_job_marked_failed_because_process_died_is_relaunched(self):
        self.write("b", state="failed", error=mj.DIED + "; inicie-o de novo")
        with patch.object(mj, "_pid_alive", return_value=False), patch.object(mj, "_launch", return_value=5):
            self.assertEqual(mj.revive_dead_jobs(), ["b"])

    def test_real_errors_alive_jobs_and_retry_limit_are_left_alone(self):
        self.write("c", state="failed", error="ComfyUI falhou no workflow", finishedAt="x")
        self.write("d")  # vivo
        self.write("e", restarts=mj.MAX_RESTARTS)
        with patch.object(mj, "_pid_alive", side_effect=lambda pid: True), patch.object(mj, "_launch") as launch:
            mj.revive_dead_jobs()
        with patch.object(mj, "_pid_alive", return_value=False), patch.object(mj, "_launch", return_value=7) as launch2:
            revived = mj.revive_dead_jobs()
        launch.assert_not_called()
        self.assertEqual(revived, ["d"])  # "c" tem erro real, "e" já usou as tentativas
        launch2.assert_called_once()

    def test_reused_pid_counts_as_dead(self):
        from datetime import UTC, datetime, timedelta
        recorded = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
        with patch.object(mj, "_pid_alive", return_value=True),                 patch.object(mj, "_process_started_at", return_value=recorded + timedelta(hours=3)):
            self.assertFalse(mj.pid_alive_since(123, recorded.isoformat()))
        with patch.object(mj, "_pid_alive", return_value=True),                 patch.object(mj, "_process_started_at", return_value=recorded - timedelta(seconds=5)):
            self.assertTrue(mj.pid_alive_since(123, recorded.isoformat()))
        with patch.object(mj, "_pid_alive", return_value=True):
            self.assertTrue(mj.pid_alive_since(123, None))  # registros antigos, sem horário

    def test_process_start_time_of_current_process_is_in_the_past(self):
        import os
        from datetime import UTC, datetime
        started = mj._process_started_at(os.getpid())
        if started is not None:
            self.assertLess(started, datetime.now(UTC))


if __name__ == "__main__":
    unittest.main()
