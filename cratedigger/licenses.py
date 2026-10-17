"""Turn license URLs into short labels like "CC BY-NC-SA 3.0"."""

from __future__ import annotations

import re

_CC_LICENSE = re.compile(r"/licenses/([a-z+\-]+)(?:/(\d+(?:\.\d+)?))?", re.IGNORECASE)


def license_label(url: str | None) -> str:
    if not url:
        return ""
    u = url.strip().lower()
    if "creativecommons.org" in u:
        if "/publicdomain/zero" in u:
            return "CC0"
        if "/publicdomain" in u:
            return "Public domain"
        m = _CC_LICENSE.search(u)
        if m:
            code = m.group(1).upper()
            return f"CC {code} {m.group(2)}" if m.group(2) else f"CC {code}"
        return "Creative Commons"
    if "artlibre.org" in u:
        return "Free Art License"
    return "See source page"


def is_open_license(url: str | None) -> bool:
    """True for Creative Commons, public-domain and Free Art License URLs."""
    if not url:
        return False
    u = url.lower()
    return "creativecommons.org" in u or "artlibre.org" in u
