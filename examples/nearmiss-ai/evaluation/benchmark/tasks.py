"""Benchmark tasks: one bounded decision each, scored against NASA's own analyst-coded fields.

Both providers receive the same instructions, the same option labels and descriptions, and the
same narrative. The coded field used as ground truth is never shown to either model.

The option descriptions below are written for this benchmark; they are NOT NASA's official
definitions (see README "Limitations").
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

# Cases used in the live demo and fixtures; kept out of the benchmark sample.
DEMO_ACNS = ("2134024", "2132897", "2134849", "2126005", "2133280")


@dataclass(frozen=True)
class Task:
    name: str
    title: str
    instructions: str
    options: dict[str, str]  # label -> description, in presentation order
    labeler: Callable[[dict], str | None] = field(repr=False)
    ground_truth_field: str = ""

    def label_of(self, row: dict) -> str | None:
        return self.labeler(row)


@dataclass(frozen=True)
class Case:
    case_id: str
    narrative: str
    label: str


def _communication_label(row: dict) -> str:
    # A blank cell means "no breakdown coded", not "verified absent" (documented limitation).
    return "breakdown" if row["communication_breakdown"] else "no_breakdown"


_PRIMARY_MAIN = ("Human Factors", "Aircraft", "Procedure", "Ambiguous")


def _problem_label(row: dict) -> str | None:
    value = row["primary_problem"]
    if not value:
        return None  # 2 records in the export have no Primary Problem; they cannot be scored
    return value if value in _PRIMARY_MAIN else "Other"


COMMUNICATION = Task(
    name="communication_breakdown",
    title="Communication breakdown detection",
    instructions=(
        "Read this aviation safety report narrative. Does it describe a communication breakdown, meaning "
        "information that was missed, misunderstood, not passed on, or not confirmed between the people "
        "involved in the event (for example pilot and controller, flight crew members, or crew and ground "
        "personnel)?"
    ),
    options={
        "breakdown": "The narrative describes a communication breakdown between people.",
        "no_breakdown": "The narrative does not describe a communication breakdown between people.",
    },
    labeler=_communication_label,
    ground_truth_field="Person 1 / Communication Breakdown (NASA analyst-coded; non-blank = breakdown)",
)

PRIMARY_PROBLEM = Task(
    name="primary_problem",
    title="Primary problem category",
    instructions=(
        "Read this aviation safety report narrative. Which category best describes the most prominent problem "
        "in the event?"
    ),
    options={
        "Human Factors": "The most prominent problem is human performance, such as fatigue, distraction, workload, situational awareness, training, or crew coordination.",
        "Aircraft": "The most prominent problem is an aircraft equipment or system malfunction or defect.",
        "Procedure": "The most prominent problem is a procedure, checklist, regulation or instruction that was missing, unclear, or not followed.",
        "Ambiguous": "No single problem clearly stands out; several factors contribute and none clearly leads.",
        "Other": "The most prominent problem is something else, such as weather, airport or airspace infrastructure, ATC equipment, charts or publications, company policy, or staffing.",
    },
    labeler=_problem_label,
    ground_truth_field="Assessments / Primary Problem (NASA analyst-coded; 13 minor values grouped as Other)",
)

TASKS: dict[str, Task] = {COMMUNICATION.name: COMMUNICATION, PRIMARY_PROBLEM.name: PRIMARY_PROBLEM}


def sample_cases(db, task: Task, n: int, seed: int, exclude: tuple[str, ...] = DEMO_ACNS) -> list[Case]:
    """Class-balanced, seeded sample (n // number_of_classes per class), shuffled, demo cases excluded.

    `db` is a DuckDB connection to the imported incidents table. The result is fully determined by
    (database contents, task, n, seed).
    """
    rows = db.execute(
        "SELECT acn, narrative, communication_breakdown, primary_problem FROM incidents ORDER BY acn"
    ).fetchall()
    pools: dict[str, list[Case]] = {label: [] for label in task.options}
    banned = set(exclude)
    for acn, narrative, communication, problem in rows:
        if acn in banned:
            continue
        label = task.label_of({"communication_breakdown": communication, "primary_problem": problem})
        if label is not None:
            pools[label].append(Case(case_id=acn, narrative=narrative, label=label))
    per_class = n // len(task.options)
    if per_class < 1:
        raise ValueError(f"n={n} is too small for {len(task.options)} classes")
    rng = random.Random(f"{seed}:{task.name}")
    chosen: list[Case] = []
    for label, pool in pools.items():
        if len(pool) < per_class:
            raise ValueError(f"Only {len(pool)} '{label}' cases available for {task.name}; need {per_class}")
        chosen.extend(rng.sample(pool, per_class))
    rng.shuffle(chosen)  # interleave classes so a partial run stays balanced
    return chosen
