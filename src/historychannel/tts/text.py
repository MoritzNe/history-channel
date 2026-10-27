"""Sentence splitting and TTS text normalization.

Subtitles show the *original* sentence; the TTS engine gets the *normalized* one.
So callers split first (`sentence_spans`) and normalize each sentence after
(`normalize_for_tts`).

The normalizer is deliberately small and conservative. It targets what Kokoro's
espeak front end gets wrong for history narration:

* years:     "1848" -> "eighteen forty-eight"  (espeak says "one thousand eight hundred ...")
* decades:   "1840s" -> "eighteen-forties"
* ranges:    "1848–49" -> "eighteen forty-eight to forty-nine"
* dates:     "18 March" -> "the eighteenth of March", "March 18" -> "March eighteenth"
* regnal numerals: "Friedrich Wilhelm IV" -> "Friedrich Wilhelm the Fourth"  (espeak says "roman four")
* "World War II" -> "World War Two"
* abbreviations: St., Dr., Gen., c./ca., e.g., i.e., etc., ...
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- numbers

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ORD_IRREGULAR = {
    "one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
    "nine": "ninth", "twelve": "twelfth",
}


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def number_to_words(n: int) -> str:
    """Cardinal words for 0 <= n < 10000 (enough for dates and regnal numbers)."""
    if n < 100:
        return _two_digits(n)
    if n < 1000:
        h, rest = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" and {_two_digits(rest)}" if rest else "")
    th, rest = divmod(n, 1000)
    out = f"{_ONES[th]} thousand"
    if rest:
        out += (" and " if rest < 100 else " ") + number_to_words(rest)
    return out


def ordinal_words(n: int) -> str:
    words = number_to_words(n)
    head, sep, last = words.rpartition("-") if "-" in words else words.rpartition(" ")
    if last in _ORD_IRREGULAR:
        last = _ORD_IRREGULAR[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return f"{head}{sep}{last}"


def year_to_words(y: int) -> str:
    """How a narrator reads a year: 1848 -> "eighteen forty-eight", 1905 -> "nineteen oh-five"."""
    if 2000 <= y <= 2009:
        return number_to_words(y)
    hi, lo = divmod(y, 100)
    if lo == 0:
        return f"{_two_digits(hi)} hundred"
    if lo < 10:
        return f"{_two_digits(hi)} oh-{_ONES[lo]}"
    return f"{_two_digits(hi)} {_two_digits(lo)}"


def _decade_words(y: int) -> str:
    """1840 -> "eighteen-forties", 1900 -> "nineteen-hundreds"."""
    hi, lo = divmod(y, 100)
    if lo == 0:
        return f"{_two_digits(hi)} hundreds"
    tens = _TENS[lo // 10]
    return f"{_two_digits(hi)} {tens[:-1]}ies"


# --------------------------------------------------------------------------- roman numerals

_ROMAN_RE = r"(?:X{0,3})(?:IX|IV|V?I{0,3})"
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10}

# Single-letter numerals ("I", "V") collide with the pronoun and initials, so they
# only convert after a known regnal name. Longer numerals convert after any
# capitalized word.
_REGNAL_NAMES = {
    "Alexander", "Alfonso", "Charles", "Christian", "Constantine", "Edward", "Elizabeth",
    "Ferdinand", "Francis", "Franz", "Frederick", "Friedrich", "George", "Gregory", "Gustav",
    "Henry", "Heinrich", "Isabella", "James", "John", "Joseph", "Karl", "Leo", "Leopold",
    "Louis", "Ludwig", "Maximilian", "Napoleon", "Nicholas", "Otto", "Paul", "Pedro", "Peter",
    "Philip", "Pius", "Richard", "Umberto", "Victor", "Victoria", "Wilhelm", "William",
}


def roman_to_int(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN_VALUES[ch]
        nxt = _ROMAN_VALUES[s[i + 1]] if i + 1 < len(s) else 0
        total += -v if v < nxt else v
    return total


_WAR_RE = re.compile(rf"\b(World War) ({_ROMAN_RE})\b")
_REGNAL_RE = re.compile(rf"\b([A-Z][a-zà-ÿ]+(?:-[A-Z][a-zà-ÿ]+)?) ({_ROMAN_RE})\b(?!['’]?[a-z])")


def _regnal_sub(m: re.Match) -> str:
    name, numeral = m.group(1), m.group(2)
    if not numeral:
        return m.group(0)
    if len(numeral) == 1 and name not in _REGNAL_NAMES:
        return m.group(0)
    return f"{name} the {ordinal_words(roman_to_int(numeral)).capitalize()}"


# --------------------------------------------------------------------------- dates / years

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_COUNT_NOUNS = (
    "people|men|women|soldiers|troops|casualties|dead|deaths|killed|wounded|workers|votes|"
    "members|deputies|delegates|ships|guns|cannons|horses|miles|kilometres|kilometers|km|"
    "metres|meters|thalers|florins|guilders|francs|marks|pounds|dollars|copies|signatures"
)
_DASH = r"[–—-]"

_DAY_MONTH_RE = re.compile(rf"\b([0-3]?\d)(?:st|nd|rd|th)? ({_MONTHS})\b")
_MONTH_DAY_RE = re.compile(rf"\b({_MONTHS}) ([0-3]?\d)(?:st|nd|rd|th)?\b(?!\d)")
_DECADE_RE = re.compile(r"\b(1[0-9]|20)([0-9])0['’]?s\b")
_YEAR_RANGE_RE = re.compile(rf"\b(1[0-9]\d\d|20\d\d){_DASH}(\d\d(?:\d\d)?)\b")
_YEAR_RE = re.compile(rf"(?<![\d,.$£€])\b(1[0-9]\d\d|20\d\d)\b(?![,.]\d)(?! (?:{_COUNT_NOUNS})\b)")

_ABBREVIATIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bSt\.(?= [A-Z])"), "Saint"),
    (re.compile(r"\bDr\.(?= )"), "Doctor"),
    (re.compile(r"\bMr\.(?= )"), "Mister"),
    (re.compile(r"\bMrs\.(?= )"), "Missus"),
    (re.compile(r"\bGen\.(?= [A-Z])"), "General"),
    (re.compile(r"\bCol\.(?= [A-Z])"), "Colonel"),
    (re.compile(r"\bLt\.(?= [A-Z])"), "Lieutenant"),
    (re.compile(r"\bCapt\.(?= [A-Z])"), "Captain"),
    (re.compile(r"\bProf\.(?= [A-Z])"), "Professor"),
    (re.compile(r"\b(?:c|ca)\.\s?(?=\d)"), "circa "),
    (re.compile(r"\be\.g\.,?"), "for example,"),
    (re.compile(r"\bi\.e\.,?"), "that is,"),
    (re.compile(r"\betc\."), "et cetera."),
    (re.compile(r"\bvs\.?(?= )"), "versus"),
    (re.compile(r"\bNo\.(?= ?\d)"), "number"),
    (re.compile(r"&"), " and "),
    (re.compile(r"%"), " percent"),
]


def _year_range_sub(m: re.Match) -> str:
    start, end = m.group(1), m.group(2)
    end_full = int(start[:2] + end) if len(end) == 2 else int(end)
    end_words = _two_digits(int(end)) if len(end) == 2 else year_to_words(end_full)
    return f"{year_to_words(int(start))} to {end_words}"


def normalize_for_tts(text: str) -> str:
    """Rewrite a sentence so the TTS engine reads it like a narrator would."""
    t = text
    for pat, rep in _ABBREVIATIONS:
        t = pat.sub(rep, t)
    t = _WAR_RE.sub(lambda m: f"{m.group(1)} {number_to_words(roman_to_int(m.group(2))).capitalize()}"
                    if m.group(2) else m.group(0), t)
    t = _REGNAL_RE.sub(_regnal_sub, t)
    t = _DAY_MONTH_RE.sub(lambda m: f"the {ordinal_words(int(m.group(1)))} of {m.group(2)}"
                          if 1 <= int(m.group(1)) <= 31 else m.group(0), t)
    t = _MONTH_DAY_RE.sub(lambda m: f"{m.group(1)} {ordinal_words(int(m.group(2)))}"
                          if 1 <= int(m.group(2)) <= 31 else m.group(0), t)
    t = _DECADE_RE.sub(lambda m: _decade_words(int(m.group(1) + m.group(2) + "0")), t)
    t = _YEAR_RANGE_RE.sub(_year_range_sub, t)
    t = _YEAR_RE.sub(lambda m: year_to_words(int(m.group(1))), t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t


# --------------------------------------------------------------------------- sentences

# Abbreviations whose trailing period does not end a sentence.
_NO_BREAK = {
    "st", "dr", "mr", "mrs", "ms", "gen", "col", "lt", "capt", "prof", "rev", "sgt", "maj",
    "c", "ca", "cf", "e.g", "i.e", "vs", "no", "vol", "pp", "jr", "sr", "fig", "approx",
}
_BOUNDARY_RE = re.compile(r"([.!?…]+[\"'”’)\]]*)(\s+)(?=[\"'“‘(\[]?[A-Z0-9])")


def sentence_spans(text: str) -> list[str]:
    """Split narration into sentences (used for TTS chunking and subtitle cues).

    Splits on . ! ? … followed by whitespace and an uppercase letter/digit, but not
    after common abbreviations ("St. Paul", "c. 1848") or single initials ("J. S. Bach").
    Paragraph breaks always split.
    """
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        start = 0
        for m in _BOUNDARY_RE.finditer(para):
            end = m.end(1)
            if m.group(1).startswith(".") and len(m.group(1).rstrip("\"'”’)]")) == 1:
                word = para[start:m.start(1)].rsplit(" ", 1)[-1].lstrip("(\"'“‘").lower()
                if word in _NO_BREAK or (len(word) == 1 and word.isalpha()):
                    continue
            sent = para[start:end].strip()
            if sent:
                out.append(sent)
            start = m.end()
        tail = para[start:].strip()
        if tail:
            out.append(tail)
    return out
