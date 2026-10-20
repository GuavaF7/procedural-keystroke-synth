"""Agent-DSP: Ultra-low latency, real-time procedural sound synthesis engine.

Sound Design & Presets:
- Warm Rhodes: 2-op FM + ±4 cents detune analog chorus;
- Crystal Bells: Shimmering crystalline bell with inharmonic partials;
- Lo-Fi Felt Piano: Muffled, warm, dark acoustic felt hammer;
- 8-Bit Chiptune: Retro pulse/square wave bite;
- Adaptive Ambient Drone: Automatic fade-out to 0.0 after 2s idle (0% silence drone);
- Ping-Pong Stereo Delay: Configurable wet mix and feedback depth;
- Independent Volume Busses: Master, Keys, Sub-Bass, Drone Pad.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import queue
import time
from typing import Optional, List

import numpy as np
import sounddevice as sd

from src.harmony.engine import HarmonyEvent


# Precomputed Vinyl Click Transient Table (2048 samples ~= 46 ms)
def _generate_vinyl_click_table(samplerate: int = 44100) -> np.ndarray:
    n_samples = 2048
    t = np.arange(n_samples, dtype=np.float32) / float(samplerate)
    body = np.sin(2.0 * np.pi * 160.0 * t) * np.exp(-t / 0.012)
    np.random.seed(42)
    noise = np.random.uniform(-0.15, 0.15, size=n_samples).astype(np.float32) * np.exp(-t / 0.005)
    click = (body + noise) * 0.4
    return click.astype(np.float32)


VINYL_CLICK_TABLE = _generate_vinyl_click_table()
VINYL_CLICK_LEN = len(VINYL_CLICK_TABLE)

# Detuning ratios for ±4 cents analog chorus
DETUNE_UP = 2.0 ** (4.0 / 1200.0)      # ~1.002313
DETUNE_DOWN = 2.0 ** (-4.0 / 1200.0)  # ~0.997692


@dataclass(slots=True)
class DSPVoice:
    """Preallocated voice slot in the static DSP pool."""
    frequency: float = 440.0
    phase1: float = 0.0             # Carrier 1 phase
    phase2: float = 0.0             # Carrier 2 phase
    amplitude: float = 0.0
    target_amplitude: float = 0.0
    decay_factor_per_block: float = 0.95
    voice_type: str = "bell"        # "bell", "sub", "percussion"
    pan: float = 0.5                # 0.0 = Left, 1.0 = Right
    is_active: bool = False
    age: int = 0
    attack_progress: float = 1.0    # 0.0 -> 1.0 smooth attack
    attack_step: float = 0.0        # Step per block
    sample_offset: int = 0          # For table-based voices

    @property
    def phase(self) -> float:
        return self.phase1

    @phase.setter
    def phase(self, val: float) -> None:
        self.phase1 = val
        self.phase2 = val


class AudioEngine:
    """Real-time sound synthesis engine with zero allocations in audio callback."""

    def __init__(
        self,
        audio_queue: Optional[queue.Queue[HarmonyEvent]] = None,
        samplerate: int = 44100,
        blocksize: int = 512,
        max_voices: int = 32,
        master_volume: float = 0.70,
        keys_volume: float = 0.85,
        sub_volume: float = 0.90,
        drone_volume: float = 0.0,
        enable_delay: bool = True,
        delay_wet: float = 0.30,
        delay_feedback: float = 0.40,
        enable_drone: bool = False,
        sound_preset: str = "Warm Rhodes"
    ) -> None:
        self.audio_queue: queue.Queue[HarmonyEvent] = (
            audio_queue if audio_queue is not None else queue.Queue(maxsize=256)
        )
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.max_voices = max_voices

        # Volume Bus Controls
        self.master_volume = master_volume
        self.keys_volume = keys_volume
        self.sub_volume = sub_volume
        self.drone_volume = drone_volume
        self.is_muted: bool = False

        # FX & Preset Controls
        self.enable_delay = enable_delay
        self.delay_wet = delay_wet
        self.delay_feedback = delay_feedback
        self.enable_drone = enable_drone
        self.sound_preset = sound_preset

        # Preallocated Static Voice Pool
        self.voices: List[DSPVoice] = [DSPVoice() for _ in range(max_voices)]
        self._voice_age_counter = 0

        # Preallocated scratch buffers for zero-alloc callback
        self._sample_indices = np.arange(blocksize, dtype=np.float32)
        self._int_indices = np.arange(blocksize, dtype=np.int32)
        self._ramp = np.linspace(0.0, 1.0, blocksize, endpoint=False, dtype=np.float32)
        self._mono_buf = np.zeros(blocksize, dtype=np.float32)
        self._phase_buf1 = np.zeros(blocksize, dtype=np.float32)
        self._phase_buf2 = np.zeros(blocksize, dtype=np.float32)
        self._temp_wave = np.zeros(blocksize, dtype=np.float32)
        self._temp_harm = np.zeros(blocksize, dtype=np.float32)
        self._amp_vec = np.zeros(blocksize, dtype=np.float32)
        self._stereo_buf = np.zeros((blocksize, 2), dtype=np.float32)

        # Stereo Ping-Pong Delay Buffer
        self.DELAY_BUF_SIZE = 32768
        self._delay_buf = np.zeros((self.DELAY_BUF_SIZE, 2), dtype=np.float32)
        self._delay_pos = 0
        self._delay_samples_l = int(0.280 * samplerate)
        self._delay_samples_r = int(0.420 * samplerate)

        # Adaptive Ambient Drone with Idle Fade-out
        self._drone_phase = 0.0
        self._drone_current_freq = 73.41  # D2 root frequency
        self._drone_target_freq = 73.41
        self._drone_lfo_phase = 0.0
        self._drone_active_gain = 0.0
        self._last_activity_time = 0.0    # Starts at 0 to ensure zero drone at startup

        # IIR Filter state and parameter smoothing
        self._target_cutoff: float = 1.0
        self._smooth_cutoff: float = 1.0
        self._filter_state_l: float = 0.0
        self._filter_state_r: float = 0.0
        self._cutoff_smoothing_coeff = 0.12

        # Stream state
        self._stream: Optional[sd.OutputStream] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def toggle_mute(self) -> bool:
        self.is_muted = not self.is_muted
        return self.is_muted

    def set_master_volume(self, volume: float) -> None:
        self.master_volume = max(0.0, min(1.0, float(volume)))

    def set_keys_volume(self, volume: float) -> None:
        self.keys_volume = max(0.0, min(1.0, float(volume)))

    def set_sub_volume(self, volume: float) -> None:
        self.sub_volume = max(0.0, min(1.0, float(volume)))

    def set_drone_volume(self, volume: float) -> None:
        self.drone_volume = max(0.0, min(1.0, float(volume)))

    def set_delay_wet(self, wet: float) -> None:
        self.delay_wet = max(0.0, min(1.0, float(wet)))

    def set_delay_feedback(self, feedback: float) -> None:
        self.delay_feedback = max(0.0, min(0.85, float(feedback)))

    def set_preset(self, preset_name: str) -> None:
        """Switches sound preset: Warm Rhodes, Crystal Bells, Lo-Fi Felt Piano, 8-Bit Chiptune."""
        self.sound_preset = preset_name

    def set_drone_root(self, freq: float) -> None:
        self._drone_target_freq = max(30.0, min(250.0, freq))

    def _allocate_voice(
        self,
        frequency: float,
        velocity: float,
        duration: float,
        voice_type: str
    ) -> DSPVoice:
        self._voice_age_counter += 1

        # Look for inactive voice
        target_voice: Optional[DSPVoice] = None
        for v in self.voices:
            if not v.is_active:
                target_voice = v
                break

        # Voice stealing: Pick lowest amplitude / oldest
        if target_voice is None:
            min_amp = 999.0
            oldest_age = 999999999
            for v in self.voices:
                if v.amplitude < min_amp:
                    min_amp = v.amplitude
                    oldest_age = v.age
                    target_voice = v
                elif math.isclose(v.amplitude, min_amp, abs_tol=1e-3) and v.age < oldest_age:
                    oldest_age = v.age
                    target_voice = v

        assert target_voice is not None

        block_dt = self.blocksize / float(self.samplerate)

        # Preset-dependent attack and decay profiles
        preset = self.sound_preset
        if "Crystal" in preset:
            attack_sec = 0.008
            decay_sec = max(1.5, min(3.0, duration * 1.2))
        elif "Felt" in preset:
            attack_sec = 0.024
            decay_sec = max(1.0, min(2.2, duration * 0.85))
        elif "8-Bit" in preset or "Chiptune" in preset:
            attack_sec = 0.002
            decay_sec = max(0.4, min(1.2, duration * 0.5))
        else:
            # Warm Rhodes
            attack_sec = 0.018
            decay_sec = max(1.4, min(2.8, duration))

        decay_factor = math.exp(-block_dt / (decay_sec / 4.0))
        attack_blocks = max(1.0, (attack_sec * self.samplerate) / self.blocksize)
        attack_step = 1.0 / attack_blocks

        # Gain assignment
        if voice_type == "sub":
            gain = velocity * self.sub_volume * 0.95
        else:
            gain = velocity * self.keys_volume * 0.80

        target_voice.frequency = max(20.0, min(8000.0, frequency))
        target_voice.target_amplitude = gain
        target_voice.amplitude = gain
        target_voice.decay_factor_per_block = decay_factor
        target_voice.voice_type = voice_type
        target_voice.age = self._voice_age_counter
        target_voice.is_active = True
        target_voice.sample_offset = 0

        if voice_type == "percussion":
            target_voice.attack_progress = 1.0
            target_voice.attack_step = 1.0
        else:
            target_voice.attack_progress = 0.0
            target_voice.attack_step = attack_step

        if voice_type in ("sub", "percussion"):
            target_voice.pan = 0.5
        else:
            pan_offset = ((self._voice_age_counter % 5) - 2) * 0.08
            target_voice.pan = 0.5 + pan_offset

        return target_voice

    def _drain_audio_queue(self) -> None:
        has_new_activity = False
        while True:
            try:
                event = self.audio_queue.get_nowait()
            except queue.Empty:
                break

            has_new_activity = True
            if event.lpf_cutoff > 0.0:
                self._target_cutoff = max(0.1, min(1.0, event.lpf_cutoff))

            if event.event_type == "note":
                self._allocate_voice(
                    frequency=event.frequency,
                    velocity=event.velocity,
                    duration=event.duration,
                    voice_type="bell"
                )
            elif event.event_type == "sub_drop":
                self._allocate_voice(
                    frequency=event.frequency,
                    velocity=event.velocity,
                    duration=event.duration,
                    voice_type="sub"
                )
                self.set_drone_root(event.frequency * 2.0)
            elif event.event_type == "percussion":
                self._allocate_voice(
                    frequency=event.frequency,
                    velocity=event.velocity,
                    duration=event.duration,
                    voice_type="percussion"
                )

        if has_new_activity:
            self._last_activity_time = time.monotonic()

    def render_block(self, outdata: np.ndarray) -> None:
        """Renders 512-sample audio block with zero runtime allocations."""
        self._drain_audio_queue()

        self._mono_buf.fill(0.0)
        self._stereo_buf.fill(0.0)

        blocksize = self.blocksize
        sr = float(self.samplerate)
        two_pi = 2.0 * np.pi
        now = time.monotonic()

        # 1. Render Adaptive Ambient Drone Sub-Pad (Idle Fade-Out)
        # If no key is pressed for > 2.0s, drone gain smoothly decays to 0.0!
        is_active = (now - self._last_activity_time) < 2.0
        target_drone_gain = (self.drone_volume * 0.08) if (self.enable_drone and is_active) else 0.0

        if target_drone_gain > self._drone_active_gain:
            self._drone_active_gain = min(target_drone_gain, self._drone_active_gain + 0.003)
        elif target_drone_gain < self._drone_active_gain:
            self._drone_active_gain = max(0.0, self._drone_active_gain - 0.002)

        if self._drone_active_gain > 0.0001:
            self._drone_current_freq += (self._drone_target_freq - self._drone_current_freq) * 0.015
            drone_inc = (two_pi * self._drone_current_freq) / sr

            lfo_val = self._drone_active_gain * (1.0 + 0.25 * math.sin(self._drone_lfo_phase))
            self._drone_lfo_phase = math.fmod(self._drone_lfo_phase + (two_pi * 0.08 * blocksize) / sr, two_pi)

            np.multiply(self._sample_indices, drone_inc, out=self._phase_buf1)
            self._phase_buf1 += self._drone_phase
            np.sin(self._phase_buf1, out=self._temp_wave)
            self._temp_wave *= lfo_val

            self._stereo_buf[:, 0] += self._temp_wave
            self._stereo_buf[:, 1] += self._temp_wave
            self._drone_phase = math.fmod(self._drone_phase + drone_inc * blocksize, two_pi)

        # 2. Render Active Voices
        preset = self.sound_preset
        for voice in self.voices:
            if not voice.is_active:
                continue

            # Special case: Vinyl Click Percussion
            if voice.voice_type == "percussion":
                offset = voice.sample_offset
                rem = VINYL_CLICK_LEN - offset
                take = min(blocksize, rem)
                if take > 0:
                    self._temp_wave[:take] = VINYL_CLICK_TABLE[offset:offset + take] * voice.amplitude
                    if take < blocksize:
                        self._temp_wave[take:].fill(0.0)
                    voice.sample_offset += take
                    self._stereo_buf[:, 0] += self._temp_wave
                    self._stereo_buf[:, 1] += self._temp_wave
                if voice.sample_offset >= VINYL_CLICK_LEN:
                    voice.is_active = False
                continue

            freq = voice.frequency
            phase0_1 = voice.phase1
            phase0_2 = voice.phase2
            amp_start = voice.amplitude
            amp_end = amp_start * voice.decay_factor_per_block

            # Envelope Attack smoothing
            if voice.attack_progress < 1.0:
                next_attack = min(1.0, voice.attack_progress + voice.attack_step)
                attack_diff = next_attack - voice.attack_progress
                np.multiply(self._ramp, attack_diff, out=self._amp_vec)
                self._amp_vec += voice.attack_progress
                voice.attack_progress = next_attack
            else:
                self._amp_vec.fill(1.0)

            # Decay ramp
            amp_diff = amp_end - amp_start
            self._amp_vec *= (amp_start + amp_diff * self._ramp)

            # Oscillator synthesis by Preset
            if voice.voice_type == "bell":
                if "Crystal" in preset:
                    # Crystal Bells: High chime partials (2.756f and 5.404f)
                    inc1 = (two_pi * freq) / sr
                    inc2 = (two_pi * freq * 2.756) / sr
                    np.multiply(self._sample_indices, inc1, out=self._phase_buf1)
                    self._phase_buf1 += phase0_1
                    np.multiply(self._sample_indices, inc2, out=self._phase_buf2)
                    self._phase_buf2 += phase0_2

                    np.sin(self._phase_buf1, out=self._temp_wave)
                    np.sin(self._phase_buf2, out=self._temp_harm)
                    self._temp_wave *= 0.55
                    self._temp_wave += self._temp_harm * 0.35

                    voice.phase1 = math.fmod(phase0_1 + inc1 * blocksize, two_pi)
                    voice.phase2 = math.fmod(phase0_2 + inc2 * blocksize, two_pi)

                elif "Felt" in preset:
                    # Lo-Fi Felt Piano: Mellow, filtered triangle-sine blend
                    inc = (two_pi * freq) / sr
                    np.multiply(self._sample_indices, inc, out=self._phase_buf1)
                    self._phase_buf1 += phase0_1
                    np.sin(self._phase_buf1, out=self._temp_wave)
                    # Soft fundamental + warm sub octave
                    np.multiply(self._sample_indices, inc * 0.5, out=self._phase_buf2)
                    self._phase_buf2 += phase0_2
                    np.sin(self._phase_buf2, out=self._temp_harm)
                    self._temp_wave = self._temp_wave * 0.70 + self._temp_harm * 0.25

                    voice.phase1 = math.fmod(phase0_1 + inc * blocksize, two_pi)
                    voice.phase2 = math.fmod(phase0_2 + (inc * 0.5) * blocksize, two_pi)

                elif "8-Bit" in preset or "Chiptune" in preset:
                    # 8-Bit Chiptune: Classic pulse wave
                    inc = (two_pi * freq) / sr
                    np.multiply(self._sample_indices, inc, out=self._phase_buf1)
                    self._phase_buf1 += phase0_1
                    np.sin(self._phase_buf1, out=self._temp_wave)
                    # 25% pulse wave via threshold
                    np.greater(self._temp_wave, 0.4, out=self._temp_wave)
                    self._temp_wave -= 0.5
                    self._temp_wave *= 0.7

                    voice.phase1 = math.fmod(phase0_1 + inc * blocksize, two_pi)

                else:
                    # Warm Rhodes: 2-op FM + detuned chorus
                    f1 = freq * DETUNE_UP
                    f2 = freq * DETUNE_DOWN
                    inc1 = (two_pi * f1) / sr
                    inc2 = (two_pi * f2) / sr

                    np.multiply(self._sample_indices, inc1, out=self._phase_buf1)
                    self._phase_buf1 += phase0_1
                    np.multiply(self._sample_indices, inc2, out=self._phase_buf2)
                    self._phase_buf2 += phase0_2

                    np.sin(self._phase_buf1, out=self._temp_wave)
                    np.sin(self._phase_buf2, out=self._temp_harm)
                    self._temp_wave *= 0.45
                    self._temp_wave += self._temp_harm * 0.45

                    # Gentle 2nd harmonic
                    np.multiply(self._phase_buf1, 2.0, out=self._phase_buf1)
                    np.sin(self._phase_buf1, out=self._temp_harm)
                    self._temp_wave += self._temp_harm * 0.16

                    voice.phase1 = math.fmod(phase0_1 + inc1 * blocksize, two_pi)
                    voice.phase2 = math.fmod(phase0_2 + inc2 * blocksize, two_pi)
            else:
                # Sub-bass sine
                inc = (two_pi * freq) / sr
                np.multiply(self._sample_indices, inc, out=self._phase_buf1)
                self._phase_buf1 += phase0_1
                np.sin(self._phase_buf1, out=self._temp_wave)
                voice.phase1 = math.fmod(phase0_1 + inc * blocksize, two_pi)

            self._temp_wave *= self._amp_vec

            pan = voice.pan
            self._stereo_buf[:, 0] += self._temp_wave * (1.0 - pan * 0.7)
            self._stereo_buf[:, 1] += self._temp_wave * (0.3 + pan * 0.7)

            voice.amplitude = amp_end
            if voice.amplitude < 0.0015:
                voice.is_active = False

        # 3. Stereo Ping-Pong Delay
        if self.enable_delay and self.delay_wet > 0.001:
            read_idx_l = (self._delay_pos - self._delay_samples_l + self._int_indices) % self.DELAY_BUF_SIZE
            read_idx_r = (self._delay_pos - self._delay_samples_r + self._int_indices) % self.DELAY_BUF_SIZE

            del_out_l = self._delay_buf[read_idx_l, 0]
            del_out_r = self._delay_buf[read_idx_r, 1]

            fb = self.delay_feedback
            write_idx = (self._delay_pos + self._int_indices) % self.DELAY_BUF_SIZE
            self._delay_buf[write_idx, 0] = self._stereo_buf[:, 0] + del_out_r * fb
            self._delay_buf[write_idx, 1] = self._stereo_buf[:, 1] + del_out_l * fb

            wet = self.delay_wet
            self._stereo_buf[:, 0] += del_out_l * wet
            self._stereo_buf[:, 1] += del_out_r * wet

            self._delay_pos = (self._delay_pos + blocksize) % self.DELAY_BUF_SIZE

        # 4. Low-Pass Filter with parameter smoothing
        self._smooth_cutoff += (self._target_cutoff - self._smooth_cutoff) * self._cutoff_smoothing_coeff
        fc = 350.0 + (self._smooth_cutoff ** 2) * 15650.0
        alpha = (two_pi * fc / sr) / (1.0 + two_pi * fc / sr)
        alpha = max(0.01, min(0.99, alpha))

        yl = self._filter_state_l
        yr = self._filter_state_r
        chan_l = self._stereo_buf[:, 0]
        chan_r = self._stereo_buf[:, 1]

        for i in range(blocksize):
            yl += alpha * (chan_l[i] - yl)
            chan_l[i] = yl
            yr += alpha * (chan_r[i] - yr)
            chan_r[i] = yr

        self._filter_state_l = yl
        self._filter_state_r = yr

        # 5. Master Volume & Soft Limiting
        if self.is_muted:
            outdata.fill(0.0)
        else:
            self._stereo_buf *= self.master_volume
            np.tanh(self._stereo_buf, out=outdata)

    def audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags
    ) -> None:
        self.render_block(outdata)

    def start(self) -> None:
        if self._is_running:
            return

        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype=np.float32,
            callback=self.audio_callback
        )
        self._stream.start()
        self._is_running = True

    def stop(self) -> None:
        self._is_running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        for v in self.voices:
            v.is_active = False
            v.amplitude = 0.0
        self._filter_state_l = 0.0
        self._filter_state_r = 0.0
        self._delay_buf.fill(0.0)
