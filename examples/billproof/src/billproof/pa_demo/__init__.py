"""Full-chain prior-authorization demo .

Showcase cases from criteria-gym validation.parquet -> browser run finds the
real policy document -> NDI boxes each criterion on the policy page and each
record in the synthetic packet -> Drex Search links criterion to record ->
Drex + code decide -> letter. Everything is recorded under data/pa_demo/<slug>
so the stage replays offline (server/pa_routes.py serves it).
"""

from billproof.pa_demo.showcase import SHOWCASE, PA_DEMO_DIR, load_showcase, load_row

__all__ = ["SHOWCASE", "PA_DEMO_DIR", "load_showcase", "load_row"]
