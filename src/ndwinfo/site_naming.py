"""Roadway information encoded in NDW measurement-site names.

RWS site names carry the carriageway they sit on: ``0091hrl0337ra`` is the
*hoofdrijbaan links* at km 33.7, ``009vwa042571`` the *verbindingsweg a*. That
is the same distinction OSM makes with ``carriageway_ref`` (``Li``/``Re`` for
the main carriageways, a letter per slip road), so the two can be compared —
which is what lets the drive HUD keep to the roadway it is actually on when a
site has no confident OSM match.
"""

import re

# Anchored between non-letters so the code is the site-name token it looks like
# (``0091hrl0337ra``, ``001-HRL-Amersfoort``) and not a fragment of a word.
NDW_ROADWAY_CODE_RE = re.compile(r"(?<![A-Za-z])(?:hr([lr])|vw([a-z]))(?![A-Za-z])", re.I)


def ndw_roadway_ref(name: str | None) -> str | None:
    """Return the roadway a site name encodes, in OSM's ``carriageway_ref`` terms.

    ``Li``/``Re`` for the main carriageways, a single lowercase letter for a
    slip road, ``None`` when the name carries no such code (regional operators
    name their sites in free text).
    """
    if not name:
        return None
    match = NDW_ROADWAY_CODE_RE.search(name)
    if match is None:
        return None
    main, link = match.groups()
    if main is not None:
        return "Li" if main.lower() == "l" else "Re"
    return link.lower()
