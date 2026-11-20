"""`intern`, on the day a session was recorded: `python -m on_the_day 2026-11-19 ARGS...`.

Every request gives today's date ahead of the conversation, in the line that names the working directory. Sent on
a later day, a replay would differ from its session before its first turn: the provider would hold nothing of the
session, and would drop System 2's own thinking from the turns that follow, neither of which a user who answers the
same day sees.
"""

import sys
from datetime import date

from intern import instructions
from intern.cli import main

DAY = date.fromisoformat(sys.argv.pop(1))


class SessionDay(date):
    @classmethod
    def today(cls) -> "SessionDay":
        return cls(DAY.year, DAY.month, DAY.day)


instructions.date = SessionDay
sys.exit(main())
