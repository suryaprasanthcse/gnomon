"""Rule-body macros (master §0.3). Expansion runs before hashing: the expanded text is what a rule's sha pins.

KCTX     → `h, causes, nseq, now, now_lc`, the kernel context every body carries.
LIVE(f)  → `(f.te = $INF AND f.vf <= now AND now < f.vt)`, a fact that is current and valid now.
"""

from __future__ import annotations

import re

KCTX = "h, causes, nseq, now, now_lc"
_KCTX = re.compile(r"(?<![\w.$'\"])KCTX(?!\w)")
_LIVE = re.compile(r"(?<![\w.$'\"])LIVE\(\s*([A-Za-z_]\w*)\s*\)")


def live(var: str) -> str:
    return f"({var}.te = $INF AND {var}.vf <= now AND now < {var}.vt)"


def expand(text: str) -> str:
    text = _KCTX.sub(KCTX, text)
    return _LIVE.sub(lambda m: live(m.group(1)), text)
