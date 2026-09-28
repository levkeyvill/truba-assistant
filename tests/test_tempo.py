"""Темп речи у Higgs: растяжение времени без изменения высоты.

Проверяем на синусах и шуме — без видеокарты, без модели и без звука: важен
тон, длина и отсутствие щелчков на стыках кусков.
"""

import unittest

import numpy as np

from core.tempo import Stretcher, stretch

RATE = 24_000


def tone(hz: float, seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def main_hz(wave: np.ndarray) -> float:
    """Основная частота по FFT — тон после растяжения должен остаться прежним."""
    spectrum = np.abs(np.fft.rfft(wave * np.hanning(len(wave))))
    return float(np.fft.rfftfreq(len(wave), 1.0 / RATE)[np.argmax(spectrum)])


def speechish(seconds: float = 2.0, seed: int = 7) -> np.ndarray:
    """Смесь синусов и шума — на стыках окон это видно лучше, чем на синусе."""
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    return (0.5 * np.sin(2 * np.pi * 200 * t)
            + 0.2 * np.sin(2 * np.pi * 431 * t)
            + 0.05 * rng.standard_normal(n)).astype(np.float32)


def streamed(wave: np.ndarray, speed: float, piece: int) -> np.ndarray:
    """Тот же звук, но кусками по piece отсчётов — как отдаёт Higgs."""
    stretcher = Stretcher(RATE, speed)
    parts = [stretcher.push(wave[at:at + piece])
             for at in range(0, len(wave), piece)]
    parts.append(stretcher.flush())
    ready = [p for p in parts if p.size]
    return np.concatenate(ready) if ready else np.zeros(0, dtype=np.float32)


class LengthAndPitchTests(unittest.TestCase):
    def test_faster_is_shorter_and_keeps_pitch(self):
        wave = tone(200.0, 2.0)
        got = stretch(wave, RATE, 1.25)
        self.assertAlmostEqual(len(got) / RATE, 2.0 / 1.25, delta=2.0 / 1.25 * 0.03)
        self.assertAlmostEqual(main_hz(got), 200.0, delta=3.0)

    def test_slower_is_longer_and_keeps_pitch(self):
        wave = tone(200.0, 2.0)
        got = stretch(wave, RATE, 0.8)
        self.assertAlmostEqual(len(got) / RATE, 2.0 / 0.8, delta=2.0 / 0.8 * 0.03)
        self.assertAlmostEqual(main_hz(got), 200.0, delta=3.0)

    def test_unit_speed_returns_the_same_array(self):
        wave = tone(200.0, 0.5)
        self.assertIs(stretch(wave, RATE, 1.0), wave)
        # Вокруг единицы тоже не трогаем: ползунок даёт шаг 0.05, и растягивать
        # на 1% незачем — только лишний хрип на стыках окон.
        self.assertIs(stretch(wave, RATE, 1.01), wave)
        self.assertIs(stretch(wave, RATE, 0.99), wave)

    def test_whole_range_works_and_keeps_pitch(self):
        wave = tone(200.0, 1.0)
        for speed in (0.5, 0.75, 1.25, 1.5, 2.0):
            with self.subTest(speed=speed):
                got = stretch(wave, RATE, speed)
                self.assertAlmostEqual(
                    len(got) / RATE, 1.0 / speed, delta=1.0 / speed * 0.03
                )
                self.assertAlmostEqual(main_hz(got), 200.0, delta=3.0)

    def test_silly_speed_is_clamped_to_the_slider_range(self):
        wave = tone(200.0, 1.0)
        self.assertEqual(len(stretch(wave, RATE, 9.0)),
                         len(stretch(wave, RATE, 2.0)))
        self.assertEqual(len(stretch(wave, RATE, 0.1)),
                         len(stretch(wave, RATE, 0.5)))
        # Мусор на входе не должен ронять синтез посреди разговора.
        self.assertIs(stretch(wave, RATE, None), wave)
        self.assertIs(stretch(wave, RATE, "быстрее"), wave)

    def test_empty_signal_survives(self):
        empty = np.zeros(0, dtype=np.float32)
        self.assertEqual(len(stretch(empty, RATE, 1.4)), 0)


class StreamTests(unittest.TestCase):
    def test_stream_matches_whole_signal(self):
        wave = speechish()
        for speed in (1.25, 0.8, 1.6, 0.6):
            with self.subTest(speed=speed):
                whole = stretch(wave, RATE, speed)
                got = streamed(wave, speed, piece=int(RATE * 0.3))
                self.assertAlmostEqual(len(got) / len(whole), 1.0, delta=0.02)

    def test_stream_has_no_jumps_at_chunk_seams(self):
        # Куски от Higgs идут по несколько сотен мс; если бы состояние между
        # ними терялось, на стыке был бы щелчок или провал.
        wave = speechish()
        got = streamed(wave, 1.3, piece=int(RATE * 0.3))
        before = float(np.abs(np.diff(wave)).max())
        after = float(np.abs(np.diff(got)).max())
        self.assertLessEqual(after, before * 1.5)

    def test_tiny_chunks_do_not_break_the_stream(self):
        # Пустой кусок и кусок короче окна (10 мс) — не поломка, а просто
        # слишком мало данных, чтобы что-то склеить.
        wave = speechish(0.5)
        stretcher = Stretcher(RATE, 1.4)
        parts = [stretcher.push(np.zeros(0, dtype=np.float32)),
                 stretcher.push(wave[:240]),
                 stretcher.push(wave[240:]),
                 stretcher.flush()]
        ready = [p for p in parts if p.size]
        got = np.concatenate(ready) if ready else np.zeros(0, dtype=np.float32)
        self.assertTrue(got.size)
        self.assertAlmostEqual(len(got) / RATE, 0.5 / 1.4, delta=0.5 / 1.4 * 0.05)
        self.assertLessEqual(float(np.abs(np.diff(got)).max()),
                             float(np.abs(np.diff(wave)).max()) * 1.5)

    def test_speech_of_ten_milliseconds_alone_does_not_crash(self):
        stretcher = Stretcher(RATE, 1.5)
        self.assertEqual(len(stretcher.push(np.zeros(10, dtype=np.float32))), 0)
        self.assertTrue(stretcher.flush().size)

    def test_nothing_after_flush(self):
        stretcher = Stretcher(RATE, 1.2)
        stretcher.push(speechish(0.3))
        stretcher.flush()
        self.assertEqual(len(stretcher.push(speechish(0.3))), 0)
        self.assertEqual(len(stretcher.flush()), 0)

    def test_neutral_stream_passes_chunks_through(self):
        wave = speechish(0.3)
        stretcher = Stretcher(RATE, 1.0)
        self.assertIs(stretcher.push(wave), wave)
        self.assertEqual(len(stretcher.flush()), 0)


class HiggsTextTests(unittest.TestCase):
    def test_spoken_has_no_speed_markup(self):
        # 26 сентября: при speed 1.2 хозяин услышал «как будто ничего не
        # поменялось». Метка `<|prosody:speed_fast|>` тут ни при чём — темп
        # теперь задаёт растяжение звука, и в текст метка не попадает.
        from core.higgs_voice import HiggsVoice

        for speed in (0.5, 0.8, 1.2, 2.0):
            with self.subTest(speed=speed):
                spoken = HiggsVoice._spoken("Восемь офисов и двенадцать.", speed)
                self.assertNotIn("prosody", spoken)
                self.assertNotIn("speed", spoken)
                self.assertTrue(spoken.endswith("офисов и двенадцать."))
        self.assertEqual(HiggsVoice._spoken("   ", 1.2), "")


if __name__ == "__main__":
    unittest.main()
