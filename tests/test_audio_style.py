import math
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from audio_style import audio_envelope, mark_emphasis


class AudioStyleTests(unittest.TestCase):
    def data(self):
        return {"segments": [{"start": 0, "end": 5, "text": "a b c d e", "words": [
            {"start": n, "end": n + 1, "text": str(n)} for n in range(5)]}]}

    def test_louder_word_is_emphasized_and_volume_gain_does_not_change_selection(self):
        for gain in (0, -12):
            data = self.data()
            levels = [-30 + gain] * 40 + [-18 + gain] * 20 + [-30 + gain] * 40
            mark_emphasis(data, levels)
            self.assertEqual([w["emphasis"] for w in data["segments"][0]["words"]],
                             [False, False, True, False, False])

    def test_silence_uniform_volume_and_missing_audio_do_not_add_emphasis(self):
        for levels in ([-100] * 100, [-25] * 100, []):
            data = self.data()
            mark_emphasis(data, levels)
            self.assertFalse(any(w["emphasis"] for w in data["segments"][0]["words"]))

    def test_decode_measures_source_audio_instead_of_player_volume(self):
        rate = 16000
        samples = np.sin(2 * math.pi * 440 * np.arange(rate) / rate)
        samples[:rate // 2] *= 0.1
        samples[rate // 2:] *= 0.5
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dynamics.wav"
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(rate)
                target.writeframes((samples * 32767).astype('<i2').tobytes())
            levels, duration = audio_envelope(path)
        self.assertAlmostEqual(duration, 1, places=2)
        self.assertEqual(len(levels), 20)
        self.assertGreater(np.mean(levels[12:]) - np.mean(levels[:8]), 12)


if __name__ == "__main__":
    unittest.main()
