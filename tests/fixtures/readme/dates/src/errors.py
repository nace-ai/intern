"""The errors the library raises: each says what text it could not read, and why."""


class DateError(ValueError):
    """Any date the library could not read."""


class UnparsedDate(DateError):
    def __init__(self, text: str, reason: str) -> None:
        super().__init__(f"cannot read {text!r} as a date: {reason}")
        self.text = text
        self.reason = reason


class AmbiguousDate(DateError):
    """Both numbers could be the day: 03/04/2026 is 3 April or 4 March."""

    def __init__(self, text: str) -> None:
        super().__init__(f"{text!r} could be day-first or month-first: pass day_first")
        self.text = text
