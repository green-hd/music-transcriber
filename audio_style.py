"""Destaque visual aproximado pela energia do áudio, sem um segundo modelo."""
from __future__ import annotations

import math

STEP = 0.05
SAMPLE_RATE = 16000


def audio_envelope(audio):
    """Decodifica em blocos e guarda só uma medida de energia a cada 50 ms."""
    import av
    import numpy as np

    block_size = round(STEP * SAMPLE_RATE)
    frequencies = np.fft.rfftfreq(block_size, 1 / SAMPLE_RATE)
    band = (frequencies >= 150) & (frequencies <= 4000)
    pending = np.empty(0, dtype=np.float32)
    levels = []
    sample_count = 0

    def measure(block):
        spectrum = np.fft.rfft(block, n=block_size)
        energy = float(np.sum(np.abs(spectrum[band]) ** 2) * 2 / block_size ** 2)
        levels.append(10 * math.log10(max(energy, 1e-10)))

    with av.open(str(audio)) as container:
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)

        def consume(frame):
            nonlocal pending, sample_count
            samples = frame.to_ndarray().reshape(-1)
            sample_count += samples.size
            pending = np.concatenate((pending, samples))
            count = len(pending) // block_size
            for index in range(count):
                measure(pending[index * block_size:(index + 1) * block_size])
            pending = pending[count * block_size:]

        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                consume(converted)
        for converted in resampler.resample(None):
            consume(converted)
        if pending.size:
            measure(pending)
    return levels, sample_count / SAMPLE_RATE


def mark_emphasis(data, levels, step=STEP):
    """Marca palavras fortes em relação à faixa e ao contexto próximo.

    A mistura inclui instrumentos: isto não identifica emoção nem isola o cantor.
    """
    import numpy as np

    levels = np.asarray(levels, dtype=float)
    entries = []
    for segment in data["segments"]:
        for word in segment["words"] or [segment]:
            word["emphasis"] = False
            left = max(0, int(word["start"] / step))
            right = min(len(levels), max(left + 1, math.ceil(word["end"] / step)))
            if left >= right:
                continue
            energy = levels[left:right]
            # Média em potência, para preservar o significado da escala em dB.
            loudness = 10 * math.log10(max(float(np.mean(10 ** (energy / 10))), 1e-10))
            entries.append((word, loudness, left, right))
    voiced = [level for _, level, _, _ in entries if level > -65]
    if not voiced:
        return
    threshold = max(float(np.percentile(voiced, 75)), float(np.median(voiced)) + 1.5)
    for word, loudness, left, right in entries:
        neighborhood = levels[max(0, left - 40):min(len(levels), right + 40)]
        local = float(np.median(neighborhood))
        word["emphasis"] = bool(loudness > -65 and loudness >= threshold and loudness >= local + 1.5)
