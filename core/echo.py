"""
Telling the user's voice apart from our own coming back through the speakers.

What it is used for today
-------------------------
The *echo tail*: for a moment after a reply ends, sound is still leaving the
speakers, because writing to an audio device returns when the buffer accepts the
audio and not when the room has finished with it. Streaming the microphone
during that gap is how an assistant hears its own last sentence and answers
itself. This module lets the tail drop our own voice while still passing a user
who replies instantly, instead of blanket-muting.

Interrupting mid-sentence by voice runs on the same machinery: the
microphone callback judges each block with `quick=True` (spectral test plus
the caller's sustain counter), which needs no per-room tuning because the
room model converges during speech itself. See `_learn_echo`.

The problem with a level test
-----------------------------
Deciding "the microphone is louder than the echo" needs to know how loud the
echo *is*, and that is not a constant. It depends on the speaker volume, where
the microphone sits, the room, whether headphones are plugged in, and whether
the user happens to have a hand over the mic. A single tuned number is wrong for
almost everyone: too eager on a loud desktop speaker, too deaf on a quiet laptop.

What this does instead
----------------------
Two independent signals, neither of which needs tuning per machine:

1. **Content.** Echo is not merely loud — it is *the same sound we just played*.
   Both streams are reduced to a handful of band energies, and then — rather
   than merely compared — as much of the recent output as fits is *subtracted*
   from the microphone block. Pure echo cancels to almost nothing. A second
   voice cannot be cancelled by ours, because its formants sit in bands where
   ours were weak, so it survives the subtraction. That distinction holds even
   when the two arrive at the same loudness, which is exactly the case a level
   test gets wrong.

2. **A learned echo gain.** Whenever the content check says "that is definitely
   just echo", the observed mic-to-output ratio is folded into a running
   estimate. The system therefore calibrates itself to the actual room within a
   few seconds of the first sentence, and re-calibrates when the volume changes
   or a hand covers the microphone.

Band energies rather than raw spectra also make the comparison sample-rate
agnostic, which matters here: the microphone runs at 16 kHz and playback at
24 kHz.
"""

from __future__ import annotations

import time

import numpy as np

# Log-spaced edges across the range that carries speech. Coarse on purpose:
# fine bins would track pitch, and pitch is exactly what differs between two
# people saying the same word — we want the *timbre* that echo preserves.
_BAND_EDGES = (200, 400, 700, 1100, 1700, 2600, 3800, 5200, 7000)

_HISTORY_S = 1.5      # how far back an echo could plausibly have been played
_MIN_LEVEL = 0.06     # below this the mic is room noise; nothing to decide

# ── Adaptive ambient floor ────────────────────────────────────────────────
# Residuals measure DISSIMILARITY, so steady room tone (fan, hum, decay tails
# in quiet gaps) scores high and looks exactly like a quiet voice — there was
# no "neither" category, only echo-vs-user. The floor below supplies it: the
# room's own return, tracked live, and anything at or under it never reaches
# the spectral judge at all.
#
# Bounds, and why each is safe:
#   _AMBIENT_INIT (0.16) — start assuming a real room; converge DOWN fast.
#     Downward is the safe direction (a high floor only ever misses, and a
#     miss degrades to "heard between turns" since the normal listening path
#     streams everything ungated).
#   _AMBIENT band [0.06, 0.22] — only quiet blocks train the tracker, so loud
#     echo and normal speech can never lift it; digital silence (< 0.06) is
#     ignored so gaps cannot drag it to zero either.
#   _FLOOR_MARGIN (1.5) — the floor sits clearly above the tracked room tone
#     (a tone at the ceiling with +50% headroom) while conversational speech
#     (~0.3+) clears it comfortably.
#   _FLOOR_MAX (0.25) — the floor can never reach conversational speech no
#     matter how noisy the room gets; worst case it filters whispers, never
#     normal voices.
#   rates: down 0.03 (~2 s to follow a quieter room), up 0.002 (minutes to be
#     dragged up by transient quiet speech — contamination is bounded and
#     self-healing).
_AMBIENT_INIT  = 0.16
_AMBIENT_MIN   = 0.06
_AMBIENT_MAX   = 0.22
_AMBIENT_DOWN  = 0.03
_AMBIENT_UP    = 0.002
_FLOOR_MARGIN  = 1.5
_FLOOR_MAX     = 0.25

# The threshold is not a tuned constant. It is placed just above essentially ALL
# the echo this particular room has been seen to produce. Residuals measured on
# synthetic material, median over many blocks:
#
#   room                    echo p99   a distinct voice   a similar voice
#   headphones                0.09           0.28              0.16
#   normal desk               0.13           0.28              0.16
#   reverberant room          0.23           0.27              0.18
#   loud, distorting, noisy   0.31           0.33              0.26
#
# Read that table honestly. How good the room is decides what is possible: in a
# quiet setup even a voice whose formants sit close to ours stands clear of the
# echo, and in a bad one nothing reliably does. The right response to the last
# row is to say so — not to interrupt at random.
_MIN_USER = 0.15      # never call anything below this a voice
# A block this loud next to recent playback cannot be room echo: echo comes
# back quieter than what was played (learned gain sits near 0.6), so mic
# audio several times louder than the loudest recent output — and plain loud
# in absolute terms — is a nearby voice, whatever the spectrum says.
_BARGE_LOUD = 0.35
_BARGE_LOUD_MULT = 3.0
# While still calibrating, only an extreme outlier counts without the
# sustained-agreement the caller demands: worst measured pure-echo residual
# is ~0.31, so the absolute floor below separates voice from echo even
# before the room is characterised.
_WARM_OUTLIER_FLOOR = 0.30
# How far above the learned echo return a block must be to count as a
# second voice on loudness alone. The room returns `gain` mic-level per unit
# of output level, so pure echo always scores ~1.0 here no matter how loud
# the speakers are — unlike the absolute `_BARGE_LOUD` override, this cannot
# be tripped by turning the volume up.
_BARGE_GAIN_MULT = 2.2
_HEAD_Q = 97          # percentile of observed echo the threshold must clear
_HEAD_MULT = 1.15     # ...with this much headroom above it

# Above this echo floor, content separates the two poorly. Loudness cannot
# rescue it — in exactly those rooms the echo already saturates the level meter,
# so a level test can never be passed. Time can: echo wobbles around its median
# while a person talking holds above it, so such rooms hold the evidence longer.
_UNRELIABLE_FLOOR = 0.22
_BLOCKS_NORMAL = 5    # ~320 ms of sustained evidence
_BLOCKS_NOISY = 6     # ~380 ms; echo still needs sustained guarded evidence

_FLOOR_WINDOW = 60    # blocks kept to characterise the room's echo
_FLOOR_Q = 35         # percentile taken as "typical echo here"
_WARMUP = 16          # blocks (~1 s) of listening before judging anyone
_RELEARN_RUN = 28     # a 'voice' lasting this long means the room changed


def barge_fallback_candidate(level: float, ambient_floor: float,
                             similarity: float) -> bool:
    """Conservative fallback when mixed speech masks spectral separation."""
    floor = max(0.10, min(0.28, float(ambient_floor) * 1.8))
    return float(level) >= floor and float(similarity) < 0.98


def band_energies(pcm, sr: int) -> np.ndarray:
    """Raw energy per speech band — the fingerprint we compare.

    Left unnormalised on purpose: the decision below projects one of these onto
    another, and a projection needs real magnitudes.

    The microphone callback hands us 2-D frames (samples × channels) while
    playback notes 1-D slices. An unflattened 2-D block broadcasts against the
    analysis window into a 1024×1024 matrix and then fails boolean indexing —
    which surfaced as every barge-in judgement returning False. Mono is all
    this pipeline ever carries, so take the first channel.
    """
    x = np.asarray(pcm, dtype=np.float32)
    if x.ndim > 1:
        x = x[:, 0]
    if x.size < 64:
        return np.zeros(len(_BAND_EDGES) - 1, dtype=np.float32)
    x = x - x.mean()
    n = 1 << (int(x.size) - 1).bit_length()          # next power of two
    mag = np.abs(np.fft.rfft(x * np.hanning(x.size), n=n))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    out = np.empty(len(_BAND_EDGES) - 1, dtype=np.float32)
    for i in range(len(_BAND_EDGES) - 1):
        m = (freqs >= _BAND_EDGES[i]) & (freqs < _BAND_EDGES[i + 1])
        out[i] = float(mag[m].sum())
    return out


class EchoGuard:
    """Classifies microphone blocks while the assistant is speaking.

    Usage: `note_output()` from the playback path, `is_user_speech()` from the
    microphone callback. Both are cheap enough to sit in an audio thread — one
    small FFT each.
    """

    def __init__(self) -> None:
        self._hist: list[tuple[float, np.ndarray, float]] = []   # (t, bands, level)
        self._gain = 0.6          # mic level per unit of output level; learned
        self._seen = 0            # how many echo blocks the estimate has seen
        self._last_sim = 0.0
        self._last_expected = 0.0
        self._last_why = '-'    # quick-path verdict: T/G/L/O or - (echo/silence)
        self._residuals: list[float] = []   # recent ECHO residuals only
        self._floor = 0.10        # typical echo residual here; learned
        self._run = 0             # consecutive blocks called speech
        self._ambient = _AMBIENT_INIT   # tracked room return; see _track_ambient
        # Start assuming a real room, not headphones: an optimistic init
        # (0.13) puts the voice bar below ordinary speaker echo, so the very
        # first reply trips over itself before any calibration exists. The
        # bar only ever converges DOWN toward observed echo, which can never
        # manufacture a false trip.
        self._head = 0.30         # near-worst echo residual here; learned

    # ── diagnostics, for the log and for tests ──────────────────────────────

    @property
    def gain(self) -> float:
        return self._gain

    @property
    def calibrated(self) -> bool:
        """True once the estimate rests on enough real echo to be trusted."""
        return self._seen >= 8

    @property
    def floor(self) -> float:
        """Residual left by this room's own echo. Higher = harder to separate."""
        return self._floor

    @property
    def reliable(self) -> bool:
        """False when the acoustics are too poor to judge on content alone.

        Speakers turned up in a reverberant room leave an echo that survives the
        subtraction almost as well as a quiet voice does. Rather than guess, the
        guard says so and starts demanding corroborating evidence.
        """
        return self._floor < _UNRELIABLE_FLOOR

    @property
    def threshold(self) -> float:
        """The residual a block must clear right now to count as a voice."""
        return max(_MIN_USER, self._head * _HEAD_MULT)

    @property
    def required_blocks(self) -> int:
        """Consecutive positive blocks before an interruption is believed."""
        return _BLOCKS_NORMAL if self.reliable else _BLOCKS_NOISY

    @property
    def last_similarity(self) -> float:
        """1.0 = fully explained by our own output, 0.0 = nothing to do with it."""
        return self._last_sim

    @property
    def last_why(self) -> str:
        """Which quick-path branch judged the last block: T=hreshold,
        G=gain, L=loud override, O=outlier, -=echo/silence."""
        return self._last_why

    def reset(self) -> None:
        """Playback stopped — drop the history, keep what was learned."""
        self._hist.clear()
        self._last_sim = 0.0

    def _track_ambient(self, level: float) -> None:
        """Follow the room's quiet return. Called with every judged block;
        only levels inside the ambient band move the tracker, so loud echo,
        normal speech and digital silence can neither lift it nor zero it."""
        try:
            if not (level >= 0.0):
                return                 # NaN/inf guard
            if level < _MIN_LEVEL or level > _AMBIENT_MAX:
                return
            if level < self._ambient:
                self._ambient += (level - self._ambient) * _AMBIENT_DOWN
            else:
                self._ambient += (level - self._ambient) * _AMBIENT_UP
            self._ambient = max(_AMBIENT_MIN, min(_AMBIENT_MAX, self._ambient))
        except Exception:
            pass

    def ambient_floor(self) -> float:
        """Existence gate: at or under this level is room, not a voice."""
        try:
            return min(_FLOOR_MAX, max(_MIN_LEVEL, self._ambient * _FLOOR_MARGIN))
        except Exception:
            return _MIN_LEVEL

    def _learn_echo(self, residual: float) -> None:
        """Fold an unambiguous echo residual into the room model.

        Only ever called with residuals BELOW the voice bar, so user speech
        cannot train the model upward: the bar converges down toward observed
        echo and can never descend past genuine returns into false trips.
        Shared by the tail judge and the barge-in judge.
        """
        self._residuals.append(residual)
        if len(self._residuals) > _FLOOR_WINDOW:
            del self._residuals[:-_FLOOR_WINDOW]
        if len(self._residuals) >= _WARMUP:
            self._floor = float(np.percentile(self._residuals, _FLOOR_Q))
            self._head = float(np.percentile(self._residuals, _HEAD_Q))

    # ── the two entry points ────────────────────────────────────────────────

    def note_output(self, pcm, sr: int, level: float, when: float | None = None) -> None:
        """Record a slice of what is being played, for later comparison."""
        try:
            t = time.monotonic() if when is None else when
            self._hist.append((t, band_energies(pcm, sr), float(level)))
            cutoff = t - _HISTORY_S
            if len(self._hist) > 8:
                self._hist = [h for h in self._hist if h[0] >= cutoff]
        except Exception:
            pass          # never let bookkeeping disturb playback

    def is_user_speech(self, pcm, sr: int, level: float,
                       when: float | None = None, quick: bool = False) -> bool:
        """True if this microphone block is a different voice, not our echo.

        `quick=True` is the barge-in judge: it never learns (a block may
        contain the user by definition, so it must not train the room model)
        and warmup never vetoes - the caller already demands `required_blocks`
        of consecutive agreement, which is the sustain protection warmup was
        standing in for. The default path is unchanged.
        """
        try:
            self._last_why = '-'
            if level < _MIN_LEVEL:
                # We are playing and the microphone hears nothing back:
                # this setup returns no echo at all (headphones, or a mic
                # far from the speaker). Record that, or the window stays
                # empty and the first person to speak gets mistaken for
                # the calibration sample.
                if self._hist and max(h[2] for h in self._hist) > 0.15:
                    self._residuals.append(0.0)
                    if len(self._residuals) > _FLOOR_WINDOW:
                        del self._residuals[:-_FLOOR_WINDOW]
                    if len(self._residuals) >= _WARMUP:
                        self._floor = float(np.percentile(self._residuals, _FLOOR_Q))
                        self._head = float(np.percentile(self._residuals, _HEAD_Q))
                self._run = 0
                return False
            # Shared existence gate: the tracked room return never reaches the
            # spectral judge. Steady room tone and quiet decay tails are
            # maximally dissimilar to speech fingerprints, so without this they
            # score as voices. Genuine speech clears it with headroom; the
            # normal listening path streams everything ungated regardless.
            self._track_ambient(level)
            if level <= self.ambient_floor():
                return False
            if not self._hist:
                # Nothing playing that we know of — anything audible is theirs.
                self._last_why = 'O'
                return True

            t = time.monotonic() if when is None else when
            bands = band_energies(pcm, sr)
            total = float(bands.sum())
            if total <= 1e-9:
                return False

            # Find the recent output slice that best explains this block, and
            # subtract as much of it as fits. What is left over is whatever the
            # microphone heard that we did not play.
            best_res, best_level = 1.0, 0.0
            for ts, ref, ref_level in self._hist:
                if ts > t or t - ts > _HISTORY_S:
                    continue
                denom = float(np.dot(ref, ref))
                if denom < 1e-12:
                    continue
                alpha = max(0.0, float(np.dot(bands, ref)) / denom)
                residual = np.maximum(bands - alpha * ref, 0.0)
                ratio = float(residual.sum()) / total
                if ratio < best_res:
                    best_res, best_level = ratio, ref_level
            self._last_sim = 1.0 - best_res

            # Nothing in the window explained it at all — that is not our sound.
            if best_level <= 0.0:
                self._last_why = 'O'
                return True

            if quick:
                # Barge-in judging only: no run counting, and the room model
                # learns ONLY from below-bar blocks, so user speech can never
                # train it upward. The caller already demands `required_blocks`
                # of sustain, which is the time dimension the tail judge gets
                # from its warmup veto. The default path below is unchanged.
                if best_res < self.threshold:
                    self._learn_echo(best_res)
                    # Same safe moment to learn the room's loudness return
                    # (below-bar blocks are echo by this judgement): feeds the
                    # gain-relative test below. Mirrors the tail-path update.
                    if best_level > 0.05:
                        obs = level / max(best_level, 1e-6)
                        self._gain += (min(obs, 3.0) - self._gain) * 0.08
                        self._seen = min(self._seen + 1, 999)
                    return False
                if (level > _BARGE_LOUD and best_level > 0.0
                        and level > _BARGE_LOUD_MULT * max(best_level, 0.05)):
                    self._last_why = 'L'
                    return True   # far louder than this room ever echoes back
                # Bound by the loudest recent output, not just the matching
                # slice: a 64 ms mic peak is compared against ~200 ms output
                # slices, so per-slice levels understate what the room may
                # legitimately return on a loud syllable.
                _ref_peak = best_level
                try:
                    for _ts, _rb, _rl in self._hist:
                        if _rl > _ref_peak:
                            _ref_peak = _rl
                except Exception:
                    pass
                if self.calibrated and level > (
                        _BARGE_GAIN_MULT * self._gain * max(_ref_peak, 0.05)):
                    # Gain-normalized loudness: this block carries far more
                    # energy than the learned echo return predicts, so a second
                    # voice is present even where spectra overlap (loud
                    # speakers bury the user's formants in echo energy and the
                    # residual test alone stays quiet). Pure echo scores ~1.0
                    # by construction, at any speaker volume.
                    self._last_why = 'G'
                    return True
                if len(self._residuals) >= _WARMUP:
                    self._last_why = 'T'
                    return True   # at/above a calibrated bar: sustained by caller
                # Uncalibrated: only an extreme outlier counts. The floor is
                # absolute on purpose — scaling it with the (pessimistic)
                # initial head would deafen fast loud interruptions.
                self._last_why = 'O' if best_res >= _WARM_OUTLIER_FLOOR else '-'
                return best_res >= _WARM_OUTLIER_FLOOR

            # Learning happens in two stages, and the order matters both times.
            #
            # While warming up, learn from EVERY block. Judging first is a trap:
            # in a poor room the very first echo block already sits above any
            # sensible starting bar, so it would be called a voice, never be
            # learned from, and the room would stay mis-characterised forever.
            #
            # Once warm, learn only from blocks BELOW the bar. Feeding the
            # user's own blocks back in is the opposite trap: they lift the
            # threshold over themselves and the guard goes deaf to the very
            # thing it is watching for.
            warming = len(self._residuals) < _WARMUP
            thr = self.threshold
            speech = (not warming) and best_res >= thr

            if speech:
                self._run += 1
                # A run this long is not someone interrupting — nobody talks
                # over an assistant for seconds on end. The room changed under
                # us (volume, headphones unplugged), so characterise it again.
                if self._run > _RELEARN_RUN:
                    self._residuals.clear()
                    self._run = 0
                    return False
                return True

            self._run = 0
            self._residuals.append(best_res)
            if len(self._residuals) > _FLOOR_WINDOW:
                del self._residuals[:-_FLOOR_WINDOW]
            if len(self._residuals) >= _WARMUP:
                self._floor = float(np.percentile(self._residuals, _FLOOR_Q))
                self._head = float(np.percentile(self._residuals, _HEAD_Q))
            if warming:
                return False

            # Comfortably just us: also a safe moment to learn how loudly this
            # room returns our voice.
            if best_res <= self._floor * 1.15 and best_level > 0.05:
                obs = level / max(best_level, 1e-6)
                self._gain += (min(obs, 3.0) - self._gain) * 0.08
                self._seen = min(self._seen + 1, 999)
            return False
        except Exception:
            return False      # any doubt: do not interrupt
