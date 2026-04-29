"""Import shim for the vendored deterministic pricing engine.

The `surplusAS-pricing-intel` repository is consumed as a git submodule under
`vendor/surplusas-pricing/`. It does not yet ship a `pyproject.toml`, so we
place its root on `sys.path` here rather than installing it as a package.

When the engine is extracted into a published `surplusas-pricing` pip package
(post-contest, per the implementation plan), this module collapses to a plain
re-export.
"""

from __future__ import annotations

import sys
from pathlib import Path

_VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor" / "surplusas-pricing"
if _VENDOR_ROOT.is_dir() and str(_VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_VENDOR_ROOT))

# Re-exports — import lazily so this module imports even if the submodule is
# uninitialised in a fresh checkout that skipped `git submodule update --init`.
try:
    from pricing_engine.anchors import lookup_anchor
    from pricing_engine.coefficients import load_latest
    from pricing_engine.formula import recommend
    from pricing_engine.schemas import (
        FORMULA_VERSION,
        AppliedPressures,
        Coefficients,
        PricingInput,
        Recommendation,
    )

    __all__ = [
        "FORMULA_VERSION",
        "AppliedPressures",
        "Coefficients",
        "PricingInput",
        "Recommendation",
        "load_latest",
        "lookup_anchor",
        "recommend",
    ]
except ImportError:
    # Submodule not initialised — surface a clearer error at use time, not import time.
    __all__ = []
