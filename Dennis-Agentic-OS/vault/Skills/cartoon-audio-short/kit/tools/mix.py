"""Lock synthesized stems under the voice at the documented levels.

Usage: python3 mix.py voice.mp3 [music.wav] [sfx.wav] [out.wav]

Writes ``mix_raw.wav`` (mono, 48 kHz, 16-bit) for ``finish.sh``:
  * music at exactly 50 % of the voice's gated RMS (-6.0 dB)
  * SFX at 50 % (linear) of the synthesized stem, with peaks capped at
    0.7 x the voice peak
  * stem gains plus one look-ahead peak limiter on the whole mix; no
    ducking and no compression.

The voice is never re-timed or level-ridden, and the picture length is
taken from the voice.
"""
import os
import subprocess
import sys

import numpy as np

SR = 48000
FRAME = 0.05          # gated-RMS analysis frame (seconds)
GATE_DB = -40.0       # frames quieter than this vs. the loudest frame are silence
CEILING = 0.98        # limiter ceiling
LOOKAHEAD = 0.005     # limiter look-ahead (seconds)
RELEASE = 0.080       # limiter release (seconds)


def fail(msg):
    sys.stderr.write("mix.py: %s\n" % msg)
    raise SystemExit(1)


def decode(path):
    """Decode any audio/video file to mono float32 at SR via ffmpeg."""
    if not os.path.isfile(path):
        fail("input not found: %s" % path)
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    if p.returncode != 0:
        sys.stderr.write(p.stderr.decode("utf-8", "replace"))
        fail("ffmpeg could not decode %s" % path)
    return np.frombuffer(p.stdout, np.float32).copy()


def fit(x, n):
    """Trim or zero-pad a stem to exactly n samples."""
    if len(x) >= n:
        return x[:n]
    return np.pad(x, (0, n - len(x)))


def gated_rms(x):
    """RMS of the frames that carry signal, ignoring pauses/silence.

    Without the gate, pauses drag the measurement down and the music bed
    would be pushed too loud; with it we match the level the listener
    hears.
    """
    n = max(1, int(FRAME * SR))
    frames = len(x) // n
    if frames == 0:
        return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    blocks = x[: frames * n].reshape(frames, n)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
    loudest = float(rms.max())
    if loudest <= 0.0:
        return 0.0
    gate = loudest * (10.0 ** (GATE_DB / 20.0))
    active = rms[rms >= gate]
    if active.size == 0:
        active = rms
    return float(np.sqrt(np.mean(active.astype(np.float64) ** 2)))


def peak(x):
    return float(np.max(np.abs(x))) if len(x) else 0.0


def db(x):
    return 20.0 * np.log10(max(float(x), 1e-12))


def limit(x):
    """Look-ahead peak limiter: instant attack, smooth release."""
    if peak(x) <= CEILING:
        return x
    need = np.minimum(1.0, CEILING / np.maximum(np.abs(x), 1e-12))
    la = max(1, int(LOOKAHEAD * SR))
    # The gain at sample i must already cover the loudest peak in
    # [i, i + la], so take the minimum required gain over that window.
    padded = np.pad(need, (0, la), constant_values=1.0)
    win = np.lib.stride_tricks.sliding_window_view(padded, la + 1)
    target = win.min(axis=1)

    gain = np.empty_like(target)
    prev = 1.0
    release = float(np.exp(-1.0 / max(1.0, RELEASE * SR)))
    for i in range(len(target)):
        t = target[i]
        prev = t if t < prev else t + (prev - t) * release
        gain[i] = prev
    return np.clip(x * gain, -CEILING, CEILING)


def write_wav(path, x):
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main(argv):
    if len(argv) < 2:
        fail("usage: python3 mix.py voice.mp3 [music.wav] [sfx.wav] [out.wav]")
    voice_path = argv[1]
    music_path = argv[2] if len(argv) > 2 else "music.wav"
    sfx_path = argv[3] if len(argv) > 3 else "sfx.wav"
    out_path = argv[4] if len(argv) > 4 else "mix_raw.wav"

    voice = decode(voice_path)
    n = len(voice)
    if n == 0:
        fail("voice track is empty: %s" % voice_path)

    music_raw = decode(music_path)
    sfx_raw = decode(sfx_path)
    short = int(0.05 * SR)
    if len(music_raw) < n - short:
        print("warning: music stem is shorter than the voice; padding silence")
    if len(sfx_raw) < n - short:
        print("warning: sfx stem is shorter than the voice; padding silence")
    music = fit(music_raw, n)
    sfx = fit(sfx_raw, n)

    voice_rms = gated_rms(voice)
    voice_pk = peak(voice)
    if voice_rms <= 0.0 or voice_pk <= 0.0:
        fail("voice track is silent: %s" % voice_path)

    music_rms = gated_rms(music)
    music_pk = peak(music)
    sfx_rms = gated_rms(sfx)
    sfx_pk = peak(sfx)

    music_gain = 0.5 * voice_rms / music_rms if music_rms > 0 else 0.0
    # SFX sit at 50 % (linear) of the synthesized stem, then their peaks are
    # capped so no hit pokes out over the narration.
    sfx_gain = 0.5
    if sfx_pk * sfx_gain > 0.7 * voice_pk:
        sfx_gain = 0.7 * voice_pk / sfx_pk

    mix = voice + music * music_gain + sfx * sfx_gain
    mix = limit(mix)
    write_wav(out_path, mix)

    print("voice   gated RMS %6.1f dBFS   peak %6.1f dBFS   %.2f s"
          % (db(voice_rms), db(voice_pk), n / SR))
    print("music   gain %+6.2f dB vs voice   peak %6.1f dBFS   %.2f s"
          % (db(music_rms * music_gain) - db(voice_rms),
             db(music_pk * music_gain), n / SR))
    print("sfx     gain %+6.2f dB vs voice   peak %6.1f dBFS   %.2f s"
          % (db(sfx_rms * sfx_gain) - db(voice_rms),
             db(sfx_pk * sfx_gain), n / SR))
    print("        (sfx gain %.2f linear, peak cap %.2f x voice peak)"
          % (sfx_gain, 0.7))
    print("mix     peak %6.1f dBFS -> %s" % (db(peak(mix)), out_path))
    if music_pk == 0.0:
        print("warning: music stem is silent")
    if sfx_pk == 0.0:
        print("warning: sfx stem is silent")


if __name__ == "__main__":
    main(sys.argv)
