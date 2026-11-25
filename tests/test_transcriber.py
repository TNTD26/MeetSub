from __future__ import annotations

from meetsub.transcriber import clean_transcript


def test_clean_trims_and_collapses_duplicate_words():
    assert clean_transcript("  hello   hello  world ") == "hello world"


def test_clean_drops_common_youtube_hallucinations():
    assert clean_transcript("Thanks for watching!") == ""
    assert clean_transcript("Please subscribe to my channel.") == ""
    assert clean_transcript("♪ ♪") == ""


def test_clean_keeps_real_meeting_speech():
    text = "Thank you. Can everyone hear me?"
    assert clean_transcript(text) == text
