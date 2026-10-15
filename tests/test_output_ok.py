import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import output_ok, painel, youtube_publish as yp


class OutputOkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.out = self.root / "output"
        self.out.mkdir()
        self.gen = self.root / "generated"
        self.folder = self.gen / "historiador" / "2026-10-02-0050" / "motivacao"
        (self.folder / "fr").mkdir(parents=True)
        (self.folder / "motion-script.json").write_text("{}", encoding="utf-8")
        (self.folder / "short.mp4").write_bytes(b"")
        (self.folder / "output-export.json").write_text(json.dumps({"base": "Sem vontade - a motivação vem depois"}), encoding="utf-8")
        (self.folder / "fr" / "output-export.json").write_text(json.dumps({"base": "Pas envie - la motivation_FRA"}), encoding="utf-8")
        for item in (patch.object(output_ok, "GENERATED_ROOT", self.gen), patch.object(yp, "GENERATED_ROOT", self.gen)):
            item.start()
            self.addCleanup(item.stop)

    def touch(self, name):
        (self.out / name).write_bytes(b"")

    def test_ok_prefix_variants_mark_only_that_language(self):
        self.assertFalse(output_ok.published_in_output(self.folder))
        self.touch("Sem vontade - a motivação vem depois.mp4")  # sem OK: ainda não publicado
        self.assertFalse(output_ok.published_in_output(self.folder))
        for name in ("OK - Sem vontade - a motivação vem depois.mp4", "OK – Sem vontade - a motivação vem depois.mp4",
                     "OK_Sem vontade - a motivação vem depois.mp4", "ok - sem vontade - a motivação vem depois.mp4"):
            with self.subTest(name=name):
                for path in self.out.glob("OK*"):
                    path.unlink()
                for path in self.out.glob("ok*"):
                    path.unlink()
                self.touch(name)
                self.assertTrue(output_ok.published_in_output(self.folder), name)
        self.assertFalse(output_ok.published_in_output(self.folder, "fr"))
        self.touch("OK - Pas envie - la motivation_FRA.mp4")
        self.assertTrue(output_ok.published_in_output(self.folder, "fr"))

    def test_title_starting_with_ok_is_not_a_marker(self):
        (self.folder / "output-export.json").write_text(json.dumps({"base": "kinawa"}), encoding="utf-8")
        self.touch("OKinawa.mp4")
        self.assertFalse(output_ok.published_in_output(self.folder))

    def test_ok_marks_publication_and_blocks_automatic_upload(self):
        (self.folder / "painel.json").write_text(json.dumps(
            {"stage": "Aprovado", "history": [{"at": "2026-10-02T10:00:00+00:00", "action": "Aprovado"}]}), encoding="utf-8")
        cutoff = yp.parse_cutoff("2026-10-02T00:00:00+00:00", yp.parse_offset("-03:00"))
        with patch.object(painel_acoes_module(), "GENERATED_ROOT", self.gen):
            self.assertEqual(yp.approved_queue(cutoff), [self.folder])
            self.touch("OK - Sem vontade - a motivação vem depois.mp4")
            self.assertEqual(yp.approved_queue(cutoff), [])
            self.assertEqual(painel.sync_manual_published(), ["motivacao"])
            self.assertEqual(yp.read_publication(self.folder)["status"], "publicado")
            self.assertEqual(painel.sync_manual_published(), [])  # não marca de novo

    def test_scheduled_upload_marks_mp4_and_txt_and_keeps_status(self):
        shorts = self.out / "shorts"
        shorts.mkdir()
        (shorts / "Sem vontade - a motivação vem depois.mp4").write_bytes(b"")
        (shorts / "Sem vontade - a motivação vem depois.txt").write_bytes(b"")
        with patch.object(output_ok, "output_root", return_value=self.out),                 patch("influencer_mcp.formats.output_dir", return_value=shorts):
            yp.write_publication(self.folder, status="agendado", videoId="v1")
            yp.mark_output_processed(self.folder)
            self.assertEqual(sorted(p.name for p in shorts.iterdir()),
                             ["OK - Sem vontade - a motivação vem depois.mp4", "OK - Sem vontade - a motivação vem depois.txt"])
            self.assertTrue(yp.read_publication(self.folder)["outputOk"])
            # O "OK - " de um vídeo enviado pelo painel não vira "publicado": vale o status do YouTube.
            self.assertEqual(painel.sync_manual_published(), [])
            self.assertEqual(yp.read_publication(self.folder)["status"], "agendado")

    def test_file_in_use_is_retried_later(self):
        with patch.object(output_ok, "mark_processed", side_effect=PermissionError("em uso")):
            yp.write_publication(self.folder, status="agendado", videoId="v1")
            yp.mark_output_processed(self.folder)
        record = yp.read_publication(self.folder)
        self.assertFalse(record.get("outputOk"))
        self.assertIn("em uso", record["outputOkError"])
        with patch.object(output_ok, "mark_processed", return_value=["x"]) as mark:
            yp.Publisher(lambda: {}).sync_output_marks()
        mark.assert_called_once()
        self.assertTrue(yp.read_publication(self.folder)["outputOk"])

    def test_reexport_keeps_ok_prefix(self):
        from influencer_mcp import media
        shorts = self.out / "shorts"
        shorts.mkdir()
        (self.folder / "youtube-metadata.json").write_text(json.dumps({"title": "Sem vontade: a motivação vem depois"}), encoding="utf-8")
        (self.folder / "youtube-metadata.txt").write_text("txt", encoding="utf-8")
        (shorts / "OK - Sem vontade - a motivação vem depois.mp4").write_bytes(b"velho")
        (shorts / "OK - Sem vontade - a motivação vem depois.txt").write_bytes(b"velho")
        with patch("influencer_mcp.formats.output_dir", return_value=shorts):
            media.export_to_output(self.folder)
        self.assertEqual(sorted(p.name for p in shorts.iterdir()),
                         ["OK - Sem vontade - a motivação vem depois.mp4", "OK - Sem vontade - a motivação vem depois.txt"])

    def test_cover_follows_ok_and_archive_moves_ok_files_only(self):
        for name in ("shorts", "videos"):
            (self.out / name).mkdir()
        videos = self.out / "videos"
        for name in ("OK - Hábito.mp4", "OK - Hábito.txt", "Hábito.jpg", "Novo.mp4", "Novo.txt", "Novo.jpg"):
            (videos / name).write_bytes(b"")
        (self.out / "shorts" / "OK - Short.mp4").write_bytes(b"")
        self.assertEqual(output_ok.sync_ok_covers(self.out), ["OK - Hábito.jpg"])
        result = output_ok.archive_published(self.out)
        self.assertEqual(result["videos"], ["OK - Hábito.jpg", "OK - Hábito.mp4", "OK - Hábito.txt"])
        self.assertEqual(result["shorts"], ["OK - Short.mp4"])
        self.assertEqual(sorted(p.name for p in videos.iterdir()), ["Novo.jpg", "Novo.mp4", "Novo.txt"])
        self.assertTrue((self.out / "archive" / "shorts" / "OK - Short.mp4").is_file())
        # Arquivado continua reconhecido como publicado.
        self.assertIn(output_ok._normalize("Hábito"), output_ok.ok_names(self.out))

    def test_reexport_of_archived_video_stays_in_archive_with_ok(self):
        archived = self.out / "archive" / "shorts"
        archived.mkdir(parents=True)
        (archived / "OK - Título.mp4").write_bytes(b"velho")
        source = self.root / "novo.mp4"
        source.write_bytes(b"novo")
        with patch.object(output_ok, "output_root", return_value=self.out),                 patch("influencer_mcp.formats.output_dir", return_value=self.out / "shorts"):
            dest = output_ok.export_files("short", "Título", "Título", {".mp4": source})
        self.assertEqual(dest, archived / "OK - Título.mp4")
        self.assertEqual(dest.read_bytes(), b"novo")
        self.assertFalse((self.out / "shorts" / "Título.mp4").exists())

    def test_cleanup_only_when_pt_fr_en_archived_and_keeps_upload_history(self):
        from datetime import UTC, datetime
        from influencer_mcp import painel_acoes
        archived = self.out / "archive" / "shorts"
        archived.mkdir(parents=True)
        (self.folder / "en").mkdir()
        (self.folder / "en" / "output-export.json").write_text(json.dumps({"base": "No motivation_ENG"}), encoding="utf-8")
        yp.write_publication(self.folder, status="agendado", videoId="v1", slot="2026-10-03T09:00:00-03:00",
                             uploadedAt=datetime.now(UTC).isoformat(timespec="seconds"))
        jobs, lotes = self.gen / "jobs", self.gen / "lotes"
        jobs.mkdir()
        lotes.mkdir()
        with patch.object(output_ok, "output_root", return_value=self.out),                 patch.object(painel_acoes, "GENERATED_ROOT", self.gen), patch.object(painel_acoes, "JOBS_DIR", jobs),                 patch.object(painel_acoes, "LOTES_DIR", lotes), patch.object(painel_acoes, "_active_jobs", return_value=[]),                 patch.object(yp, "ARCHIVED_LEDGER", self.gen / "publicacoes-arquivadas.jsonl"):
            (archived / "OK - Sem vontade - a motivação vem depois.mp4").write_bytes(b"")
            (archived / "OK - Pas envie - la motivation_FRA.mp4").write_bytes(b"")
            self.assertEqual(painel_acoes.limpar_arquivados()["cleaned"], [])  # falta o EN
            self.assertTrue(self.folder.exists())
            (archived / "OK - No motivation_ENG.mp4").write_bytes(b"")
            # Com comentário sugerido ainda não postado, a pasta fica: o publicador precisa dela para postá-lo.
            (self.folder / "youtube-metadata.json").write_text(json.dumps({"pinnedCommentSuggestion": "Conta aqui"}), encoding="utf-8")
            self.assertEqual(painel_acoes.limpar_arquivados()["cleaned"], [])
            yp.write_publication(self.folder, commentId="c1")
            self.assertEqual(painel_acoes.limpar_arquivados()["cleaned"], ["motivacao"])
            self.assertFalse(self.folder.exists())
            self.assertFalse(self.folder.parent.exists())  # lote vazio também sai
            # O envio continua contando na cota de 24 h e ocupando o horário da grade.
            self.assertEqual(yp.uploads_last_24h(datetime.now(UTC)), 1)
            self.assertEqual(len(yp.taken_slots()), 1)


def painel_acoes_module():
    from influencer_mcp import painel_acoes
    return painel_acoes


if __name__ == "__main__":
    unittest.main()
