"""Photo shrink: decode, scale, encode, keep EXIF."""

from .shrink import Options, Result, ShrinkPlan, build_pairs, output_name, parse_bg

__all__ = ["Options", "Result", "ShrinkPlan", "build_pairs", "output_name", "parse_bg"]
