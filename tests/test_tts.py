import shutil
import sys

import numpy as np
import pytest
import soundfile as sf

from historychannel.tts import TTSProvider, get_tts, normalize_for_tts, sentence_spans
from historychannel.tts.base import normalize_loudness, trim_silence
from historychannel.tts.text import ordinal_words, roman_to_int, year_to_words

# --------------------------------------------------------------------------- sentence splitting


def test_sentence_spans_basic():
    text = "The king hesitated. On 18 March, fighting began! Was it over? No."
    assert sentence_spans(text) == [
        "The king hesitated.", "On 18 March, fighting began!", "Was it over?", "No.",
    ]


def test_sentence_spans_abbreviations_and_initials():
    text = "St. Paul's Church met in c. 1848. J. S. Bach was long dead. Gen. Wrangel entered Berlin."
    assert sentence_spans(text) == [
        "St. Paul's Church met in c. 1848.", "J. S. Bach was long dead.", "Gen. Wrangel entered Berlin.",
    ]


def test_sentence_spans_quotes_paragraphs_and_whitespace():
    text = '"To my dear Berliners," he wrote.  "Return to peace!" The crowd\nlistened.\n\nThen silence'
    assert sentence_spans(text) == [
        '"To my dear Berliners," he wrote.', '"Return to peace!"', "The crowd listened.", "Then silence",
    ]


def test_sentence_spans_lowercase_after_question_does_not_split():
    assert sentence_spans('"Why?" he asked.') == ['"Why?" he asked.']


def test_sentence_spans_empty():
    assert sentence_spans("  \n\n ") == []


# --------------------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    "year,words",
    [(1848, "eighteen forty-eight"), (1900, "nineteen hundred"), (1905, "nineteen oh-five"),
     (2003, "two thousand and three"), (2012, "twenty twelve"), (1815, "eighteen fifteen")],
)
def test_year_to_words(year, words):
    assert year_to_words(year) == words


def test_ordinals_and_roman():
    assert [ordinal_words(n) for n in (1, 2, 3, 4, 12, 18, 20, 21, 31)] == [
        "first", "second", "third", "fourth", "twelfth", "eighteenth", "twentieth",
        "twenty-first", "thirty-first",
    ]
    assert [roman_to_int(r) for r in ("I", "IV", "IX", "XIV", "XVIII")] == [1, 4, 9, 14, 18]


@pytest.mark.parametrize(
    "src,expected",
    [
        ("Friedrich Wilhelm IV hesitated.", "Friedrich Wilhelm the Fourth hesitated."),
        ("Napoleon III and Pius IX.", "Napoleon the Third and Pius the Ninth."),
        ("Wilhelm I was crowned.", "Wilhelm the First was crowned."),
        ("Then I went home.", "Then I went home."),  # pronoun untouched
        ("In World War II.", "In World War Two."),
        ("On 18 March 1848, Berlin rose.", "On the eighteenth of March eighteen forty-eight, Berlin rose."),
        ("March 18, 1848", "March eighteenth, eighteen forty-eight"),
        ("The 1840s were hungry.", "The eighteen forties were hungry."),
        ("The revolutions of 1848–49.", "The revolutions of eighteen forty-eight to forty-nine."),
        ("From 1848-1849.", "From eighteen forty-eight to eighteen forty-nine."),
        ("St. Paul's Church, c. 1850.", "Saint Paul's Church, circa eighteen fifty."),
        ("Gen. Wrangel, e.g. here.", "General Wrangel, for example, here."),
        ("1848 soldiers died.", "1848 soldiers died."),  # a count, not a year
        ("He paid 3,000 thalers.", "He paid 3,000 thalers."),
        ("the 19th century", "the 19th century"),  # espeak handles ordinals fine
    ],
)
def test_normalize_for_tts(src, expected):
    assert normalize_for_tts(src) == expected


def test_normalize_leaves_acronyms_alone():
    # Only I/V/X numerals are treated as Roman; MIX/CD/LCD etc. never match.
    assert normalize_for_tts("The DC office and the MIX tape.") == "The DC office and the MIX tape."


# --------------------------------------------------------------------------- pipeline with a fake provider


class _ToneTTS(TTSProvider):
    """Deterministic provider: 0.1 s per word of tone, padded with silence."""

    name = "tone"
    sample_rate = 24000

    def __init__(self):
        self.calls = []

    def _render_sentence(self, text, voice, speed, previous_text=None, next_text=None):
        self.calls.append((text, previous_text, next_text))
        n = int(0.1 * len(text.split()) * self.sample_rate)
        t = np.arange(n) / self.sample_rate
        tone = 0.05 * np.sin(2 * np.pi * 220 * t).astype(np.float32)
        pad = np.zeros(int(0.3 * self.sample_rate), np.float32)
        return np.concatenate([pad, tone, pad])


def test_synthesize_with_timings_fake_provider(tmp_path):
    tts = _ToneTTS()
    text = "Friedrich Wilhelm IV hesitated. The crowd waited in the square.\n\nThen shots rang out."
    res, timings = tts.synthesize_with_timings(text, tmp_path / "out.wav")

    # Subtitles keep the text as written, the engine got the normalized text.
    assert [s for s, _, _ in timings] == [
        "Friedrich Wilhelm IV hesitated.", "The crowd waited in the square.", "Then shots rang out.",
    ]
    assert tts.calls[0][0] == "Friedrich Wilhelm the Fourth hesitated."
    assert tts.calls[1][1] == tts.calls[0][0] and tts.calls[1][2] == tts.calls[2][0]

    # Silence padding trimmed, pauses inserted (0.25 s sentence, longer at paragraph).
    (s0, a0, b0), (_, a1, b1), (_, a2, b2) = timings
    assert a0 == pytest.approx(0.05 + 0.02, abs=0.03)
    assert b0 - a0 == pytest.approx(0.5 + 0.04, abs=0.05)  # 5 normalized words
    assert a1 - b0 == pytest.approx(0.25 + 0.04, abs=0.05)
    assert a2 - b1 > a1 - b0 + 0.2
    assert res.duration == pytest.approx(b2 + 0.05 + 0.02, abs=0.05)

    audio, sr = sf.read(res.path)
    assert sr == 24000 and audio.ndim == 1
    assert len(audio) / sr == pytest.approx(res.duration, abs=1e-3)
    assert np.abs(audio).max() <= 10 ** (-1 / 20) + 1e-3


def test_loudness_normalization_matches_scenes():
    sr = 24000
    t = np.arange(sr) / sr
    quiet = (0.02 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    loud = (0.6 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    rq = np.sqrt(np.mean(normalize_loudness(quiet, sr) ** 2))
    rl = np.sqrt(np.mean(normalize_loudness(loud, sr) ** 2))
    assert 20 * np.log10(rq / rl) == pytest.approx(0, abs=0.5)


def test_trim_silence():
    sr = 24000
    sig = np.concatenate([np.zeros(sr), 0.5 * np.ones(sr // 2), np.zeros(sr)]).astype(np.float32)
    out = trim_silence(sig, sr, keep=0.05)
    assert len(out) / sr == pytest.approx(0.6, abs=0.03)


def test_empty_text_raises(tmp_path):
    with pytest.raises(ValueError):
        _ToneTTS().synthesize("  ...  ", tmp_path / "x.wav")


# --------------------------------------------------------------------------- real engines


@pytest.mark.slow
@pytest.mark.network
def test_kokoro_synthesizes_sentence(tmp_path):
    tts = get_tts("kokoro")
    res, timings = tts.synthesize_with_timings(
        "On 18 March 1848, barricades rose across Berlin. Friedrich Wilhelm IV hesitated.",
        tmp_path / "k.wav",
    )
    assert res.sample_rate == 24000
    assert res.duration > 1.0
    assert len(timings) == 2 and timings[0][2] <= timings[1][1] < timings[1][2] <= res.duration
    audio, _ = sf.read(res.path)
    assert np.abs(audio).max() > 0.1  # not silent


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("ffmpeg"), reason="needs macOS say + ffmpeg")
def test_say_fallback(tmp_path):
    res = get_tts("say").synthesize("Berlin, eighteen forty-eight.", tmp_path / "s.wav")
    assert res.duration > 0.5
