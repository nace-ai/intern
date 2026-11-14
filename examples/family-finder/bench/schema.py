"""The row schema both systems fill, and the shared instructions."""

PERSON = {
    "type": "object",
    "properties": {
        "line": {"type": "integer", "description": "Line number printed at the left of the row, 1 to 30."},
        "full_name": {"type": "string", "description": "Given names then surname, e.g. 'John A. Smith'. Where the surname is a dash or ditto mark, fill in the surname from the row above. Empty string if the line is blank."},
        "relationship": {"type": "string", "description": "Column 8, relationship to head, as written (Head, wife, son, daughter, lodger...)."},
        "race": {"type": "string", "description": "Column 9, as written (W, Neg, ...)."},
        "sex": {"type": "string", "description": "Column 10, M or F."},
        "age": {"type": "string", "description": "Column 11, age at last birthday as written. Infants may have a month abbreviation such as 'Jan'."},
        "marital_status": {"type": "string", "description": "Column 12, as written (Mar, Nev, Wd, D, Sep)."},
        "birthplace": {"type": "string", "description": "Column 13, state or foreign country of birth, spelled out (e.g. 'Delaware')."},
        "occupation": {"type": "string", "description": "Column 20a, occupation, as written. Empty string if none."},
    },
    "required": ["line", "full_name", "relationship", "race", "sex", "age", "marital_status", "birthplace", "occupation"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "description": "One entry per numbered line (1 to 30) that lists a person, in line order. Skip lines that say 'no one at home' or are blank.",
            "items": PERSON,
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}

INSTRUCTIONS = (
    "This is a page of the 1950 US census, population schedule (Form P1). "
    "Transcribe the upper table, one entry per person line (lines 1 to 30). "
    "Ignore the sample-line questions in the lower half of the page. "
    "Transcribe the handwriting as written; do not guess missing values, use an empty string instead."
)
