import contextlib
import io
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import player
import transcriber as core


class PlayerTests(unittest.TestCase):
    def test_offline_checkbox_is_forwarded_to_transcriber(self):
        for offline in (False, True):
            with self.subTest(offline=offline):
                window = player.Player.__new__(player.Player)
                window.busy = False
                window.audio = Path("song.mp3")
                window.mode = Mock(get=Mock(return_value="Equilibrado · small"))
                window.language = Mock(get=Mock(return_value="auto"))
                window.context = Mock(get=Mock(return_value=""))
                window.refazer = Mock(get=Mock(return_value=False))
                window.offline = Mock(get=Mock(return_value=offline))
                for name in ("stop", "set_lyrics", "prepare_button", "open_button", "library", "status", "progress"):
                    setattr(window, name, Mock())
                with patch("player.threading.Thread") as worker:
                    window.prepare()
                argv = worker.call_args.kwargs["args"][1]
                self.assertEqual("--offline" in argv, offline)
                self.assertEqual(core.parser().parse_args(argv).offline, offline)

    def test_sustained_word_extends_only_visual_text_and_keeps_punctuation(self):
        word = {"start": 1, "end": 3.5, "text": "rejection!"}
        self.assertEqual(player.display_word(word), "rejection!")
        stretched = player.display_word(word, stretch=True)
        self.assertTrue(stretched.startswith("rejectionnn"))
        self.assertTrue(stretched.endswith("!"))
        self.assertEqual(word["text"], "rejection!")
        self.assertEqual(player.display_word({**word, "end": 1.3}, True), "rejection!")

    def test_stretch_events_and_dot_offsets_share_same_text(self):
        data = {"segments": [{"start": 0, "end": 4, "text": "scared of rejection", "words": [
            {"start": 0, "end": 0.4, "text": "scared"},
            {"start": 0.4, "end": 0.6, "text": "of"},
            {"start": 0.6, "end": 4, "text": "rejection"}]}]}
        events = player.lyric_events(data, stretch=True)
        text = "".join(char for _, char in events)
        cues = player.word_cues(data, stretch=True)
        for cue in cues:
            self.assertEqual(text[cue.offset:cue.offset + len(cue.text)], cue.text)
        self.assertTrue(cues[-1].text.startswith("rejectionnn"))
        self.assertEqual([t for t, _ in events], sorted(t for t, _ in events))
        self.assertEqual(events[-1][0], 4)
        self.assertGreater(events[-2][0], 3)

    def test_zero_duration_or_punctuation_only_does_not_stretch(self):
        for word in ({"start": 1, "end": 1, "text": "oi"},
                     {"start": 0, "end": 3, "text": "..."}):
            self.assertEqual(player.display_word(word, True), word["text"])

    def test_animation_preserves_text_and_monotonic_times_for_overlapping_words(self):
        data = {"segments": [{"start": 0, "end": 2, "text": "Olá gato", "words": [
            {"start": 0, "end": 1, "text": "Olá"},
            {"start": 0.05, "end": 2, "text": "gato"}]}]}
        events = player.lyric_events(data)
        self.assertEqual("".join(char for _, char in events), "Olá gato\n\n")
        times = [t for t, _ in events]
        self.assertEqual(times, sorted(times))
        self.assertGreater(times[1], times[0])

    def test_segment_without_word_times_still_animates(self):
        events = player.lyric_events({"segments": [
            {"start": 10, "end": 11, "text": "green", "words": []}]})
        self.assertEqual("".join(char for _, char in events), "green\n\n")
        self.assertEqual(events[0][0], 10)
        self.assertGreater(events[1][0], 10)

    def test_emphasis_is_applied_only_to_characters_of_loud_word(self):
        events = player.lyric_events({"segments": [
            {"start": 0, "end": 1, "text": "green", "words": [
                {"start": 0, "end": 1, "text": "green", "emphasis": True}]}]}, styled=True)
        self.assertEqual([tag for _, char, tag in events if char.strip()], ["impact"] * 5)
        self.assertEqual(events[-1][2], "")

    def test_pause_and_resume_use_audio_pause_without_restarting(self):
        window = player.Player.__new__(player.Player)
        window.playing, window.paused = True, False
        music = Mock()
        window.pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
        window.pause_button, window.status = Mock(), Mock()
        window.toggle_pause()
        self.assertTrue(window.paused)
        music.pause.assert_called_once()
        window.toggle_pause()
        self.assertFalse(window.paused)
        music.unpause.assert_called_once()
        music.play.assert_not_called()

    def test_closed_window_does_not_start_a_transcription_process(self):
        window = player.Player.__new__(player.Player)
        window.process_lock = threading.Lock()
        window.closed = threading.Event()
        window.closed.set()
        window.process = None
        window.messages = queue.Queue()
        with patch("player.subprocess.Popen") as process:
            window.worker(Path("song.mp3"), [])
        process.assert_not_called()

    def test_worker_reads_cli_cache_and_reports_ready(self):
        window = player.Player.__new__(player.Player)
        window.process_lock = threading.Lock()
        window.closed = threading.Event()
        window.process = None
        window.messages = queue.Queue()
        process = Mock(stdout=io.StringIO("Resultado salvo\n"))
        process.wait.return_value = 0
        data = {"segments": [{"end": 20}]}
        container = Mock()
        container.__enter__ = Mock(return_value=SimpleNamespace(duration=30000000))
        container.__exit__ = Mock(return_value=False)
        with patch("player.subprocess.Popen", return_value=process), \
                patch.object(core, "cache_key", return_value="abc"), \
                patch.object(core, "load_cache", return_value=data), \
                patch.dict("sys.modules", {"av": SimpleNamespace(open=Mock(return_value=container), time_base=1000000)}), \
                contextlib.redirect_stdout(io.StringIO()):
            window.worker(Path("song.mp3"), ["song.mp3", "--sem-reproducao"])
        self.assertEqual(window.messages.get(), ("status", "Resultado salvo"))
        self.assertEqual(window.messages.get()[0], "status")
        self.assertEqual(window.messages.get(), ("ready", (Path("song.mp3"), data, 30)))
        self.assertIsNone(window.process)


if __name__ == "__main__":
    unittest.main()
