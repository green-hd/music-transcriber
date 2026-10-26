import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import transcriber as app


class TranscriberTests(unittest.TestCase):
    def sample(self):
        return {"version": app.CACHE_VERSION, "key": "test", "language": "pt", "segments": [
            {"start": 59.9998, "end": 62.25, "text": "Olá, mundo!", "words": [
                {"start": 60, "end": 61, "text": "Olá,"},
                {"start": 61, "end": 62.25, "text": "mundo!"}]}]}

    def test_subtitles_utf8_and_timestamp_carry(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "faixa.v1"
            app.save_outputs(base, self.sample())
            self.assertEqual(Path(str(base) + ".txt").read_text(encoding="utf-8"), "Olá, mundo!\n")
            self.assertIn("00:01:00,000 --> 00:01:02,250", Path(str(base) + ".srt").read_text())
            self.assertIn("[01:00.00]Olá, mundo!", Path(str(base) + ".lrc").read_text())
            self.assertEqual(app.load_cache(Path(str(base) + ".json"), "test"), self.sample())

    def test_cache_rejects_corruption_and_invalid_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            for invalid in ["{", "[]", "null", '{"key": "test"}']:
                path.write_text(invalid)
                self.assertIsNone(app.load_cache(path, "test"))
            data = self.sample()
            data["segments"][0]["words"][0]["start"] = -1
            path.write_text(json.dumps(data))
            self.assertIsNone(app.load_cache(path, "test"))

    def test_cache_changes_with_audio_or_recognition_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audio.mp3"
            path.write_bytes(b"audio1")
            first = app.cache_key(path, {"model": "base"})
            self.assertNotEqual(first, app.cache_key(path, {"model": "small"}))
            path.write_bytes(b"audio2")
            self.assertNotEqual(first, app.cache_key(path, {"model": "base"}))

    def test_cli_reuses_cache_without_loading_model_and_refazer_bypasses_it(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "música.mp3"
            audio.write_bytes(b"test")
            args = [str(audio), "--saida", str(Path(folder) / "out"), "--sem-reproducao"]
            with patch.object(app, "transcribe", side_effect=lambda *a: self.sample()) as transcribe:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(app.main(args), 0)
                    self.assertEqual(app.main(args), 0)
                    self.assertEqual(transcribe.call_count, 1)
                    self.assertEqual(app.main(args + ["--refazer"]), 0)
                    self.assertEqual(transcribe.call_count, 2)

    def test_missing_audio_reports_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["/missing/song.mp3", "--sem-reproducao"]), 1)

    def test_download_uses_snapshot_path_and_offline_skips_downloader(self):
        from unittest.mock import Mock

        settings = {"model": "small", "language": "en", "beam_size": 5, "context": "Maddie Klein"}
        segment = SimpleNamespace(start=0, end=1, text="Hello", words=[])
        for offline in (False, True):
            with self.subTest(offline=offline):
                downloader = Mock(return_value="/cached/small")
                model = Mock()
                model.transcribe.return_value = (iter([segment]), SimpleNamespace(language="en"))
                factory = Mock(return_value=model)
                with patch.dict("sys.modules", {
                    "faster_whisper": SimpleNamespace(WhisperModel=factory),
                    "huggingface_hub": SimpleNamespace(snapshot_download=downloader),
                }), contextlib.redirect_stdout(io.StringIO()):
                    result = app.transcribe(Path("song.mp3"), settings, 2, offline)
                self.assertEqual(result["segments"][0]["text"], "Hello")
                self.assertEqual(factory.call_args.args[0], "small" if offline else "/cached/small")
                if offline:
                    downloader.assert_not_called()
                else:
                    downloader.assert_called_once()
                    self.assertEqual(downloader.call_args.kwargs["repo_id"], "Systran/faster-whisper-small")

    def test_characters_appear_individually_at_word_timestamps(self):
        events = []

        def wait_until(target):
            events.append((target, output.getvalue()))
            return True

        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(app.type_timed_text("gato", 1, 1.2, wait_until))
        self.assertEqual(output.getvalue(), "gato")
        self.assertEqual([text for _, text in events], ["", "g", "ga", "gat"])
        for actual, expected in zip([t for t, _ in events], [1, 1.05, 1.1, 1.15]):
            self.assertAlmostEqual(actual, expected)

    def test_typing_stops_when_playback_ends_mid_word(self):
        with patch("builtins.print") as output:
            self.assertFalse(app.type_timed_text("gato", 0, 0.4, lambda target: target == 0))
        output.assert_called_once_with("g", end="", flush=True)

    def test_playback_stops_waiting_when_audio_ends_and_releases_mixer(self):
        mixer = SimpleNamespace()
        from unittest.mock import Mock
        mixer.init = Mock()
        mixer.quit = Mock()
        mixer.music = SimpleNamespace(load=Mock(), set_volume=Mock(), play=Mock(), get_busy=Mock(return_value=False))
        pygame = SimpleNamespace(mixer=mixer, error=RuntimeError)
        with patch.dict("sys.modules", {"pygame": pygame}), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(app.play(Path("audio.mp3"), self.sample(), 0.5))
        mixer.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
