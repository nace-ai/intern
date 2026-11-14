"""Read-only results hub. Numbers come from recorded run and metric files."""

from billproof.results.cases import get_case, list_cases
from billproof.results.catalog import build_summary, load_summary, render_markdown

__all__ = ["build_summary", "get_case", "list_cases", "load_summary", "render_markdown"]
