"""Precursor-family taxonomy (plan section 4, "Suggested initial taxonomy").

Refine the labels/descriptions here after Builder A inspects the real ASRS export.
These are triage categories, not ASRS ground-truth labels.
"""

PATTERN_FAMILIES: dict[str, str] = {
    "communication_coordination": (
        "A communication or coordination breakdown between pilots, controllers or crew members: "
        "call-sign confusion, readback/hearback errors, misunderstood or ambiguous clearances, "
        "frequency congestion, or crew members not sharing information."
    ),
    "runway_surface": (
        "Runway or taxiway surface operations: runway incursions, crossing a hold-short line, "
        "wrong runway or taxiway, ground-movement conflicts between aircraft or vehicles."
    ),
    "procedural_deviation": (
        "A deviation from a required procedure, checklist, standard operating procedure or clearance "
        "that is not primarily explained by miscommunication or surface navigation."
    ),
    "aircraft_systems": (
        "A mechanical, electrical, avionics, flight-control or other aircraft-system malfunction. "
        "Choose this when the malfunction is the main reported issue, even if it occurs during taxi."
    ),
    "other": "A safety issue outside the families above, such as weather, airspace or airport infrastructure.",
    "insufficient_evidence": "The narrative does not contain enough information to assign any family.",
}

# Aircraft is a major ASRS primary-problem code (168 of the 503 June 2024 reports; 1,548 of the
# 5,127 reports in the July 2023 - June 2024 export), so equipment deserves its own triage choice. The two ingested FAA taxi PDFs do
# not cover aircraft-system malfunctions; keep their guidance topics unchanged.
GUIDANCE_TOPICS: list[str] = ["communication_coordination", "runway_surface", "procedural_deviation"]
