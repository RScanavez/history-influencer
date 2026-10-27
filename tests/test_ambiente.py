import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import ambiente
from influencer_mcp import painel_acoes as acoes


class LocalGpuTests(unittest.TestCase):
    def test_no_nvidia_driver_makes_queue_unavailable(self):
        with patch.object(ambiente, "apple_silicon", return_value=False), patch.object(ambiente.shutil, "which", return_value=None):
            result = ambiente.local_gpu()
        self.assertFalse(result["available"])
        self.assertIn("nvidia-smi", result["problems"][0])

    def test_apple_silicon_counts_as_gpu_and_only_needs_comfyui(self):
        folder = Path(tempfile.mkdtemp())
        with patch.object(ambiente, "apple_silicon", return_value=True), \
                patch.object(ambiente, "apple_chip_name", return_value="Apple M6"), \
                patch("influencer_mcp.comfyui.comfyui_dir", return_value=folder):
            missing = ambiente.local_gpu()
            self.assertFalse(missing["available"])
            self.assertIn("ComfyUI não encontrado", missing["problems"][0])
            (folder / "main.py").write_text("")
            (folder / "venv" / "bin").mkdir(parents=True)
            (folder / "venv" / "bin" / "python").write_text("")
            with patch.object(ambiente, "venv_python", side_effect=lambda venv: venv / "bin" / "python"):
                ready = ambiente.local_gpu()
        self.assertTrue(ready["available"])
        self.assertIn("Apple M6", ready["gpus"][0])


class BatchRefusalTests(unittest.TestCase):
    def test_batch_refused_without_usable_gpu(self):
        with patch.object(ambiente, "local_gpu", return_value={"available": False, "problems": ["sem ComfyUI."]}):
            with self.assertRaisesRegex(RuntimeError, "sem ComfyUI"):
                acoes.iniciar_lote(1, "tema", "short")


class MacSensorsTests(unittest.TestCase):
    def test_chip_temperature_is_the_hottest_die_sensor(self):
        from influencer_mcp import sensores_mac

        sensors = {"PMU tdie1": 41.2, "PMU2 tdie3": 55.5, "NAND CH0 temp": 70.0, "PMU tcal": 90.0}
        with patch.object(sensores_mac, "temperatures", return_value=sensors):
            self.assertEqual(sensores_mac.chip_temperature(), 55.5)
        with patch.object(sensores_mac, "temperatures", return_value={}):
            self.assertIsNone(sensores_mac.chip_temperature())

    def test_apple_gpu_usage_reads_ioreg_and_thermal_state(self):
        ioreg = '"PerformanceStatistics" = {"Alloc system memory"=2147483648,"Device Utilization %"=37}'
        results = iter([type("R", (), {"stdout": ioreg})(), type("R", (), {"stdout": "CPU_Speed_Limit = 80"})()])
        with patch.object(ambiente.subprocess, "run", side_effect=lambda *a, **k: next(results)), \
                patch.object(ambiente, "apple_chip_name", return_value="Apple M6"), \
                patch.object(ambiente, "chip_temperature", return_value=48.0):
            gpu = ambiente.apple_gpu_usage(32.0)
        self.assertEqual((gpu["utilization"], gpu["vramUsedGb"], gpu["temperature"], gpu["thermal"]), (37.0, 2.0, 48.0, "limitada"))


if __name__ == "__main__":
    unittest.main()
