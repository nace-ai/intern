"""Load public cases separately from sealed benchmark labels."""
import hashlib
import json
import re
from billproof.config import SYN_DIR, REAL_DIR
from billproof.schema import Case, GoldItem


def load_case(case_id: str) -> Case:
    if case_id.startswith("UP-"):
        from billproof.ingest import load_uploaded
        return load_uploaded(case_id)
    if not re.fullmatch(r'SYN-(?:[0-9]{4}|D[0-9]{2})', case_id):
        raise ValueError('Invalid synthetic case identifier')
    return Case.model_validate_json((SYN_DIR / 'cases' / f'{case_id}.json').read_text())


def list_cases(split: str | None = None) -> list[Case]:
    cases = [Case.model_validate_json(p.read_text()) for p in sorted((SYN_DIR / 'cases').glob('*.json'))]
    return [c for c in cases if split is None or c.split == split]


def load_labels(case_id: str) -> dict:
    with (SYN_DIR / 'labels.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row['case_id'] == case_id:
                return row
    raise KeyError(case_id)


def load_gold(case_id: str) -> dict[str, GoldItem]:
    return {k: GoldItem.model_validate(v) for k, v in load_labels(case_id)['gold'].items()}


def label_sha256(bench: str = 'synthetic') -> str:
    if bench not in ('synthetic', 'imr'):
        raise ValueError(bench)
    path = SYN_DIR / 'labels.jsonl' if bench == 'synthetic' else REAL_DIR / 'imr_labels.jsonl'
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''
