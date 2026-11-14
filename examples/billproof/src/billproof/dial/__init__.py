"""Trust dial: Drex keeps answers at or above a confidence threshold, GPT takes the rest.

Simulated from recorded answers only. No model calls. See `simulate.py` for the
cost and latency formulas and `build.py` for the precomputed curves.
"""

from billproof.dial.simulate import BENCHES, curve_document, point_at

__all__ = ["BENCHES", "curve_document", "point_at"]
