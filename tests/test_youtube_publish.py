import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import painel_acoes, youtube_publish as yp

BRT = timezone(timedelta(hours=-3))
FLOW = {"times": ["09:00", "12:00", "15:00", "18:00"], "timezone": "-03:00", "audit": "aprovada",
        "approvedSince": "2026-10-01T00:00:00+00:00"}


def make_approved(root: Path, slug: str, approved_at: str) -> Path:
    folder = root / "historiador" / "2026-10-01-1214" / slug
    folder.mkdir(parents=True)
    (folder / "motion-script.json").write_text("{}", encoding="utf-8")
    (folder / "short.mp4").write_bytes(b"")
    (folder / "youtube-metadata.json").write_text(json.dumps({
        "title": "Título", "description": "Desc", "descriptionForUpload": "Desc\n\n#a #b", "tags": ["x", "y"],
        "pinnedCommentSuggestion": "Comente!",
    }), encoding="utf-8")
    (folder / "painel.json").write_text(json.dumps({"stage": "Aprovado", "history": [{"at": approved_at, "action": "Aprovado"}]}), encoding="utf-8")
    return folder


class YoutubePublishTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for item in (patch.object(yp, "GENERATED_ROOT", self.root), patch.object(painel_acoes, "GENERATED_ROOT", self.root),
                     patch.object(yp, "ARCHIVED_LEDGER", self.root / "publicacoes-arquivadas.jsonl")):
            item.start()
            self.addCleanup(item.stop)

    def test_schedule_changed_in_studio_is_followed_without_false_warning(self):
        from unittest.mock import MagicMock
        folder = make_approved(self.root, "remarcado", "2026-10-06T08:50:00+00:00")
        yp.write_publication(folder, status="agendado", videoId="v1", slot="2026-10-06T09:17:00+00:00")
        api = MagicMock()
        api.videos().list().execute.return_value = {"items": [
            {"id": "v1", "status": {"privacyStatus": "private", "publishAt": "2026-10-06T12:17:00Z"}}]}
        with patch.object(yp, "youtube", return_value=api):
            yp.refresh_published(datetime(2026, 10, 6, 10, 0, tzinfo=UTC))
        record = yp.read_publication(folder)
        self.assertEqual(record["slot"], "2026-10-06T12:17:00+00:00")
        self.assertIsNone(record["warning"])
        self.assertEqual(record["status"], "agendado")

    def test_parse_offset(self):
        self.assertEqual(yp.parse_offset("-03:00"), BRT)
        self.assertEqual(yp.parse_offset("UTC−3"), BRT)
        self.assertEqual(yp.parse_offset("+05:30"), timezone(timedelta(hours=5, minutes=30)))
        with self.assertRaises(ValueError):
            yp.parse_offset("America/Sao_Paulo")

    def test_audit_line_controls_release(self):
        self.assertTrue(yp.settings(FLOW)["audit"])
        self.assertFalse(yp.settings({**FLOW, "audit": "pendente"})["audit"])
        self.assertFalse(yp.settings({**FLOW, "audit": None})["audit"])

    def test_slots_skip_less_than_fifteen_minutes_ahead_and_roll_to_next_day(self):
        cfg = yp.settings(FLOW)
        now = datetime(2026, 10, 1, 17, 50, tzinfo=BRT).astimezone(UTC)  # 18:00 está a 10 min: pula
        slots = list(yp.slot_candidates(now, cfg, days=2))
        self.assertEqual(slots[0], datetime(2026, 10, 2, 9, 0, tzinfo=BRT))
        self.assertEqual(len(slots), 4)
        # Com 30 min de folga, o 18:00 do mesmo dia é o primeiro disponível (era pulado com a folga antiga de 1 h).
        soon = datetime(2026, 10, 1, 17, 30, tzinfo=BRT).astimezone(UTC)
        self.assertEqual(next(yp.slot_candidates(soon, cfg)), datetime(2026, 10, 1, 18, 0, tzinfo=BRT))

    def test_projected_slots_follow_approval_order_and_skip_taken(self):
        first = make_approved(self.root, "primeiro", "2026-10-01T10:00:00+00:00")
        second = make_approved(self.root, "segundo", "2026-10-01T11:00:00+00:00")
        busy = make_approved(self.root, "ja-agendado", "2026-10-01T09:00:00+00:00")
        yp.write_publication(busy, status="agendado", slot=datetime(2026, 10, 2, 9, 0, tzinfo=BRT).isoformat())
        now = datetime(2026, 10, 1, 20, 0, tzinfo=BRT)
        projected = yp.projected_slots(now, yp.settings(FLOW))
        self.assertEqual(list(projected), [first, second])
        self.assertEqual(projected[first], datetime(2026, 10, 2, 12, 0, tzinfo=BRT))
        self.assertEqual(projected[second], datetime(2026, 10, 2, 15, 0, tzinfo=BRT))

    def test_upload_body_matches_user_decisions(self):
        folder = make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        body = yp.upload_body(folder, datetime(2026, 10, 2, 9, 0, tzinfo=BRT))
        self.assertEqual(body["status"], {"privacyStatus": "private", "publishAt": "2026-10-02T12:00:00Z",
                                          "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True})
        self.assertEqual(body["snippet"]["categoryId"], "26")
        self.assertEqual(body["snippet"]["defaultAudioLanguage"], "pt-BR")
        # Palavra-chave primeiro, assinatura do Instagram no fim (2026-10-14).
        self.assertTrue(body["snippet"]["description"].startswith("Desc"))
        self.assertTrue(body["snippet"]["description"].endswith(f"#a #b\n\n{yp.DESCRIPTION_INTRO}"))
        self.assertNotIn("#Shorts", body["snippet"]["description"])

    def test_publisher_waits_for_audit_and_token(self):
        make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        publisher = yp.Publisher(lambda: {**FLOW, "audit": "pendente"})
        with patch.object(yp, "upload") as upload, patch.object(yp, "TOKEN_PATH", self.root / "token.json"):
            (self.root / "token.json").write_text("{}")
            publisher.tick(datetime.now(UTC))
        upload.assert_not_called()

    def test_publisher_uploads_all_approved_in_order_without_daily_limit(self):
        for index in range(6):
            make_approved(self.root, f"video-{index}", f"2026-10-01T10:0{index}:00+00:00")
        now = datetime(2026, 10, 1, 20, 0, tzinfo=BRT).astimezone(UTC)

        def fake_upload(folder, slot):
            return yp.write_publication(folder, status="agendado", slot=slot.isoformat(), videoId=folder.name,
                                        uploadedAt=now.isoformat())

        publisher = yp.Publisher(lambda: FLOW)
        with (
            patch.object(yp, "TOKEN_PATH", self.root / "token.json"),
            patch.object(yp, "refresh_published"),
            patch.object(yp, "upload", side_effect=fake_upload) as upload,
        ):
            (self.root / "token.json").write_text("{}")
            publisher.tick(now)
        self.assertEqual(upload.call_count, 6)  # sem limite por 24 h desde 2026-10-10: todos sobem na hora
        sent = [call.args[0].name for call in upload.call_args_list]
        self.assertEqual(sent, [f"video-{index}" for index in range(6)])
        slots = [call.args[1] for call in upload.call_args_list]
        self.assertEqual(len(set(slots)), 6)  # cada um num horário diferente da grade, passando para o dia seguinte

    def test_upload_error_is_recorded_and_retried_later(self):
        folder = make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        now = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)

        def failing(folder, slot):
            yp.write_publication(folder, attemptAt=now.isoformat())
            raise RuntimeError("rede caiu")

        publisher = yp.Publisher(lambda: FLOW)
        with (
            patch.object(yp, "TOKEN_PATH", self.root / "token.json"),
            patch.object(yp, "refresh_published"),
            patch.object(yp, "upload", side_effect=failing) as upload,
        ):
            (self.root / "token.json").write_text("{}")
            publisher.tick(now)
            publisher.tick(now + timedelta(minutes=5))  # cedo demais para tentar de novo
            self.assertEqual(upload.call_count, 1)
            publisher.tick(now + timedelta(minutes=31))
            self.assertEqual(upload.call_count, 2)
        self.assertEqual(yp.read_publication(folder)["status"], "erro")

    def test_comment_is_seo_question_with_subscribe_reminder_below(self):
        self.assertEqual(yp.comment_text("Quantos segundos você aguenta?"),
                         "Quantos segundos você aguenta?\n\n" + yp.SUBSCRIBE_REMINDER)
        self.assertEqual(yp.comment_text(""), yp.SUBSCRIBE_REMINDER)  # sem pergunta do SEO, só o lembrete
        self.assertIn("sininho", yp.SUBSCRIBE_REMINDER)

    def test_comment_and_cancel_require_right_status(self):
        folder = make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        yp.write_publication(folder, status="agendado", videoId="abc", slot="2026-10-02T09:00:00-03:00")
        with self.assertRaises(RuntimeError):
            yp.post_comment(folder)
        yp.write_publication(folder, status="publicado")
        with self.assertRaises(RuntimeError):
            yp.cancel(folder)

    def test_review_state_cannot_change_after_sending(self):
        folder = make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        yp.write_publication(folder, status="agendado")
        with patch.object(painel_acoes, "_active_jobs", return_value=[]):
            with self.assertRaises(RuntimeError):
                painel_acoes.definir_estado("2026-10-01-1214", "video", "Ajustes")

    def test_only_approvals_after_cutoff_are_sent(self):
        old = make_approved(self.root, "antigo", "2026-10-01T10:00:00+00:00")
        new = make_approved(self.root, "novo", "2026-10-02T10:00:00+00:00")
        cutoff = yp.parse_cutoff("2026-10-02T00:36:26+01:00", BRT)
        self.assertEqual(yp.approved_queue(cutoff), [new])
        self.assertEqual(yp.approved_before_cutoff(cutoff), [old])

    def test_missing_or_invalid_cutoff_sends_nothing(self):
        make_approved(self.root, "video", "2026-10-02T10:00:00+00:00")
        self.assertIsNone(yp.parse_cutoff(None, BRT))
        self.assertIsNone(yp.parse_cutoff("ontem", BRT))
        self.assertEqual(yp.approved_queue(None), [])
        publisher = yp.Publisher(lambda: {**FLOW, "approvedSince": None})
        with patch.object(yp, "upload") as upload, patch.object(yp, "TOKEN_PATH", self.root / "token.json"),                 patch.object(yp, "refresh_published"):
            (self.root / "token.json").write_text("{}")
            publisher.tick(datetime(2026, 10, 2, 12, tzinfo=UTC))
        upload.assert_not_called()

    def test_reapproving_after_cutoff_puts_video_back_in_queue(self):
        folder = make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        cutoff = yp.parse_cutoff("2026-10-02T00:00:00+00:00", BRT)
        self.assertEqual(yp.approved_queue(cutoff), [])
        state = json.loads((folder / "painel.json").read_text(encoding="utf-8"))
        state["history"].append({"at": "2026-10-02T09:00:00+00:00", "action": "Aprovado"})
        (folder / "painel.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(yp.approved_queue(cutoff), [folder])

    def test_cancel_keeps_current_status_fields_and_only_sets_private(self):
        from unittest.mock import MagicMock
        folder = make_approved(self.root, "video", "2026-10-02T10:00:00+00:00")
        yp.write_publication(folder, status="agendado", videoId="abc", slot="2026-10-03T09:00:00-03:00")
        api = MagicMock()
        api.videos().list().execute.return_value = {"items": [{"status": {
            "privacyStatus": "private", "publishAt": "2026-10-03T12:00:00Z", "embeddable": True,
            "publicStatsViewable": True, "license": "youtube", "selfDeclaredMadeForKids": False, "uploadStatus": "processed"}}]}
        with patch.object(yp, "youtube", return_value=api):
            yp.cancel(folder)
        body = api.videos().update.call_args.kwargs["body"]
        self.assertEqual(body["status"], {"license": "youtube", "embeddable": True, "publicStatsViewable": True,
                                          "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
                                          "privacyStatus": "private"})
        self.assertEqual(yp.read_publication(folder)["status"], "cancelado")

    def test_playlist_waits_until_enabled_then_adds_sent_videos(self):
        sent = make_approved(self.root, "enviado", "2026-10-02T10:00:00+00:00")
        yp.write_publication(sent, status="agendado", videoId="v1", slot="2026-10-03T09:00:00-03:00")
        cancelled = make_approved(self.root, "cancelado", "2026-10-02T10:00:00+00:00")
        yp.write_publication(cancelled, status="cancelado", videoId="v2")
        manual = make_approved(self.root, "manual", "2026-10-02T10:00:00+00:00")
        yp.write_publication(manual, status="publicado", source="manual")
        self.assertEqual(sorted(f.name for f in yp.pending_playlist()), ["cancelado", "enviado"])
        publisher = yp.Publisher(lambda: FLOW)
        off = yp.settings({**FLOW, "playlist": "Dicas diárias", "playlistActive": "nao"})
        on = yp.settings({**FLOW, "playlist": "Dicas diárias", "playlistActive": "sim"})
        with patch.object(yp, "add_to_playlist", side_effect=lambda f, n: yp.write_publication(f, playlistItemId="x")) as add:
            publisher.sync_playlist(off)
            add.assert_not_called()
            publisher.sync_playlist(on)
            self.assertEqual(add.call_count, 2)
            publisher.sync_playlist(on)
            self.assertEqual(add.call_count, 2)  # já incluídos não são reenviados


    def test_immediate_mode_publishes_every_approved_video_now_as_public(self):
        flow = {**FLOW, "times": ["imediato"]}
        cfg = yp.settings(flow)
        self.assertTrue(cfg["immediate"])
        for index in range(6):
            make_approved(self.root, f"video-{index}", f"2026-10-01T10:0{index}:00+00:00")
        now = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)

        def fake_upload(folder, slot):
            self.assertIsNone(slot)
            return yp.write_publication(folder, status="publicado", videoId=folder.name, uploadedAt=now.isoformat())

        with (
            patch.object(yp, "TOKEN_PATH", self.root / "token.json"),
            patch.object(yp, "refresh_published"),
            patch.object(yp, "upload", side_effect=fake_upload) as upload,
        ):
            (self.root / "token.json").write_text("{}")
            yp.Publisher(lambda: flow).tick(now)
        self.assertEqual(upload.call_count, 6)  # sem limite por horário

    def test_immediate_upload_body_is_public_without_publish_at(self):
        folder = make_approved(self.root, "video", "2026-10-01T10:00:00+00:00")
        status = yp.upload_body(folder, None)["status"]
        self.assertEqual(status["privacyStatus"], "public")
        self.assertNotIn("publishAt", status)
        self.assertTrue(status["containsSyntheticMedia"])


    def test_immediate_mode_schedules_twenty_minutes_after_approval(self):
        cfg = yp.settings({**FLOW, "times": ["imediato"], "delayMinutes": "20"})
        folder = make_approved(self.root, "video", "2026-10-04T10:00:00+00:00")
        on_time = yp.immediate_slot(folder, datetime(2026, 10, 4, 10, 1, tzinfo=UTC), cfg)
        self.assertEqual(on_time, datetime(2026, 10, 4, 10, 20, tzinfo=UTC))
        late = yp.immediate_slot(folder, datetime(2026, 10, 4, 13, 0, tzinfo=UTC), cfg)  # envio atrasou (cota)
        self.assertEqual(late, datetime(2026, 10, 4, 13, 20, tzinfo=UTC))
        self.assertIsNone(yp.immediate_slot(folder, datetime(2026, 10, 4, 10, 1, tzinfo=UTC), {**cfg, "delay_minutes": 0}))


if __name__ == "__main__":
    unittest.main()


class AutomaticCommentTests(unittest.TestCase):
    def _folder(self, **record):
        folder = Path(tempfile.mkdtemp())
        yp.write_publication(folder, **record)
        return folder

    def test_only_published_videos_uploaded_after_the_switch_get_the_comment(self):
        since = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)
        now = since + timedelta(hours=1)
        base = {"status": "publicado", "videoId": "v", "uploadedAt": (since + timedelta(minutes=5)).isoformat()}
        new = self._folder(**base)
        old = self._folder(**{**base, "uploadedAt": (since - timedelta(days=1)).isoformat()})
        scheduled = self._folder(**{**base, "status": "agendado"})
        done = self._folder(**{**base, "commentId": "c"})
        tired = self._folder(**{**base, "commentAttempts": 5})
        recent_try = self._folder(**{**base, "commentAttemptAt": (now - timedelta(minutes=3)).isoformat()})
        folders = [new, old, scheduled, done, tired, recent_try]
        self.assertEqual(yp.pending_comments(since, now, folders), [new])
        self.assertEqual(yp.pending_comments(None, now, folders), [])

    def test_failed_comment_is_recorded_and_retried_later(self):
        since = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)
        now = since + timedelta(hours=1)
        folder = self._folder(status="publicado", videoId="v", uploadedAt=(since + timedelta(minutes=5)).isoformat())
        publisher = yp.Publisher(lambda: {})
        with patch.object(yp, "pending_comments", return_value=[folder]), \
                patch.object(yp, "post_comment", side_effect=RuntimeError("processando")):
            publisher.sync_comments({"commentSince": since}, now)
        record = yp.read_publication(folder)
        self.assertEqual((record["commentAttempts"], record["commentError"]), (1, "processando"))
        self.assertEqual(yp.pending_comments(since, now + timedelta(minutes=5), [folder]), [])
        self.assertEqual(yp.pending_comments(since, now + timedelta(minutes=11), [folder]), [folder])

    def test_comment_switch_is_read_from_the_flow(self):
        cfg = yp.settings({"timezone": "-03:00", "commentSince": "2026-10-04T17:23:20-03:00"})
        self.assertEqual(cfg["commentSince"], datetime(2026, 10, 4, 20, 23, 20, tzinfo=UTC))
        self.assertIsNone(yp.settings({"timezone": "-03:00"})["commentSince"])


class CommentBacklogTests(unittest.TestCase):
    def test_backlog_waits_for_quota_reset_and_respects_daily_cap(self):
        from unittest.mock import MagicMock
        root = Path(tempfile.mkdtemp())
        path = root / "comentarios-pendentes.json"
        items = [{"videoId": f"v{n}", "text": "t"} for n in range(3)]
        path.write_text(json.dumps({"notBefore": "2026-10-07T07:05:00+00:00", "items": items}), encoding="utf-8")
        api = MagicMock()
        api.commentThreads().insert().execute.return_value = {"id": "c"}
        with patch.object(yp, "BACKLOG_PATH", path), patch.object(yp, "youtube", return_value=api), \
                patch.object(yp, "BACKLOG_PER_DAY", 2):
            self.assertIsNone(yp.sync_comment_backlog(datetime(2026, 10, 7, 7, 0, tzinfo=UTC)))  # antes da cota renovar
            self.assertEqual(yp.sync_comment_backlog(datetime(2026, 10, 7, 7, 6, tzinfo=UTC)), "v0")
            self.assertEqual(yp.sync_comment_backlog(datetime(2026, 10, 7, 7, 7, tzinfo=UTC)), "v1")
            self.assertIsNone(yp.sync_comment_backlog(datetime(2026, 10, 7, 7, 8, tzinfo=UTC)))  # limite de 24 h
            self.assertEqual(yp.sync_comment_backlog(datetime(2026, 10, 8, 7, 7, tzinfo=UTC)), "v2")
