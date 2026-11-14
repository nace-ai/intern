"""
Pre-cached, high-fidelity demo scenarios for ChronoGround.
Ensures 100% stage reliability even if conference Wi-Fi drops or upload times out.
"""

from typing import Any

SCENARIOS: dict[str, dict[str, Any]] = {
    "texas_auto_nexar_incident": {
        "id": "texas_auto_nexar_incident",
        "title": "Texas Personal Auto: Nexar Dashcam Rainy Intersection Crossing",
        "category": "Personal Auto & Rainy Weather Triage",
        "query": "Evaluate intersection crossing footage for illegal racing, intentional hazard, or speed contest exclusions under Texas Personal Auto Policy TX 01-PAP-003.",
        "video": {
            "title": "Nexar Cam-00406 Wet Intersection Telematics Feed",
            "duration_seconds": 18,
            "segment_duration_seconds": 3,
            "total_segments": 6,
            "active_incident_segments": ["s02", "s03", "s04"],
            "onset_segment": "s02",
            "peak_segment": "s03",
            "offset_segment": "s04",
            "incident_start_time": 4.5,
            "incident_end_time": 9.8,
        },
        "policy": {
            "document_name": "texas_personal_auto_policy_2024.pdf",
            "total_pages": 51,
            "primary_violated_clause_id": "c3",
        }
    },
    "dashcam_lane_violation": {
        "id": "dashcam_lane_violation",
        "title": "Commercial Fleet Dashcam: Double-Yellow Lane Cross & Near-Miss",
        "category": "Commercial Auto Insurance & Fleet Safety",
        "query": "Find the exact moment the commercial vehicle crosses the double yellow line into opposing traffic, and determine whether coverage is Approved, Denied, or Escalated under Policy Section 4.",
        "video": {
            "title": "Dashcam Cam-04 Forward Telematics Feed",
            "duration_seconds": 180,
            "segment_duration_seconds": 3,
            "total_segments": 60,
            "active_incident_segments": ["s12", "s13", "s14"],
            "onset_segment": "s12",
            "peak_segment": "s13",
            "offset_segment": "s14",
            "incident_start_time": 34.2,
            "incident_end_time": 41.5,
        },
        "policy": {
            "document_name": "commercial_fleet_master_policy_2026.pdf",
            "total_pages": 15,
            "primary_violated_clause_id": "c4",
        }
    },
    "workplace_safety_interlock": {
        "id": "workplace_safety_interlock",
        "title": "Industrial Stamping Plant: Light Curtain Safety Interlock Bypass",
        "category": "Workers Comp & OSHA SOP Compliance",
        "query": "Detect the timestamp when the press operator reaches past the optical safety curtain while the ram is cycling, and determine liability under SOP Section 8.",
        "video": {
            "title": "Bay 3 Hydraulic Stamping Cell CCTV",
            "duration_seconds": 180,
            "segment_duration_seconds": 3,
            "total_segments": 60,
            "active_incident_segments": ["s24", "s25", "s26"],
            "onset_segment": "s24",
            "peak_segment": "s25",
            "offset_segment": "s26",
            "incident_start_time": 71.0,
            "incident_end_time": 78.5,
        },
        "policy": {
            "document_name": "industrial_machinery_sop_osha_compliant.pdf",
            "total_pages": 12,
            "primary_violated_clause_id": "c6",
        }
    }
}


def get_texas_nexar_segments() -> list[dict[str, Any]]:
    """Segments for the 18-second 00406.mp4 Nexar rainy intersection video."""
    return [
        {
            "id": "s01",
            "t_start": 0.0,
            "t_end": 3.0,
            "transcript": "Vehicle approaching signalized multi-lane intersection in wet, overcast conditions. Windshield wipers active. Traffic signal green.",
            "visual_tags": ["weather:rain", "wipers:active", "signal:green", "speed:28mph", "lane:centered"]
        },
        {
            "id": "s02",
            "t_start": 3.0,
            "t_end": 6.0,
            "transcript": "INCIDENT ONSET: Vehicle crosses stop bar into intersection. Oncoming silver sedan in opposing turn lane slows to yield. Road surface wet and reflective.",
            "visual_tags": ["intersection:entry", "oncoming_cars:turning", "surface:wet_reflective", "speed:29mph", "signal:green"]
        },
        {
            "id": "s03",
            "t_start": 6.0,
            "t_end": 9.0,
            "transcript": "CRITICAL APEX: Silver sedan completes left turn in front of vehicle path with narrow clearance; driver modulates throttle and maintains straight lane through intersection.",
            "visual_tags": ["apex:clearance", "oncoming_turn:close_proximity", "traction:wet_road", "speed:30mph", "evasive_action:none"]
        },
        {
            "id": "s04",
            "t_start": 9.0,
            "t_end": 12.0,
            "transcript": "INCIDENT OFFSET: Vehicle safely exits intersection corridor, accelerating towards highway approach under overhead electronic advisory sign ('VEH OVER 13FT 6IN USE ALT RTE').",
            "visual_tags": ["intersection:cleared", "overhead_sign:active", "acceleration:35mph", "hazard:none"]
        },
        {
            "id": "s05",
            "t_start": 12.0,
            "t_end": 15.0,
            "transcript": "Ascending overpass approach ramp. Speed telemetry indicates smooth cruise at 38 mph. Rain intensity steady.",
            "visual_tags": ["ramp:highway_approach", "lane_keeping:centered", "speed:38mph", "weather:rain"]
        },
        {
            "id": "s06",
            "t_start": 15.0,
            "t_end": 18.0,
            "transcript": "Vehicle maintains straight lane heading under overpass bridge. Normal highway transit established.",
            "visual_tags": ["transit:stable", "speed:40mph", "road:freeway_ramp"]
        }
    ]


def get_texas_policy_clauses() -> list[dict[str, Any]]:
    """Extracts clauses from texas_personal_auto_policy_2024.pdf (TX 01-PAP-003)."""
    return [
        {
            "id": "c1",
            "section": "Part A - Insuring Agreement",
            "title": "Liability Coverage & Permissive Operator Defense",
            "page": 5,
            "text": "We will pay damages for bodily injury or property damage for which any covered person becomes legally responsible because of an auto accident. We will settle or defend any claim or suit asking for these damages.",
            "bounding_box": {"page": 5, "x0": 0.10, "y0": 0.15, "x1": 0.90, "y1": 0.28},
            "is_exclusion": False
        },
        {
            "id": "c2",
            "section": "Part A - Exclusion 1",
            "title": "Intentional Acts & Deliberate Damage Exclusion",
            "page": 6,
            "text": "We do not provide Liability Coverage to any person who intentionally causes or directs another to cause bodily injury or property damage.",
            "bounding_box": {"page": 6, "x0": 0.12, "y0": 0.35, "x1": 0.88, "y1": 0.45},
            "is_exclusion": True
        },
        {
            "id": "c3",
            "section": "Part A - Exclusion 6",
            "title": "Racing, Speed Contest & Exhibition Driving Exclusion",
            "page": 7,
            "text": "We do not provide Liability Coverage for any vehicle located inside a facility designed for racing, or while participating in any organized or unorganized racing event, speed contest, or competitive exhibition.",
            "bounding_box": {"page": 7, "x0": 0.11, "y0": 0.50, "x1": 0.89, "y1": 0.62},
            "is_exclusion": True
        },
        {
            "id": "c4",
            "section": "Part A - Exclusion 2",
            "title": "Commercial Delivery & Rideshare Use Exclusion",
            "page": 7,
            "text": "We do not provide Liability Coverage for ownership or operation of any vehicle while it is being used to carry persons or property for a fee, including delivery or ride-sharing, unless share-the-expense car pool.",
            "bounding_box": {"page": 7, "x0": 0.12, "y0": 0.20, "x1": 0.88, "y1": 0.32},
            "is_exclusion": True
        },
        {
            "id": "c5",
            "section": "Part B - Medical Payments",
            "title": "Medical Payments & Emergency Treatment Protection",
            "page": 8,
            "text": "We will pay reasonable expenses incurred for necessary medical and funeral services because of bodily injury caused by accident and sustained by a covered person.",
            "bounding_box": {"page": 8, "x0": 0.10, "y0": 0.15, "x1": 0.90, "y1": 0.25},
            "is_exclusion": False
        },
        {
            "id": "c6",
            "section": "Part C - UM/UIM Coverage",
            "title": "Uninsured / Underinsured Motorist Protection",
            "page": 11,
            "text": "We will pay damages which a covered person is legally entitled to recover from the owner or operator of an uninsured motor vehicle because of bodily injury or property damage.",
            "bounding_box": {"page": 11, "x0": 0.10, "y0": 0.22, "x1": 0.90, "y1": 0.35},
            "is_exclusion": False
        },
        {
            "id": "c7",
            "section": "Part D - Damage to Your Auto",
            "title": "Collision & Comprehensive Weather / Rain Loss",
            "page": 13,
            "text": "We will pay for direct and accidental loss to your covered auto. Loss caused by water, flood, or contact with bird or animal is comprehensive coverage.",
            "bounding_box": {"page": 13, "x0": 0.11, "y0": 0.40, "x1": 0.89, "y1": 0.52},
            "is_exclusion": False
        },
        {
            "id": "c8",
            "section": "General Provisions",
            "title": "Duty to Report & Submit Telematics Evidence",
            "page": 17,
            "text": "A person seeking coverage must give us prompt notice of the accident, how, when and where the accident happened, and submit all video or electronic telematics upon request.",
            "bounding_box": {"page": 17, "x0": 0.10, "y0": 0.30, "x1": 0.90, "y1": 0.42},
            "is_exclusion": False
        }
    ]


def get_dashcam_segments() -> list[dict[str, Any]]:
    segments = []
    for i in range(1, 61):
        seg_id = f"s{i:02d}"
        t_start = round((i - 1) * 3.0, 1)
        t_end = round(i * 3.0, 1)
        
        if i < 11:
            desc = "Routine commercial transit along suburban secondary arterial. Lane alignment centered, speed 34 mph, dry pavement."
            visual_tags = ["traffic:normal", "lane_departure:none", "brake_lights:inactive", "speed:34mph"]
        elif i == 11:
            desc = "Approaching blind leftward curve. Speed telemetry indicates acceleration to 44 mph. Vehicle begins mild drift towards center divider."
            visual_tags = ["curve:left", "speed_increase:44mph", "lane_drift:minor"]
        elif i == 12:
            desc = "INCIDENT ONSET: Front left tire breaches solid double yellow dividing lines. Oncoming headlights emerge from curve. Telematics alarms sound."
            visual_tags = ["line_breach:double_yellow", "oncoming_traffic:visible", "lane_deviation:active", "speed:48mph"]
        elif i == 13:
            desc = "INCIDENT CRITICAL PEAK: Vehicle crosses 80% into oncoming opposing traffic lane at 52 mph. Opposing box truck sounds air horn; severe near-miss evasive maneuver."
            visual_tags = ["oncoming_head_on_risk:extreme", "full_lane_trespass:yes", "speed:52mph_in_35mph", "hazard_airhorn:active"]
        elif i == 14:
            desc = "INCIDENT OFFSET: Operator yanks steering wheel right; chassis skids across rumble strip onto right shoulder gravel. Collision avoided by 1.8 feet."
            visual_tags = ["recovery_skid:shoulder", "dust_plume:active", "emergency_braking:maximum", "speed_drop:12mph"]
        elif i == 15:
            desc = "Vehicle halts on unimproved right shoulder. Hazard flashers engaged. Telematics accelerometer logs 0.82G lateral jerk."
            visual_tags = ["vehicle_stopped:shoulder", "hazards:on", "telematics_flag:g_force_alert"]
        else:
            desc = f"Post-incident stationary idling on shoulder. Hazard blinkers active. Operator cabin telemetry idle at t={t_start}s."
            visual_tags = ["stationary:shoulder", "hazards:on", "engine:idling"]

        segments.append({
            "id": seg_id,
            "t_start": t_start,
            "t_end": t_end,
            "transcript": desc,
            "visual_tags": visual_tags,
        })
    return segments


def get_dashcam_policy_clauses() -> list[dict[str, Any]]:
    return [
        {
            "id": "c1",
            "section": "Section 1.1",
            "title": "Eligible Operators & Commercial Authorization",
            "page": 2,
            "text": "Coverage applies exclusively to certified commercial drivers with valid Class-B endorsements who maintain an active duty log on the automated telematics system.",
            "bounding_box": {"page": 2, "x0": 0.12, "y0": 0.22, "x1": 0.88, "y1": 0.32},
            "is_exclusion": False,
        },
        {
            "id": "c2",
            "section": "Section 2.4",
            "title": "Telematics Continuous Monitoring Mandate",
            "page": 4,
            "text": "Every insured vehicle must keep forward and cabin-facing optical telematics enabled during all commercial transit operating hours. Unexplained camera outages void indemnity.",
            "bounding_box": {"page": 4, "x0": 0.14, "y0": 0.55, "x1": 0.86, "y1": 0.65},
            "is_exclusion": False,
        },
        {
            "id": "c3",
            "section": "Section 3.2",
            "title": "Excessive Velocity Thresholds & Speed Governance",
            "page": 6,
            "text": "Vehicular operation exceeding the posted regulatory speed limit by greater than fifteen (15) miles per hour shall incur mandatory underwriting surcharge and conditional defense review.",
            "bounding_box": {"page": 6, "x0": 0.10, "y0": 0.34, "x1": 0.90, "y1": 0.46},
            "is_exclusion": False,
        },
        {
            "id": "c4",
            "section": "Section 4.2",
            "title": "Reckless Lane Departure & Crossing of Solid Traffic Dividers Exclusion",
            "page": 7,
            "text": "DIRECT EXCLUSION OF LIABILITY: No indemnity or defense shall be afforded under this policy for any loss, third-party claim, near-miss investigation, or collision resulting directly from willful, unprovoked, or reckless vehicular trespass across painted solid double yellow dividing lines or physical barriers into oncoming lanes of traffic, absent documented proof of immediate sudden mechanical failure or emergency obstacle avoidance.",
            "bounding_box": {"page": 7, "x0": 0.11, "y0": 0.44, "x1": 0.89, "y1": 0.62},
            "is_exclusion": True,
        },
        {
            "id": "c5",
            "section": "Section 4.8",
            "title": "Aftermarket Optical Equipment Warranty",
            "page": 8,
            "text": "Telematics recording devices must meet SAE J3016 optical clarity standards and not be tampered with or modified without insurer written authorization.",
            "bounding_box": {"page": 8, "x0": 0.15, "y0": 0.18, "x1": 0.85, "y1": 0.28},
            "is_exclusion": False,
        },
        {
            "id": "c6",
            "section": "Section 5.1",
            "title": "Operator Distraction & Mobile Device Prohibition",
            "page": 9,
            "text": "Hands-free mobile communication is permitted solely via integrated Bluetooth. Handheld mobile device operation during transit constitutes gross operational negligence.",
            "bounding_box": {"page": 9, "x0": 0.12, "y0": 0.60, "x1": 0.88, "y1": 0.72},
            "is_exclusion": True,
        },
        {
            "id": "c7",
            "section": "Section 6.3",
            "title": "Immediate Incident Notification Requirement",
            "page": 10,
            "text": "Insured must transmit optical telemetry and collision sensor logs to the claims intake desk within twenty-four (24) hours of any reportable event or severe telematics anomaly.",
            "bounding_box": {"page": 10, "x0": 0.13, "y0": 0.28, "x1": 0.87, "y1": 0.38},
            "is_exclusion": False,
        },
        {
            "id": "c8",
            "section": "Section 7.1",
            "title": "Gross Vehicle Weight & Axle Distribution Rating",
            "page": 11,
            "text": "Payload capacity must remain strictly within manufacturer GVWR gross vehicle weight limits as verified by roadside weight station manifests.",
            "bounding_box": {"page": 11, "x0": 0.14, "y0": 0.48, "x1": 0.86, "y1": 0.58},
            "is_exclusion": False,
        },
        {
            "id": "c9",
            "section": "Section 8.2",
            "title": "Adverse Weather & Reduced Visibility Safe Harbor",
            "page": 12,
            "text": "In conditions of rain, snow, or fog reducing sight distance below 300 feet, safe operating velocity must decrease by at least 25% below posted dry limits.",
            "bounding_box": {"page": 12, "x0": 0.12, "y0": 0.15, "x1": 0.88, "y1": 0.26},
            "is_exclusion": False,
        },
        {
            "id": "c10",
            "section": "Section 9.4",
            "title": "Subrogation Rights Against Third-Party Infringing Operators",
            "page": 13,
            "text": "In the event of an excluded claim caused jointly by third-party negligence, the insurer reserves full subrogation recovery rights against external motor carriers.",
            "bounding_box": {"page": 13, "x0": 0.13, "y0": 0.65, "x1": 0.87, "y1": 0.76},
            "is_exclusion": False,
        },
        {
            "id": "c11",
            "section": "Section 10.1",
            "title": "Bodily Injury & Property Damage Comprehensive Schedule",
            "page": 14,
            "text": "Maximum combined single limit liability coverage is $2,000,000 per commercial incident occurrence, subject to applicable deductibles and policy exclusions.",
            "bounding_box": {"page": 14, "x0": 0.10, "y0": 0.20, "x1": 0.90, "y1": 0.32},
            "is_exclusion": False,
        },
        {
            "id": "c12",
            "section": "Section 11.2",
            "title": "Defensive Driving Telematics Safety Discount",
            "page": 14,
            "text": "Drivers maintaining a zero-departure record for 180 consecutive operating days earn a 15% deductible credit on qualifying first-party collision claims.",
            "bounding_box": {"page": 14, "x0": 0.14, "y0": 0.70, "x1": 0.86, "y1": 0.80},
            "is_exclusion": False,
        },
        {
            "id": "c13",
            "section": "Section 12.5",
            "title": "Mandatory Arbitration & Dispute Forum",
            "page": 15,
            "text": "Any contested denial of indemnity under Section 4 exclusions shall proceed to binding AAA commercial arbitration in the governing jurisdiction.",
            "bounding_box": {"page": 15, "x0": 0.12, "y0": 0.18, "x1": 0.88, "y1": 0.28},
            "is_exclusion": False,
        },
        {
            "id": "c14",
            "section": "Section 14.1",
            "title": "Telematics Tampering & Material Misrepresentation",
            "page": 15,
            "text": "Intentional deletion, distortion, or withholding of dashcam telematics files triggers immediate policy rescission ab initio and criminal referral.",
            "bounding_box": {"page": 15, "x0": 0.11, "y0": 0.40, "x1": 0.89, "y1": 0.52},
            "is_exclusion": True,
        },
        {
            "id": "c15",
            "section": "Section 15.0",
            "title": "Summary Claim Routing & Auto-Triage Rules",
            "page": 15,
            "text": "Autonomous triage rules: Claims with clear liability and zero exclusions are auto-approved; claims with verified Section 4 Reckless Lane Departure breaches (>0.85 confidence) are auto-denied; borderline incidents are flagged for tier-3 senior adjuster manual review.",
            "bounding_box": {"page": 15, "x0": 0.10, "y0": 0.68, "x1": 0.90, "y1": 0.85},
            "is_exclusion": False,
        },
    ]


def build_systemone_questions(
    segments: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
    query: str,
    policy_name: str | None = None
) -> dict[str, Any]:
    """
    Dynamically compiles the Drex System One question matrix from ANY segments and policy clauses.
    Zero hardcoded segment IDs or question counts.
    """
    questions: dict[str, Any] = {}

    # 1. Dynamic segment activity questions (noul)
    for seg in segments:
        seg_id = seg["id"]
        t_span = f"{seg.get('t_start', 0.0)}s to {seg.get('t_end', 0.0)}s"
        questions[f"{seg_id}_active"] = {
            "type": "noul",
            "instructions": f"Does segment {seg_id} ({t_span}) exhibit significant vehicle activity, maneuver, or incident relevance to: '{query}'?",
            "criteria": {
                "true": f"Segment {seg_id} shows active incident maneuver, turning, or conflict",
                "false": f"Segment {seg_id} represents routine transit or background cruising"
            }
        }

    # 2. Dynamic action boundary classification (choice: onset, continuation, offset, background)
    for seg in segments:
        seg_id = seg["id"]
        questions[f"{seg_id}_boundary"] = {
            "type": "choice",
            "instructions": f"Classify the temporal action boundary role of segment {seg_id} in the incident sequence.",
            "criteria": {
                "onset": "The initiation, approach, or commencement of the maneuver/incident",
                "continuation": "The critical apex, crossing, or peak of the maneuver/incident",
                "offset": "The stabilization, recovery, or completion of the maneuver/incident",
                "background": "Routine background driving before or after the incident"
            }
        }

    # 3. Dynamic policy clause exclusion verification (noul)
    for clause in clauses:
        c_id = clause["id"]
        sec = clause.get("section", f"Clause {c_id}")
        title = clause.get("title", "")
        questions[f"violates_clause_{c_id}"] = {
            "type": "noul",
            "instructions": f"Does the video evidence substantiate a violation or trigger an exclusion under {sec} ({title})?",
            "criteria": {
                "true": f"Evidence substantiates breach of {sec}",
                "false": f"Operation is compliant with {sec}; exclusion not triggered"
            }
        }

    # 4. Telematics & Risk assessment scores
    questions["incident_severity"] = {
        "type": "score",
        "instructions": "Rate the overall physical and situational hazard severity of the incident.",
        "criteria": ["negligible", "minor", "moderate", "severe", "near_catastrophic"]
    }
    questions["speed_excess_tier"] = {
        "type": "score",
        "instructions": "Rate the degree of speed limit exceedance or speeding risk.",
        "criteria": ["within_limit", "1_to_5mph_over", "6_to_15mph_over", "16_to_25mph_over", "extreme_overspeed"]
    }
    questions["operator_culpability"] = {
        "type": "score",
        "instructions": "Assess operator culpability vs external unavoidable conditions.",
        "criteria": ["zero_culpability", "contributory_minor", "substantial_fault", "gross_recklessness"]
    }
    questions["weather_surface_hazard"] = {
        "type": "score",
        "instructions": "Rate roadway surface wetness and traction hazard.",
        "criteria": ["dry_pavement", "damp", "wet_rain_slick", "severe_standing_water"]
    }

    # 5. Autonomous claim adjudication disposition (choice)
    questions["final_disposition"] = {
        "type": "choice",
        "instructions": "Determine final claim coverage disposition based on policy compliance and video evidence.",
        "criteria": {
            "approve": "Operation compliant with policy provisions; no statutory exclusions triggered.",
            "deny": "Direct policy exclusion or reckless breach substantiated by optical evidence.",
            "escalate": "Inconclusive optical evidence or complex liability requiring Tier-3 human review."
        }
    }

    return questions


# Universal alias so any caller can use either name
build_texas_systemone_questions = build_systemone_questions


