"""Row schema for printed city directory pages (Hill's Wilmington, NC, 1928)."""

ENTRY = {
    "type": "object",
    "properties": {
        "surname": {"type": "string", "description": "Surname as printed at the start of the entry."},
        "given_names": {"type": "string", "description": "Given names and initials as printed, e.g. 'Benj R' or 'Wm T'. Include Jr/Sr/Mrs if printed."},
        "spouse": {"type": "string", "description": "Name in parentheses after the given names (the wife's given name), without the parentheses. Empty if none."},
        "colored_mark": {"type": "boolean", "description": "True if the entry carries the printed '(c)' or '(c; ...)' mark."},
        "widow_of": {"type": "string", "description": "Name after 'wid' inside parentheses, e.g. 'T G' from '(wid T G)'. Empty if none."},
        "occupation": {"type": "string", "description": "Occupation as printed, abbreviations kept (e.g. 'lab', 'dom', 'slsmn'). Empty if none."},
        "employer": {"type": "string", "description": "Employer or business named after the occupation, as printed. Empty if none."},
        "residence_code": {"type": "string", "description": "The single letter before the address: h (householder), r (resides/rooms), b (boards). Empty if none."},
        "address": {"type": "string", "description": "Address after the residence code, as printed (e.g. '123 N 4th'). Empty if none."},
    },
    "required": ["surname", "given_names", "spouse", "colored_mark", "widow_of", "occupation", "employer", "residence_code", "address"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "description": "Every resident entry in the alphabetical listing on this page, in printed order. An entry can wrap onto an indented second line; join it. Skip advertisements, page headers and running titles.",
            "items": ENTRY,
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}

INSTRUCTIONS = (
    "This is a page of a printed 1928 city directory (Wilmington, North Carolina). "
    "Transcribe every resident entry in the alphabetical listing, one entry per person or business, "
    "exactly as printed, keeping the directory's abbreviations. Ignore advertisements and headers."
)
