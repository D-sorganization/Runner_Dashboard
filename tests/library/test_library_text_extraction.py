from library.text_extraction import extract_text_passages as _extract_text_passages
from library.text_extraction import parse_filename as _parse_filename


def test_text_passages_split_long_text_and_keep_line_ranges() -> None:
    raw = ("a" * 1500 + "\nshort\n").encode()

    passages = _extract_text_passages(raw, "txt")

    assert passages == [(None, 1, 1, "a" * 1500), (None, 2, 2, "short")]


def test_markdown_headings_label_following_passages() -> None:
    raw = b"# Intro\nfirst paragraph\n## Details\nsecond paragraph\n"

    passages = _extract_text_passages(raw, "md")

    assert passages == [("Intro", 1, 2, "# Intro\nfirst paragraph"), ("Details", 3, 4, "## Details\nsecond paragraph")]


def test_filename_parser_handles_author_and_year_bounds() -> None:
    assert _parse_filename(" Ada Lovelace - Computing 2001 ") == ("Ada Lovelace", "Computing 2001", 2001)
    assert _parse_filename("Untitled 1799") == (None, "Untitled 1799", None)
    assert _parse_filename("") == (None, None, None)
