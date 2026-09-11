"""What every source-specific mapper returns.

One shape, so `monitor/fetch.py` needs no special case per source and a fourth
connector does not bring a fourth copy of the same three fields.

`title_en` and `body_en` are the English rendering *where the source supplies one*.
TED translates its titles into all 24 EU languages, so it fills `title_en` for
free. Prozorro and Find a Tender do not: Prozorro publishes Ukrainian only and
leaves both empty for step 14 to fill, and Find a Tender publishes English, where
the original already is the English and duplicating it would be noise.

Empty means no English was published, never that the original was English.
"""

from __future__ import annotations

from dataclasses import dataclass

from monitor.models import Notice


@dataclass(frozen=True)
class MappedNotice:
    notice: Notice
    title_en: str = ""
    body_en: str = ""
