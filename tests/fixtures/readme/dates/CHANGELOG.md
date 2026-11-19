# Changelog

## Unreleased

- ISO week dates (`2026-W05-3`) are listed in `FORMATS` and documented, but `parse_date` does not read them yet.
- `weeks_in_year`.

## 0.8.2

- `months_between` counts whole months from a month's last day correctly.
- `find_dates` skips candidates that are not dates (`2026-13-01`) instead of raising.

## 0.8.1

- Ordinal dates (`2026-028`) through `ordinal_day`.
- `timezones.split_zone` reads `EET` and `JST`.

## 0.8.0

- `normalise` and the `dates normalise` command.
- `AmbiguousDate` says to pass `day_first`.

## 0.7.0

- Written dates with abbreviated months and a full stop (`28 Jan. 2026`).
- Two-digit years read as 20xx.
