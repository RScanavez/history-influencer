import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import ambiente
from influencer_mcp import gpu_remota as gr
from influencer_mcp import painel_acoes as acoes


class LocalGpuTests(unittest.TestCase):
    def test_no_nvidia_driver_makes_local_queue_unavailable(self):
        with patch.object(ambiente.shutil, "which", return_value=None):
            result = ambiente.local_gpu()
        self.assertFalse(result["available"])
        self.assertIn("nvidia-smi", result["problems"][0])

    def test_jobs_default_to_remote_when_there_is_no_local_gpu(self):
        with patch.object(ambiente, "local_gpu_cached", return_value={"available": False}):
            self.assertEqual(ambiente.default_target(), "remota")
        with patch.object(ambiente, "local_gpu_cached", return_value={"available": True}):
            self.assertEqual(ambiente.default_target(), "local")


class RemoteChecksTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "secrets").mkdir()
        for item in (patch.object(gr, "CONFIG_PATH", self.root / "gpu_remota.md"),
                     patch.object(gr, "API_KEY_PATH", self.root / "secrets" / "runpod.txt"),
                     patch.object(gr, "SSH_KEY_PATH", self.root / "secrets" / "runpod_ssh")):
            item.start()
            self.addCleanup(item.stop)

    def test_missing_keys_explain_where_and_how(self):
        items = {item["id"]: item for item in ambiente.remote_checks()}
        self.assertFalse(items["api"]["ok"])
        self.assertIn("secrets/runpod.txt", items["api"]["help"])
        self.assertFalse(items["ssh"]["ok"])
        self.assertIn("ssh-keygen", items["ssh"]["help"])
        self.assertFalse(items["maquina"]["ok"])
        self.assertFalse(ambiente.remote_ready(list(items.values())))

    def test_ready_when_keys_registered_and_machine_configured(self):
        (self.root / "secrets" / "runpod.txt").write_text("chave")
        (self.root / "secrets" / "runpod_ssh").write_text("privada")
        (self.root / "secrets" / "runpod_ssh.pub").write_text("ssh-ed25519 AAAAX influencer-painel")
        (self.root / "gpu_remota.md").write_text("- pod-id: abc\n")
        with patch.object(gr, "api", return_value={"myself": {"pubKey": "ssh-ed25519 AAAAX outra", "clientBalance": 10}}):
            items = ambiente.remote_checks()
        self.assertTrue(all(item["ok"] for item in items), items)
        self.assertTrue(ambiente.remote_ready(items))

    def test_unregistered_public_key_is_pointed_out(self):
        (self.root / "secrets" / "runpod.txt").write_text("chave")
        (self.root / "secrets" / "runpod_ssh").write_text("privada")
        (self.root / "secrets" / "runpod_ssh.pub").write_text("ssh-ed25519 AAAAX influencer-painel")
        with patch.object(gr, "api", return_value={"myself": {"pubKey": "", "clientBalance": 10}}):
            items = {item["id"]: item for item in ambiente.remote_checks()}
        self.assertFalse(items["ssh-registrada"]["ok"])
        self.assertIn("SSH Public Keys", items["ssh-registrada"]["help"])


    def test_quick_check_does_not_block_when_registration_cannot_be_verified(self):
        (self.root / "secrets" / "runpod.txt").write_text("chave")
        (self.root / "secrets" / "runpod_ssh").write_text("privada")
        (self.root / "secrets" / "runpod_ssh.pub").write_text("ssh-ed25519 AAAAX influencer-painel")
        (self.root / "gpu_remota.md").write_text("- pod-id: abc\n")
        self.assertTrue(ambiente.remote_ready(ambiente.remote_checks(check_api=False)))


class BatchGpuChoiceTests(unittest.TestCase):
    def test_local_batch_refused_without_local_gpu(self):
        with patch.object(ambiente, "local_gpu", return_value={"available": False, "problems": ["sem NVIDIA."]}):
            with self.assertRaisesRegex(RuntimeError, "GPU remota"):
                acoes.iniciar_lote(1, "tema", "short", None, "local")

    def test_remote_batch_refused_with_instructions_when_not_ready(self):
        pending = [{"id": "api", "label": "Chave da API do RunPod", "ok": False, "help": "salve em secrets/runpod.txt"}]
        with patch.object(ambiente, "remote_checks", return_value=pending):
            with self.assertRaisesRegex(RuntimeError, "secrets/runpod.txt"):
                acoes.iniciar_lote(1, "tema", "short", None, "remota")


if __name__ == "__main__":
    unittest.main()
