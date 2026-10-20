"""Source-bound equipment extraction and revision comparison.

This package reports observed document facts. It does not certify engineering
correctness or authorize a manufacturing release.
"""

from .comparison import analyze_package, compare_packages
from .schemas import SourceInput

__all__ = ["SourceInput", "analyze_package", "compare_packages"]
