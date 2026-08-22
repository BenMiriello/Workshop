"""A stored attachment whose name is not latin-1 must still be servable.

HTTP header values are latin-1. Both attachment routes interpolated the stored
name straight into Content-Disposition, so a non-latin-1 character raised
UnicodeEncodeError inside Starlette -- after the handler returned, which is why
the route answered 500 with nothing in the handler to blame.

macOS puts U+202F (a narrow no-break space) before AM/PM in every screenshot
name, so this was not exotic: measured 2026-08-22, 4 of the 33 stored
attachments could not be opened, and check_routes_answer had been reporting both
routes as 5xx since 2026-08-17 -- which, because that gate is repo-wide, was
rejecting every backend ticket the unattended loop attempted.
"""
from __future__ import annotations

import pytest

from routes.chat import _content_disposition

MACOS_SCREENSHOT = "Screenshot 2026-08-20 at 5.00.28 PM.png"


@pytest.mark.parametrize("name", [
    MACOS_SCREENSHOT,
    "plain.png",
    "你好.pdf",
    'quote"and\\back.txt',
    " ",
    "",
])
def test_the_header_is_always_latin_1_encodable(name):
    """The regression itself: this is the encode that used to raise."""
    _content_disposition("inline", name).encode("latin-1")


def test_the_real_name_survives_percent_encoded():
    """The ASCII fallback is lossy by definition, so the true name has to ride
    in filename*, which is what a current browser actually reads."""
    got = _content_disposition("inline", MACOS_SCREENSHOT)
    assert "filename*=UTF-8''" in got
    assert "%E2%80%AF" in got, "U+202F must be preserved in the encoded name"


def test_the_ascii_fallback_stays_readable():
    """NFKD folds U+202F to a plain space instead of dropping it, so the
    fallback is still the file's name rather than a mangled one."""
    got = _content_disposition("inline", MACOS_SCREENSHOT)
    assert 'filename="Screenshot 2026-08-20 at 5.00.28 PM.png"' in got


def test_the_fallback_cannot_break_out_of_the_quoted_string():
    """A quote or backslash in a name would otherwise terminate the
    quoted-string early and let the rest of the name become header syntax."""
    got = _content_disposition("inline", 'evil".txt')
    head = got.split("; filename*=")[0]
    assert head.count('"') == 2, head


def test_an_unrepresentable_name_still_yields_a_usable_fallback():
    """A name with no ASCII at all must not produce an empty filename="" ."""
    assert 'filename="download"' in _content_disposition("inline", " ")
    assert 'filename="download"' in _content_disposition("inline", "")


def test_the_disposition_kind_is_preserved():
    """The preview route serves inline, the /original route serves attachment;
    collapsing them would turn a preview into a download."""
    assert _content_disposition("inline", "a.png").startswith("inline;")
    assert _content_disposition("attachment", "a.png").startswith("attachment;")
