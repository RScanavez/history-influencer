import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import gpu_remota as gr
from influencer_mcp import motion_jobs as mj


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        for item in (patch.object(mj, "GENERATED_ROOT", self.root), patch.object(mj, "JOBS_DIR", self.jobs),
                     patch.object(mj, "LOCK_PATH", self.jobs / "gpu.lock"), patch.object(mj, "_job_alive", return_value=True)):
            item.start()
            self.addCleanup(item.stop)

    def test_old_videos_stay_local_and_new_batches_follow_choice(self):
        self.assertEqual(mj.resolve_target({"batch_id": "2026-10-03-0216", "video_slug": "antigo"}), "local")
        (self.root / "lotes").mkdir()
        (self.root / "lotes" / "l.json").write_text(json.dumps({"batchId": "2026-10-04-0900", "gpu": "remota"}))
        self.assertEqual(mj.resolve_target({"batch_id": "2026-10-04-0900", "script": {"slug": "novo"}}), "remota")
        folder = self.root / "historiador" / "2026-10-04-0900" / "novo"
        folder.mkdir(parents=True)
        (folder / "motion-script.json").write_text(json.dumps({"gpu": "local"}))
        self.assertEqual(mj.resolve_target({"batch_id": "2026-10-04-0900", "video_slug": "novo"}), "local")
        self.assertEqual(mj.resolve_target({"gpu": "remota"}), "remota")

    def test_each_gpu_has_its_own_queue_and_lock(self):
        def write(job_id, gpu, queued_at):
            job = {"id": job_id, "kind": "animation", "state": "queued", "pid": 1, "queuedAtNs": queued_at,
                   "params": {"gpu": gpu}}
            (self.jobs / f"{job_id}.json").write_text(json.dumps(job))

        write("loc", "local", 5)
        write("rem", "remota", 9)
        with patch.object(mj, "_TARGET", "remota"):
            self.assertTrue(mj._oldest_queued("rem"))  # a fila remota não espera a local
            self.assertEqual(mj._lock_path().name, "gpu-remota.lock")
        with patch.object(mj, "_TARGET", "local"):
            self.assertTrue(mj._oldest_queued("loc"))
            self.assertEqual(mj._lock_path().name, "gpu.lock")


class RemoteGpuTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "secrets").mkdir()
        for item in (patch.object(gr, "CONFIG_PATH", self.root / "gpu_remota.md"),
                     patch.object(gr, "API_KEY_PATH", self.root / "secrets" / "runpod.txt"),
                     patch.object(gr, "SSH_KEY_PATH", self.root / "secrets" / "runpod_ssh"),
                     patch.object(gr, "STATE_PATH", self.root / "state.json")):
            item.start()
            self.addCleanup(item.stop)

    def configure(self):
        (self.root / "gpu_remota.md").write_text("- pod-id: abc123\n- porta-local: 8190\n- desligar-apos-min: 10\n")
        (self.root / "secrets" / "runpod.txt").write_text("k")
        (self.root / "secrets" / "runpod_ssh").write_text("k")

    def test_not_configured_until_pod_and_keys_exist(self):
        (self.root / "gpu_remota.md").write_text("- pod-id:\n")
        self.assertFalse(gr.configured())
        self.configure()
        self.assertTrue(gr.configured())
        self.assertEqual(gr.base_url(), "http://127.0.0.1:8190")

    def test_never_stops_on_its_own_unless_enabled(self):
        self.configure()
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        gr._save_state(lastActivity=(now - timedelta(hours=5)).isoformat())
        with patch.object(gr, "pod", return_value={"desiredStatus": "RUNNING"}), patch.object(gr, "stop") as stop:
            self.assertFalse(gr.stop_if_idle(False, now))
        stop.assert_not_called()

    def test_stops_only_after_idle_minutes_when_enabled(self):
        self.configure()
        (self.root / "gpu_remota.md").write_text(
            "- pod-id: abc123\n- desligar-automatico: sim\n- desligar-apos-min: 10\n")
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        gr._save_state(lastActivity=(now - timedelta(minutes=5)).isoformat())
        with patch.object(gr, "pod", return_value={"desiredStatus": "RUNNING"}), patch.object(gr, "stop") as stop, \
                patch.object(gr, "close_tunnel"):
            self.assertFalse(gr.stop_if_idle(False, now))
            self.assertFalse(gr.stop_if_idle(True, now + timedelta(minutes=30)))  # job remoto ativo renova
            self.assertTrue(gr.stop_if_idle(False, now + timedelta(minutes=41)))
        stop.assert_called_once_with("abc123")

    def test_ssh_endpoint_from_runtime_ports(self):
        info = {"runtime": {"ports": [{"privatePort": 8188, "isIpPublic": False, "ip": "x", "publicPort": 1},
                                      {"privatePort": 22, "isIpPublic": True, "ip": "1.2.3.4", "publicPort": 40022}]}}
        self.assertEqual(gr.ssh_endpoint(info), ("1.2.3.4", 40022))
        self.assertIsNone(gr.ssh_endpoint({"runtime": None}))


class RemoteWanTests(unittest.TestCase):
    def test_remote_uses_full_fp8_wan_and_local_keeps_gguf(self):
        import os
        from influencer_mcp import motion
        with patch.dict(os.environ, {"INFLUENCER_GPU": "local"}):
            local = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x")
        with patch.dict(os.environ, {"INFLUENCER_GPU": "remota"}):
            remote = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x")
        self.assertEqual(local["1"]["class_type"], "UnetLoaderGGUF")
        self.assertEqual(remote["1"]["class_type"], "UNETLoader")
        self.assertIn("fp8", remote["1"]["inputs"]["unet_name"])
        self.assertIn("low_noise", remote["2"]["inputs"]["unet_name"])


class ResumeRetryTests(unittest.TestCase):
    def test_waits_for_free_gpu_instead_of_failing(self):
        calls, messages = [], []

        def resume(pod_id):
            calls.append(pod_id)
            if len(calls) < 3:
                raise RuntimeError("RunPod: There are not enough free GPUs on the host machine to start this pod.")

        no_stock = RuntimeError("RunPod: There are no longer any instances available with the requested specifications.")
        with patch.object(gr, "resume", side_effect=resume), patch.object(gr, "migrate", side_effect=no_stock), \
                patch.object(gr, "settings", return_value={"pod_id": "abc"}), patch.object(gr.time, "sleep"):
            self.assertEqual(gr._resume_when_available("abc", messages.append), "abc")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(messages), 1)
        self.assertIn("nenhuma H100 livre", messages[0])

    def test_full_server_moves_to_a_new_machine_on_the_same_disk(self):
        full = RuntimeError("RunPod: There are not enough free GPUs on the host machine to start this pod.")
        with patch.object(gr, "resume", side_effect=full), patch.object(gr, "migrate", return_value="novo") as migrate, \
                patch.object(gr.time, "sleep"):
            self.assertEqual(gr._resume_when_available("abc", None), "novo")
        migrate.assert_called_once_with("abc", None)

    def test_migrate_copies_the_machine_and_switches_the_config(self):
        root = Path(tempfile.mkdtemp())
        config = root / "gpu_remota.md"
        config.write_text("- pod-id: velho\n- volume-id: vol\n- datacenter: EUR-NO-2\n", encoding="utf-8")
        calls = []

        def api(query, variables=None):
            calls.append((query, variables))
            if "pod(input" in query:
                return {"pod": {"name": "influencer-comfyui", "imageName": "img", "dockerArgs": "bash start", "ports": "22/tcp",
                                "env": ["PUBLIC_KEY=ssh-ed25519 AAA x"], "containerDiskInGb": 40, "volumeMountPath": "/workspace",
                                "networkVolumeId": "vol", "machine": {"dataCenterId": "EUR-NO-2"}}}
            if "myself" in query:
                return {"myself": {"pods": [{"id": "velho", "networkVolumeId": "vol"}, {"id": "outro", "networkVolumeId": "x"}]}}
            if "podFindAndDeployOnDemand" in query:
                return {"podFindAndDeployOnDemand": {"id": "novo", "desiredStatus": "RUNNING"}}
            return {"podTerminate": None}

        with patch.object(gr, "CONFIG_PATH", config), patch.object(gr, "STATE_PATH", root / "estado.json"), \
                patch.object(gr, "api", side_effect=api):
            self.assertEqual(gr.migrate("velho"), "novo")
        deploy = calls[2][1]["input"]
        self.assertEqual((deploy["networkVolumeId"], deploy["dataCenterId"], deploy["gpuCount"]), ("vol", "EUR-NO-2", 1))
        self.assertEqual(deploy["env"], [{"key": "PUBLIC_KEY", "value": "ssh-ed25519 AAA x"}])
        self.assertIn("podTerminate", calls[3][0])
        self.assertEqual(calls[3][1], {"id": "velho"})
        self.assertIn("- pod-id: novo\n- volume-id: vol", config.read_text(encoding="utf-8"))

    def test_other_errors_still_fail(self):
        with patch.object(gr, "resume", side_effect=RuntimeError("RunPod: saldo insuficiente")), patch.object(gr.time, "sleep"):
            with self.assertRaises(RuntimeError):
                gr._resume_when_available("abc", None)


class PrepareScriptTests(unittest.TestCase):
    def test_process_search_does_not_match_the_script_itself(self):
        import re
        for pattern in re.findall(r'p(?:kill|grep) -f "([^"]+)"', gr._PREPARE):
            self.assertIsNone(re.search(pattern, gr._PREPARE), pattern)  # senão o script se encerra sozinho


if __name__ == "__main__":
    unittest.main()


class PrepareCleanupTests(unittest.TestCase):
    def test_slow_cleanup_ssh_is_retried_and_deletion_runs_in_background(self):
        import subprocess

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "ssh" and "LIMPO" in cmd[-1] and len(calls) == 1:
                raise subprocess.TimeoutExpired(cmd, 120)

            class Result:
                returncode = 0
                stdout = "LIMPO PREPARADO"
                stderr = ""
            return Result()

        with patch.object(gr.subprocess, "run", side_effect=fake_run), patch.object(gr.time, "sleep"):
            gr.prepare_machine("1.2.3.4", 22)
        cleanup = calls[0][-1]
        self.assertIn("mv $d .apagar-", cleanup)
        self.assertIn("nohup rm -rf", cleanup)
        self.assertEqual(calls[1][-1], cleanup)  # tentou de novo depois do tempo esgotado


class MigrateCostTests(unittest.TestCase):
    def test_never_creates_a_second_machine_on_the_same_disk(self):
        def api(query, variables=None):
            if "pod(input" in query:
                return {"pod": {"networkVolumeId": "vol", "env": [], "machine": {}}}
            if "myself" in query:
                return {"myself": {"pods": [{"id": "velho", "networkVolumeId": "vol"}, {"id": "esquecida", "networkVolumeId": "vol"}]}}
            raise AssertionError("não pode criar máquina")

        with patch.object(gr, "api", side_effect=api):
            with self.assertRaisesRegex(RuntimeError, "esquecida"):
                gr.migrate("velho")


class CreatePodTests(unittest.TestCase):
    def test_missing_pod_is_recreated_on_the_same_disk_waiting_for_a_free_gpu(self):
        root = Path(tempfile.mkdtemp())
        config = root / "gpu_remota.md"
        config.write_bytes(b"- pod-id: velho\r\n- volume-id: vol1\r\n- datacenter: EUR-NO-2\r\n")
        key = root / "runpod_ssh"
        key.with_suffix(".pub").write_text("ssh-ed25519 AAA x", encoding="utf-8")
        calls = []

        def fake_api(query, variables=None):
            calls.append(query)
            if "myself" in query:
                return {"myself": {"pods": []}}
            if len([c for c in calls if "podFindAndDeployOnDemand" in c]) == 1:
                raise RuntimeError("RunPod: There are no longer any instances available with the requested specifications.")
            self.assertEqual(variables["input"]["networkVolumeId"], "vol1")
            self.assertEqual(variables["input"]["dataCenterId"], "EUR-NO-2")
            return {"podFindAndDeployOnDemand": {"id": "novo", "desiredStatus": "RUNNING"}}

        logs = []
        with patch.object(gr, "CONFIG_PATH", config), patch.object(gr, "SSH_KEY_PATH", key), \
                patch.object(gr, "api", side_effect=fake_api), patch.object(gr.time, "sleep"):
            self.assertEqual(gr.create_pod(logs.append), "novo")
        self.assertIn(b"- pod-id: novo\r\n", config.read_bytes())
        self.assertTrue(any("nova tentativa" in item for item in logs))
