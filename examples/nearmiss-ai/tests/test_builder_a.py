"""Builder A checks using only the official local ASRS export."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from data_pipeline.asrs import DEFAULT_DB, ROOT, count_cases, get_case_context, import_export, period_of, read_export, search_cases


class ASRSDataChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ROOT / "data" / "asrs_jul2023_jun2024.csv"
        if not cls.source.exists():
            raise unittest.SkipTest("Official ASRS CSV not installed; see data/manifest.md")
        with cls.source.open(encoding="utf-8-sig", newline="") as stream:
            cls.header_and_rows = list(csv.reader(stream))

    def export_rows(self, rows, destination):
        with destination.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(self.header_and_rows[:2] + rows)

    def test_missing_narrative_rejected(self):
        row = next(item.copy() for item in self.header_and_rows[2:] if item)
        row[120] = ""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.csv"
            self.export_rows([row], path)
            with self.assertRaisesRegex(ValueError, "Missing primary narrative"):
                read_export(path)

    def test_duplicate_acn_rejected(self):
        row = next(item for item in self.header_and_rows[2:] if item)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.csv"
            self.export_rows([row, row], path)
            with self.assertRaisesRegex(ValueError, "duplicate ACN"):
                read_export(path)

    def test_one_real_record_has_empty_neighbors(self):
        row = next(item for item in self.header_and_rows[2:] if item)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.csv"
            database = Path(directory) / "one.duckdb"
            self.export_rows([row], path)
            import_export(path, database)
            with duckdb.connect(str(database), read_only=True) as connection:
                context = get_case_context(connection, row[0])
                self.assertEqual(context["candidate_incidents"], [])
                self.assertEqual(context["narrative"], row[120])

    def test_real_corpus_search_and_context(self):
        with duckdb.connect(str(DEFAULT_DB), read_only=True) as connection:
            expected = len([row for row in self.header_and_rows[2:] if row])
            self.assertEqual(connection.execute("SELECT count(*) FROM incidents").fetchone()[0], expected)
            self.assertGreater(expected, 5000)
            results = search_cases(connection, "pushback", "2024-06", "", 1000)
            self.assertTrue(results)
            context = get_case_context(connection, "2134849")
            self.assertEqual(len(context["candidate_incidents"]), 5)
            self.assertNotIn(context["case_id"], [candidate["case_id"] for candidate in context["candidate_incidents"]])
            self.assertEqual(len(json.loads(connection.execute("SELECT raw_fields FROM incidents WHERE acn = '2134849'").fetchone()[0])), 126)

    def test_paging_total_and_period(self):
        with duckdb.connect(str(DEFAULT_DB), read_only=True) as connection:
            total = count_cases(connection)
            first = search_cases(connection, limit=1000, offset=0)
            second = search_cases(connection, limit=1000, offset=1000)
            self.assertEqual(len(first), 1000)
            self.assertFalse({row["acn"] for row in first} & {row["acn"] for row in second})
            self.assertEqual(len(search_cases(connection, limit=1000, offset=total)), 0)
            self.assertEqual(count_cases(connection, month="2024-06"), len(search_cases(connection, month="2024-06", limit=1000)))
            self.assertEqual(count_cases(connection, phrase="pushback"), len(search_cases(connection, phrase="pushback", limit=1000)))
            period = period_of(connection)
            self.assertEqual((period["first_month"], period["last_month"]), ("2023-07", "2024-06"))  # isolated out-of-window records ignored

    def test_communication_filter_matches_coded_breakdowns(self):
        with duckdb.connect(str(DEFAULT_DB), read_only=True) as connection:
            coded = connection.execute("SELECT count(*) FROM incidents WHERE communication_breakdown IS NOT NULL").fetchone()[0]
            self.assertGreater(coded, 0)
            self.assertEqual(count_cases(connection, category="Communication"), coded)
            self.assertEqual(count_cases(connection, category="communication"), coded)
            self.assertTrue(all(row["communication_breakdown"] for row in search_cases(connection, category="Communication", limit=1000)))

    def test_candidates_come_from_the_whole_corpus(self):
        with duckdb.connect(str(DEFAULT_DB), read_only=True) as connection:
            context = get_case_context(connection, "2134849")
            self.assertEqual(len(context["candidate_incidents"]), 5)
            scores = [candidate["retrieval_score"] for candidate in context["candidate_incidents"]]
            self.assertEqual(scores, sorted(scores, reverse=True))
            self.assertTrue(all(0 <= score <= 1 for score in scores))


if __name__ == "__main__":
    unittest.main()
