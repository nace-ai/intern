"""Engine tests: simulated mode only, offline. Any request leaving engine.net fails the test."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import cases, config, drex, intern_runner, keenable, lawstore, lawwiki, mcp_server, ndi, net, replay, roles, server, slack_bridge, speech, trial  # noqa: E402

SECRET_ENV = ("NACE_API_KEY", "DREX_API_KEY", "DEEPINFRA_API_KEY", "TAD_USE_INTERN")
CASE_IDS = [c["id"] for c in cases.all_cases()]


class OfflineCase(unittest.TestCase):
    """No keys in the environment, any outgoing request fails the test, run folders live under runs/ and are removed."""

    def setUp(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch("engine.net._open", side_effect=AssertionError("network call attempted")),
                        # the local LawWiki store is optional and 890 MB: these tests run as if it were absent
                        mock.patch.object(config, "LAW_STORE", ROOT / "runs" / "no-such-store.sqlite")):
            patcher.start()
            self.addCleanup(patcher.stop)
        (ROOT / "runs").mkdir(exist_ok=True)
        self.out = Path(tempfile.mkdtemp(prefix="test-", dir=ROOT / "runs"))
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_case(self, case_id: str, flips=None) -> dict:
        return trial.run_trial(case_id, flips, simulate=True, out=self.out)


class TestTrial(OfflineCase):
    def test_each_case_runs_with_valid_probabilities(self) -> None:
        for cid in CASE_IDS:
            with self.subTest(case=cid):
                evs = self.run_case(cid)["events"]
                self.assertGreater(len(evs), 100)
                self.assertEqual([e["i"] for e in evs], list(range(len(evs))))
                for e in evs:
                    for key in ("i", "t", "src", "id", "ms", "simulated"):
                        self.assertIn(key, e)
                    if e["src"] != "drex":
                        continue
                    self.assertTrue(e["simulated"])
                    self.assertIn(e["route"], ("auto", "person"))
                    if e["type"] == "noul":
                        self.assertTrue(0 <= e["p"] <= 1)
                    else:
                        labels = e.get("options") or e.get("levels")
                        self.assertEqual(len(e["dist"]), len(labels))
                        self.assertLess(len(labels), 6)
                        self.assertAlmostEqual(sum(e["dist"]), 1.0, delta=1e-6)

    def test_verdict_matches_votes(self) -> None:
        for cid in CASE_IDS:
            with self.subTest(case=cid):
                res = self.run_case(cid)
                final = [e for e in res["events"] if e["id"] == "liable" and e["phase"] == 3]
                self.assertEqual(len(final), 12)
                liable = sum(e["p"] >= 0.5 for e in final)
                v, summary = next(e for e in res["events"] if e["id"] == "verdict"), res["summary"]
                self.assertIn(f"Vote {liable} to {12 - liable}.", v["text"])
                self.assertEqual(summary["vote"], f"{liable}-{12 - liable}")
                side = cases.get_case(cid)["sides"][1]
                word = summary["role"]["finding"]  # "liable" in a civil case, "guilty" in a criminal one
                expected = f"{side} {word}" if liable >= 9 else f"{side} not {word}" if liable <= 3 else "Split jury"
                self.assertEqual(summary["verdict"]["label"], expected)
                self.assertAlmostEqual(summary["mean"], sum(e["p"] for e in final) / 12, places=9)

    def test_fact_flip_changes_the_vote(self) -> None:
        base, flipped = self.run_case("fl-notice"), self.run_case("fl-notice", ["f1"])
        self.assertNotEqual(base["summary"]["vote"], flipped["summary"]["vote"])
        self.assertEqual(flipped["summary"]["flips"], ["f1"])
        self.assertNotEqual({f["id"]: f["on"] for f in base["summary"]["facts"]}["f1"],
                            {f["id"]: f["on"] for f in flipped["summary"]["facts"]}["f1"])

    def test_unknown_flip_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.run_case("fl-notice", ["nope"])

    def test_image_case_checks_the_image_first(self) -> None:
        evs = self.run_case("fl-notice")["events"]
        checks = [e for e in evs if e["id"] in ("image_legible", "image_has_instructions")]
        self.assertEqual([e["model"] for e in checks], ["drex-max-v1.1", "drex-max-v1.1"])
        self.assertLess(checks[0]["i"], next(e["i"] for e in evs if e["id"] == "parse"))

    def test_uploaded_ruling_is_judged_before_the_trial(self) -> None:
        events = [e for e in self.run_case("ca-wages")["events"] if e["id"].startswith("ruling_")]
        self.assertEqual([e["id"] for e in events], ["ruling_disposition", "ruling_on_point"])
        self.assertEqual(events[0]["options"][0], "Employee won")
        self.assertGreaterEqual(events[1]["p"], 0.5)

    def test_ruling_passages_are_verbatim_and_the_text_matches_its_hash(self) -> None:
        import hashlib
        ruling = cases.get_case("ca-wages")["ruling"]
        rec = json.loads((ROOT / "data" / "rulings" / f"{ruling['name']}.json").read_text())
        self.assertEqual(hashlib.sha256(rec["text"].encode("utf-8")).hexdigest(), rec["sha256"])
        self.assertTrue(rec["sha256_matches_dataset"])
        flat = " ".join(rec["text"].split())
        for key in ("excerpt", "holding", "disposition"):
            self.assertTrue(ruling[key])
            self.assertIn(ruling[key], flat)

    def test_key_never_follows_a_redirect_to_another_site(self) -> None:
        from engine import net
        import urllib.request
        handler = net._SameSiteRedirect()
        req = urllib.request.Request("https://console.nace.ai/v1/systemone", headers={"Authorization": "Bearer x"})
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, "https://example.com/v1/systemone")
        self.assertIsNotNone(handler.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, "https://drex.nace.ai/v1/systemone"))

    def test_api_host_must_be_nace(self) -> None:
        with mock.patch.dict(os.environ, {"NACE_API_BASE": "https://example.com"}):
            with self.assertRaises(ValueError):
                config._nace_base()
        with mock.patch.dict(os.environ, {"NACE_API_BASE": "https://drex.nace.ai/"}):
            self.assertEqual(config._nace_base(), "https://drex.nace.ai")

    def test_estimate_counts_equal_real_run(self) -> None:
        for cid in CASE_IDS:
            with self.subTest(case=cid):
                est, real = trial.estimate(cid), self.run_case(cid)["summary"]["totals"]
                self.assertEqual((est["requests"], est["decisions"], est["tokens"]),
                                 (real["requests"], real["decisions"], real["tokens"]))
                self.assertAlmostEqual(est["usd_billed"], est["tokens_billed"] * 0.12 / 1e6, places=12)

    def test_run_files_parse_and_hold_no_key(self) -> None:
        res = self.run_case("ca-deposit")
        run = Path(res["run_dir"])
        lines = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x) for x in lines], res["events"])
        summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
        for key in ("verdict", "vote", "mean", "spread", "totals", "models", "facts", "model_answered"):
            self.assertIn(key, summary)
        reqs = sorted((run / "requests").glob("req_*.json"))
        self.assertEqual(len(reqs), summary["totals"]["requests"])
        for f in reqs:
            self.assertNotIn("authorization", f.read_text(encoding="utf-8").lower())


class TestReplay(OfflineCase):
    def test_replay_returns_the_same_answer(self) -> None:
        res = self.run_case("ca-deposit")
        picked = {}
        for e in res["events"]:
            if e["src"] == "drex":
                picked.setdefault(e["type"], e)
        picked.setdefault("unstable", next((e for e in res["events"] if e["src"] == "drex" and not e["stable"]), None))
        for kind, ev in picked.items():
            if ev is None:
                continue
            with self.subTest(kind=kind, index=ev["i"]):
                out = replay.replay(res["run_dir"], ev["i"])
                self.assertTrue(out["same"], out)

    def test_replay_refuses_a_non_decision(self) -> None:
        res = self.run_case("ca-deposit")
        with self.assertRaises(ValueError):
            replay.replay(res["run_dir"], 0)


class TestDrexClient(OfflineCase):
    def test_choice_gets_an_order_stability_request(self) -> None:
        s = drex.Session(simulate=True)
        rec = s.decide("state", {"c": {"type": "choice", "instructions": "Pick", "options": ["a", "b", "c"]}})["c"]
        self.assertEqual((s.requests, rec["req"], rec["req2"]), (2, "req_001", "req_002"))

    def test_caps_abort(self) -> None:
        s = drex.Session(simulate=True, max_requests=1)
        with self.assertRaises(drex.BudgetExceeded):
            s.decide("state", {"c": {"type": "choice", "instructions": "Pick", "options": ["a", "b"]}})

    def test_normalize_reads_several_shapes(self) -> None:
        yn, ch = {"type": "noul", "instructions": "q"}, {"type": "choice", "instructions": "q", "options": ["a", "b"]}
        self.assertEqual(drex.normalize(yn, 0.7), {"p": 0.7})
        self.assertEqual(drex.normalize(yn, {"probability": 0.2}), {"p": 0.2})
        self.assertEqual(drex.normalize(ch, {"b": 3, "a": 1}), {"dist": [0.25, 0.75]})
        self.assertEqual(drex.normalize(ch, {"probabilities": [0.5, 0.5]}), {"dist": [0.5, 0.5]})
        with self.assertRaises(drex.DrexError):
            drex.normalize(ch, "yes")

    def test_real_call_flags_other_model_and_splits_reported_cost(self) -> None:
        reply = {"model": "drex-other", "answers": {"a": {"p": 0.9}, "b": {"p": 0.1}}, "request_id": "r1",
                 "usage": {"input_tokens": 100, "cost": 0.0004}}
        questions = {q: {"type": "noul", "instructions": "x"} for q in "ab"}
        with mock.patch.dict(os.environ, {"NACE_API_KEY": "test-key-not-real"}), \
                mock.patch("engine.drex.net.json_call", return_value=(200, reply, 12)) as call, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            s = drex.Session(simulate=False)
            recs = s.decide("state", questions)
            s.decide("state", questions)
        self.assertEqual(recs["a"]["model_answered"], "drex-other")
        self.assertEqual((recs["a"]["usd"], recs["a"]["cost_raw"], recs["a"]["tok"] + recs["b"]["tok"]), (0.0002, 0.0004, 100))
        self.assertEqual(err.getvalue().count("warning:"), 1)
        self.assertEqual(len(s.warnings), 2)
        self.assertNotIn("test-key-not-real", json.dumps(call.call_args.args[2]))

    def test_simulated_run_cannot_be_real_without_a_key(self) -> None:
        with self.assertRaises(drex.DrexError):
            drex.resolve_simulate(False)


class TestRoles(OfflineCase):
    def test_role_tables_fit_the_five_option_limit(self) -> None:
        self.assertEqual(roles.check(), [])
        for it in roles.intake_items():
            self.assertLessEqual(len(it["options"]), 5)
        self.assertEqual({r["group"] for r in roles.ROLES.values()}, set(range(len(roles.GROUPS))))

    def test_every_case_names_a_known_role_and_has_fixtures_for_its_statutes(self) -> None:
        for c in cases.all_cases():
            self.assertIn(c["practice"], roles.ROLES)
            self.assertIn(c["jurisdiction"], roles.ROLES[c["practice"]]["scope"])
            for st in c["statutes"]:
                self.assertTrue(lawwiki.statute(st["key"])["text"], st["key"])  # fixture: the store is absent in these tests

    def test_role_is_picked_at_intake_in_the_same_request(self) -> None:
        res = self.run_case("ny-goods")
        ev = {e["qid"]: e for e in res["events"] if e["src"] == "drex" and e["id"] in ("practice_group", "practice_area", "asker_role")}
        self.assertEqual(set(ev), {"practice_group", "area_business", "area_people", "area_criminal", "asker_role"})
        self.assertEqual(len({e["req"] for e in ev.values()}), 1)
        role = next(e for e in res["events"] if e["id"] == "role")["result"]
        self.assertEqual((role["id"], role["source"], role["detected"]["id"], role["side"]), ("corporate", "case", "corporate", "Buyer"))

    def test_criminal_role_asks_for_guilt_beyond_a_reasonable_doubt(self) -> None:
        res = self.run_case("tx-theft")
        votes = [e for e in res["events"] if e["id"] == "liable"]
        self.assertTrue(all("guilty beyond a reasonable doubt?" in e["q"] for e in votes))
        self.assertIn(res["summary"]["verdict"]["label"], ("Defendant guilty", "Defendant not guilty", "Split jury"))
        self.assertEqual(res["summary"]["next_steps"]["options"], roles.ROLES["prosecutor"]["next_steps"])
        civil = [e for e in self.run_case("ca-wages")["events"] if e["id"] == "liable"]
        self.assertTrue(all(e["q"].endswith("is the employer liable?") for e in civil))

    def test_role_override_changes_side_and_next_steps(self) -> None:
        res = trial.run_trial("tx-theft", simulate=True, out=self.out, role="criminal_defense")
        self.assertEqual(res["summary"]["role"]["source"], "override")
        self.assertEqual(res["summary"]["next_steps"]["for_side"], "Defendant")
        self.assertEqual(res["summary"]["next_steps"]["options"], roles.ROLES["criminal_defense"]["next_steps"])
        with self.assertRaises(ValueError):
            trial.Trial("tx-theft", simulate=True, write=False, role="tax")

    def test_local_store_is_built_from_snapshots_and_searched_inside_a_role_scope(self) -> None:
        import gzip
        import importlib.util
        snap = self.out / "snapshots" / "FL" / "20260101T000000Z_test"
        snap.mkdir(parents=True)
        recs = [{"type": "code", "jurisdiction": "FL", "abbreviation": "FS", "name": "Florida Statutes", "citation_prefix": "Fla. Stat. §"},
                {"type": "section", "jurisdiction": "FL", "code": "FS", "section": "83.49", "heading": "Deposit money or advance rent",
                 "text": "The landlord shall give the tenant written notice by certified mail of a claim on the security deposit.",
                 "citation": "Fla. Stat. § 83.49", "status": "active", "content_hash": "a" * 64, "xrefs": []},
                {"type": "section", "jurisdiction": "FL", "code": "FS", "section": "812.014", "heading": "Theft",
                 "text": "A person commits theft if he or she knowingly obtains the property of another. A deposit is not required.",
                 "citation": "Fla. Stat. § 812.014", "status": "active", "content_hash": "b" * 64, "xrefs": []},
                {"type": "section", "jurisdiction": "FL", "code": "FS", "section": "83.99", "heading": "Repealed", "text": "deposit",
                 "citation": "Fla. Stat. § 83.99", "status": "repealed", "content_hash": "c" * 64, "xrefs": []}]
        with gzip.open(snap / "records.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in recs))
        (snap / "manifest.json").write_text(json.dumps({"source_version": "test", "units": None, "totals": {"errors": 0}, "records_sha256": "x"}))
        spec = importlib.util.spec_from_file_location("ingest_local", ROOT / "tools" / "ingest_lawwiki_local.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        store = self.out / "store" / "lawwiki.sqlite"
        with mock.patch.object(tool, "SNAPSHOTS", self.out / "snapshots"), mock.patch.object(tool, "STORE", store), \
                mock.patch.object(tool, "ROOT", self.out), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            tool.ingest(["FL", "NY"])  # NY has no snapshot here: reported, not fatal
        with mock.patch.object(config, "LAW_STORE", store):
            self.assertEqual(lawstore.get("FL/FS/83.49")["text"], recs[1]["text"])
            self.assertIsNone(lawstore.get("FL/FS/83.99"))  # repealed sections are not ingested
            self.assertEqual([h["key"] for h in lawstore.search("security deposit notice", "FL", ["FS:83"])], ["FL/FS/83.49"])
            self.assertEqual({h["key"] for h in lawstore.search("deposit", "FL")}, {"FL/FS/83.49", "FL/FS/812.014"})
            self.assertEqual(lawstore.count("FL", ["FS:812"]), 1)
            self.assertEqual(lawwiki.statute("FL/FS/812.014")["citation"], "Fla. Stat. § 812.014")  # store, when no fixture
            res = trial.run_trial("fl-notice", simulate=True, out=self.out)  # the store adds role-scoped candidates
            search = next(e for e in res["events"] if e["src"] == "lawwiki")
            self.assertFalse(search["simulated"])
            self.assertTrue(all(h["key"].startswith("FL/FS/83.") for h in search["result"]))


class TestSupport(OfflineCase):
    def test_lawwiki_fixture_first_and_offline_safe(self) -> None:
        self.assertEqual(lawwiki.statute("CA/CIV/1950.5")["title"], "Tenant Security Deposit Rules")
        with self.assertRaises(ValueError):
            lawwiki.statute("../../etc/passwd")
        with mock.patch("engine.net._open", side_effect=urllib.error.URLError("offline")):
            self.assertIsNone(lawwiki.statute("CA/CIV/99999"))
            self.assertEqual(lawwiki.search("deposit", "CA"), [])

    def test_failed_ndi_call_falls_back_to_stored_fields(self) -> None:
        with mock.patch.dict(os.environ, {"NACE_API_KEY": "test-key"}), \
                mock.patch("engine.ndi._extract", side_effect=drex.DrexError("nace extract exited 1")):
            live = drex.Session(simulate=False, run_dir=None)  # a live session whose document job fails
            doc = ndi.read_document(config.CASE_FILES_DIR / "lease_and_statement.pdf", ["deposit_amount"],
                                    {"fields": [["deposit_amount", "$3,000", 2, 0.99]], "pages": 6}, live)
        self.assertTrue(doc["simulated"])
        self.assertIn("NDI call failed", doc["error"])
        self.assertEqual(doc["fields"][0][1], "$3,000")

    def test_ndi_job_fields_are_read_with_page_and_confidence(self) -> None:
        # The layout of a real Extract job (2026-11-14), cut down to what the engine reads.
        job = {"status": "succeeded", "job_id": "j1", "cost": {"total_usd": "0.012", "lines": [{"operation": "parse", "quantity": 1}]},
               "timing": {"breakdown": {"preparation_ms": 202, "queue_ms": 169, "ndi_processing_ms": 3745, "result_preparation_ms": 178}},
               "result": {"fields": [
                   {"path": "/notice_date", "value": "September 5, 2026", "status": "found", "confidence": 0.89,
                    "citations": [{"location": {"kind": "page_region", "page": 1}, "source_text": "September 5, 2026"}]},
                   {"path": "/claim_amount", "value": "", "status": "not_found", "confidence": 0.0, "citations": []}]}}
        self.assertEqual(ndi.rows_from(job, ["claim_amount", "notice_date"]), [["notice_date", "September 5, 2026", 1, 0.89]])
        with mock.patch.dict(os.environ, {"NACE_API_KEY": "test-key"}), mock.patch("engine.ndi._extract", return_value=job):
            live = drex.Session(simulate=False, run_dir=None)
            doc = ndi.read_document(config.CASE_FILES_DIR / "lease_and_statement.pdf", ["notice_date", "claim_amount"],
                                    {"fields": [["notice_date", "x", 1, 0.9], ["claim_amount", "$1,850", 1, 0.9]], "pages": 1}, live)
        self.assertFalse(doc["simulated"])
        self.assertEqual(doc["fields"], [["notice_date", "September 5, 2026", 1, 0.89], ["claim_amount", "$1,850", None, 0.0]])
        self.assertIn("claim_amount", doc["note"])
        self.assertAlmostEqual(live.ndi_usd, 0.012)

    def test_day_counts_are_computed_in_code_from_the_dates_read(self) -> None:
        rows = [["lease_end_date", "August 1, 2026", 1, 0.9], ["notice_date", "September 5, 2026", 1, 0.8]]
        got = ndi.add_derived(rows, ["lease_end_date", "notice_date", "days_to_notice"])
        self.assertEqual(got[-1], ["days_to_notice", "35", 1, 0.8])
        self.assertEqual(ndi.add_derived(rows[:1], ["lease_end_date", "days_to_notice"]), rows[:1])  # a date missing: no count

    def test_questions_travel_in_the_documented_shape(self) -> None:
        self.assertEqual(drex._wire({"type": "choice", "instructions": "q", "options": ["a", "b"]}),
                         {"type": "choice", "instructions": "q", "criteria": {"a": None, "b": None}})
        self.assertEqual(drex._wire({"type": "score", "instructions": "q", "levels": ["low", "high"]})["criteria"], ["low", "high"])
        self.assertNotIn("criteria", drex._wire({"type": "noul", "instructions": "q"}))
        # answers as the API returns them
        self.assertEqual(drex.normalize({"type": "noul"}, {"type": "noul", "noul": 0.9, "confidence": 0.9}), {"p": 0.9})
        got = drex.normalize({"type": "choice", "options": ["a", "b"]}, {"type": "choice", "choice": "b", "probabilities": {"b": 0.75, "a": 0.25}})
        self.assertEqual(got, {"dist": [0.25, 0.75]})
        got = drex.normalize({"type": "score", "levels": ["low", "mid", "high"]}, {"type": "score", "score": 1.4, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}})
        self.assertEqual(got, {"dist": [0.1, 0.4, 0.5]})

    def test_ndi_without_key_returns_the_case_fields_flagged_simulated(self) -> None:
        c = cases.get_case("ca-deposit")
        doc = ndi.read_document("lease_and_statement.pdf", [f[0] for f in c["fields"]],
                                {"fields": c["fields"], "pages": 6}, drex.Session(simulate=True))
        self.assertTrue(doc["simulated"])
        self.assertEqual(len(doc["fields"]), len(c["fields"]))

    def test_speech_is_canned_without_a_model_and_checked_in_code(self) -> None:
        out = speech.write("Tenant", "Lead with the 21-day deadline", "brief", "Canned text.", simulate=True)
        self.assertEqual((out["text"], out["simulated"], out["via"]), ("Canned text.", True, "canned"))
        self.assertTrue(speech._acceptable("It took 34 days.", "34 days"))
        self.assertFalse(speech._acceptable("It took 99 days.", "34 days"))
        self.assertEqual(speech._clean("<think>x</think>One. Two. Three."), "One. Two.")


class TestPartners(OfflineCase):
    def test_keenable_keeps_only_https_results_and_survives_an_outage(self) -> None:
        reply = (200, {"results": [{"title": "Guide", "url": "https://selfhelp.courts.ca.gov/x", "snippet": "s"},
                                   {"title": "Plain", "url": "http://example.test/x"}, "junk"]}, 12)
        with mock.patch("engine.net.json_call", return_value=reply) as call, mock.patch.object(config, "env_key", return_value=None):
            hits, ms = keenable.search("deposit", 3)      # whatever key this machine's .env holds, the test runs without one
        self.assertEqual(([h["url"] for h in hits], ms), (["https://selfhelp.courts.ca.gov/x"], 12))
        self.assertTrue(call.call_args.args[1].endswith("/search/public"), "no key: the public endpoint")
        self.assertEqual(call.call_args.args[3], {"X-Keenable-Title": config.KEENABLE_TITLE})
        with mock.patch("engine.net.json_call", side_effect=net.NetError("down", 503)):
            self.assertEqual((keenable.search("deposit"), keenable.fetch("https://a.gov/x")), (([], 0), (None, 0)))
        self.assertEqual(keenable.fetch("http://a.gov/x"), (None, 0), "plain http is never fetched")

    def test_official_site_is_decided_by_the_address(self) -> None:
        yes = ["https://selfhelp.courts.ca.gov/a", "https://leg.state.fl.us/statutes", "https://ccb.gov/claimant"]
        no = ["https://caselaw.findlaw.com/x", "http://courts.ca.gov/a", "https://courts.gov.example.com/a", "https://example.com/.gov"]
        self.assertEqual(([keenable.official_host(u) for u in yes], [keenable.official_host(u) for u in no]), ([True] * 3, [False] * 4))

    def test_web_page_enters_the_record_only_after_both_checks(self) -> None:
        hits = [{"title": "Law firm", "url": "https://firm.example.com/x", "snippet": ""},
                {"title": "Court guide", "url": "https://selfhelp.courts.ca.gov/guide", "snippet": ""}]
        page = {"title": "Court guide", "content": "A landlord must return the deposit. " * 80, "url": hits[1]["url"]}

        def run(p_on_point: float, p_instructions: float) -> trial.Trial:
            t = trial.Trial("ca-deposit", simulate=True, write=False)
            t.simulate = False  # the web step only searches in a live run

            def decide(items, state):
                self.assertIn("untrusted", state)
                return {"web_page_on_point": {"p": p_on_point}, "web_page_has_instructions": {"p": p_instructions}}, []

            with mock.patch.object(keenable, "search", return_value=(hits, 5)), mock.patch.object(keenable, "fetch", return_value=(page, 7)) as fetch, \
                    mock.patch.object(t, "_decide", side_effect=decide):
                events = list(t._web())
            self.assertEqual(fetch.call_args.args[0], hits[1]["url"], "the law-firm page is skipped in code")
            self.assertEqual([e["id"] for e in events], ["fetch", "web_check"])
            return t

        kept = run(0.9, 0.02)
        self.assertTrue(kept.web["used"])
        self.assertLessEqual(len(kept.law_text[-1]), config.WEB_CHARS + 200)
        self.assertIn("not law", kept.law_text[-1])
        for off_topic_or_injected in (run(0.2, 0.02), run(0.9, 0.8)):
            self.assertFalse(off_topic_or_injected.web["used"])
            self.assertFalse(any("self-help page" in x for x in off_topic_or_injected.law_text))

    def test_drex_moves_to_the_other_nace_host_on_a_bot_check(self) -> None:
        calls = []

        def call(method, url, body, headers, **kw):
            calls.append(url)
            if url.startswith("https://console.nace.ai"):
                raise net.NetError('HTTP 403 from console: {"error":{"code":"challenge"}}', 403)
            return 200, {"answers": {}}, 9

        with mock.patch.object(config, "NACE_BASE", "https://console.nace.ai"), \
                mock.patch.object(config, "SYSTEMONE_URL", "https://console.nace.ai/v1/systemone"), \
                mock.patch.dict(os.environ, {"NACE_API_KEY": "test-key"}), mock.patch("engine.net.json_call", side_effect=call), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(drex.post({"model": "m"}), ({"answers": {}}, 9))
            self.assertEqual(calls, ["https://console.nace.ai/v1/systemone", "https://drex.nace.ai/v1/systemone"])
            self.assertFalse(config.use_other_nace_host(), "no third host to try")
            with self.assertRaises(net.NetError):  # any other failure is not retried elsewhere
                with mock.patch("engine.net.json_call", side_effect=net.NetError("HTTP 401", 401)):
                    drex.post({"model": "m"})

    def test_mcp_server_lists_tools_and_runs_a_simulated_trial(self) -> None:
        tools = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, set(mcp_server.HANDLERS))
        with mock.patch.object(config, "RUNS_DIR", self.out):
            reply = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                       "params": {"name": "run_trial", "arguments": {"case": "tx-theft", "simulate": True}}})
        body = json.loads(reply["result"]["content"][0]["text"])
        self.assertFalse(reply["result"]["isError"], body)
        self.assertEqual((body["simulated"], body["role"]["finding"], len(body["what_moves_the_vote"])), (True, "guilty", 5))
        bad = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}})
        self.assertEqual(bad["error"]["code"], -32602)
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


class TestIntern(OfflineCase):
    """The Intern path, with tests/fake_intern.py standing in for the `intern` command (Intern itself needs keys)."""

    def setUp(self) -> None:
        super().setUp()
        stub = self.out / "intern"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_intern.py"}" "$@"\n')
        stub.chmod(0o755)
        env = {"TAD_INTERN_BIN": str(stub), "TAD_RUNS_DIR": str(self.out), "FAKE_INTERN_FAIL": ""}
        for p in (mock.patch.dict(os.environ, env), mock.patch.object(config, "RUNS_DIR", self.out),
                  mock.patch.object(intern_runner, "HOME", self.out / "intern-home"),
                  mock.patch.object(drex, "live_base", return_value=config.NACE_HOSTS[1])):
            p.start()
            self.addCleanup(p.stop)

    def test_run_carries_intern_steps_tool_calls_and_the_trial_in_one_trace(self) -> None:
        seen: list[dict] = []
        res = intern_runner.run("Should we charge this as a felony?", None, "tx-theft", on_event=seen.append)
        self.assertEqual((res["status"], res["exit"]), ("ok", 0))
        self.assertIn("jury.run_trial", res["tools"])
        self.assertIn("Vote 12-0", res["reply"])
        ids = [e["id"] for e in seen]
        for wanted in ("intern.start", "intern.system1", "intern.tool", "mcp.call", "verdict", "next_steps", "intern.reply", "intern.end"):
            self.assertIn(wanted, ids)
        self.assertEqual([e["i"] for e in seen], list(range(len(seen))))           # one numbering for the whole trace
        self.assertLess(ids.index("intern.start"), ids.index("verdict"))
        session = self.out / res["session"]
        for name in ("intern.jsonl", "mcp_calls.jsonl", "trial_run_id", "stream.jsonl", "result.json"):
            self.assertTrue((session / name).is_file(), name)
        record = server.run_record("tx-theft")                                       # what the trace ledger loads
        self.assertEqual((record["run_id"], record["harness"], len(record["events"])), (res["run_id"], "intern", len(seen)))
        self.assertEqual(record["reply"], res["reply"])

    def test_config_keeps_the_hosted_server_only_on_the_console_host(self) -> None:
        text = (intern_runner.write_home(config.NACE_HOSTS[1]) / "config.toml").read_text()
        self.assertNotIn("nace_hosted", text)
        self.assertNotIn("__", text.replace("__pycache__", ""))
        self.assertIn('namespace = "jury"', text)
        self.assertIn("TAD_SESSION_DIR", text)
        self.assertNotIn("nace_hosted", (intern_runner.write_home(config.NACE_HOSTS[0]) / "config.toml").read_text())   # opt-in
        with mock.patch.dict(os.environ, {"TAD_INTERN_HOSTED": "1"}):
            self.assertIn("nace_hosted", (intern_runner.write_home(config.NACE_HOSTS[0]) / "config.toml").read_text())
        only = (intern_runner.write_home(config.NACE_HOSTS[0], only_jury=True) / "config.toml").read_text()
        self.assertEqual((only.count("[[capabilities.mcp]]"), 'namespace = "jury"' in only), (1, True))

    def test_a_startup_failure_is_retried_with_the_jury_server_alone(self) -> None:
        seen: list[dict] = []
        with mock.patch.dict(os.environ, {"FAKE_INTERN_FAIL": "config"}):
            res = intern_runner.run("Should we charge this as a felony?", None, "tx-theft", on_event=seen.append)
        self.assertEqual((res["status"], res["exit"]), ("ok", 0))
        self.assertIn("intern.retry", [e["id"] for e in seen])
        self.assertIn("Vote 12-0", res["reply"])

    def test_slack_falls_back_when_intern_cannot_start(self) -> None:
        posts: list[str] = []
        client = mock.Mock()
        client.chat_postMessage.side_effect = lambda channel, thread_ts, text, blocks=None: posts.append(text)
        with mock.patch.object(intern_runner, "ready", return_value=True), mock.patch.object(drex, "resolve_simulate", return_value=True), \
                mock.patch.object(intern_runner, "run", side_effect=intern_runner.InternError("no binary")):
            slack_bridge.handle({"ts": "2.0", "channel": "C1", "text": "tool kits taken from the hardware store"}, client, "x", set())
        self.assertEqual(len(posts), 2)
        self.assertIn("The jury engine ran it directly", posts[-1])
        self.assertIn("Result: Vote", posts[-1])

    def test_intern_events_become_trace_rows_and_keys_are_masked(self) -> None:
        with mock.patch.object(config, "env_key", side_effect=lambda n: "di_secret_value_123" if n == "DEEPINFRA_API_KEY" else None):
            ev = intern_runner.trace_event({"type": "tool_call_start", "turn": 1, "call_id": "c", "name": "mcp__jury__run_trial",
                                            "arguments": {"note": "key di_secret_value_123"}})
        self.assertEqual((ev["id"], ev["tool"], ev["src"]), ("intern.tool", "mcp__jury__run_trial", "intern"))
        self.assertNotIn("di_secret_value_123", json.dumps(ev))
        end = intern_runner.trace_event({"type": "run_end", "reason": "completed", "turns": 2, "final_text": "Done.", "system1": {"asks": 3}})
        self.assertEqual((end["id"], end["reply"], end["reason"]), ("intern.reply", "Done.", "completed"))
        self.assertEqual(intern_runner.trace_event({"type": "run_end", "reason": "turn_budget", "final_text": ""})["id"], "intern.error")
        said = intern_runner.trace_event({"type": "message", "message": {"role": "assistant", "text": "Vote 12-0.", "tool_calls": []}})
        self.assertEqual((said["id"], said["reply"]), ("intern.system2", "Vote 12-0."))
        guard = intern_runner.trace_event({"type": "decision", "purpose": "tool_guard", "questions": 2, "ok": True, "duration_ms": 180})
        self.assertEqual((guard["id"], guard["ms"]), ("intern.system1", 180))
        for left_out in ({"type": "text_delta", "text": "x"}, {"type": "reasoning_delta", "text": "x"}, {"type": "context_budget"},
                         {"type": "message", "message": {"role": "system", "content": "long prompt"}}):
            self.assertIsNone(intern_runner.trace_event(left_out))
        # Event types this code does not know still become rows.
        old = intern_runner.trace_event({"event": "ToolCallStarted", "data": {"tool_name": "jury.run_trial"}})
        self.assertEqual((old["id"], old["tool"]), ("intern.tool", "jury.run_trial"))
        self.assertEqual(intern_runner.trace_event({"type": "final_answer", "content": "Done."})["reply"], "Done.")
        self.assertEqual(intern_runner.trace_event("plain line")["id"], "intern.output")
        self.assertEqual(intern_runner.trace_event({"weird": 1})["id"], "intern.step")

    def test_config_leaves_out_references_to_empty_variables(self) -> None:
        with mock.patch.object(config, "env_key", side_effect=lambda n: None if n == "KEENABLE_API_KEY" else "x"):
            text = (intern_runner.write_home(config.NACE_HOSTS[0]) / "config.toml").read_text()
        self.assertNotIn("KEENABLE_API_KEY", text)       # Intern refuses a server whose config names an unset variable
        self.assertIn('NACE_API_KEY = "NACE_API_KEY"', text)
        self.assertIn('TAD_SESSION_DIR = "TAD_SESSION_DIR"', text)

    def _slack(self, fail: str) -> list[str]:
        posts: list[str] = []
        client = mock.Mock()
        client.chat_postMessage.side_effect = lambda channel, thread_ts, text, blocks=None: posts.append(text)
        with mock.patch.dict(os.environ, {"FAKE_INTERN_FAIL": fail}), mock.patch.object(intern_runner, "ready", return_value=True), \
                mock.patch.object(drex, "resolve_simulate", return_value=True):
            slack_bridge.handle({"ts": "1.0", "channel": "C1", "text": "a man left the hardware store with two tool kits, employer prepaid?"},
                                client, "x", set())
        return posts

    def test_slack_request_goes_through_intern_and_posts_its_answer(self) -> None:
        posts = self._slack("")
        self.assertEqual(len(posts), 2)                                              # acknowledgment, then one summary
        self.assertIn("Intern is running this", posts[0])
        self.assertIn("Result: Vote 12 to 0", posts[-1])
        self.assertIn("jury.run_trial", posts[-1])
        self.assertIn("Intern's answer", posts[-1])
        self.assertLessEqual(len(posts[-1]), slack_bridge.LIMIT)
        self.assertEqual(posts[-1].count(trial.DISCLAIMER), 1)                       # the disclaimer once, as the last line
        self.assertTrue(posts[-1].endswith(f"_{trial.DISCLAIMER}_"))
        st = slack_bridge._new_state()                                                 # a long summary shrinks its longest list
        st.update(result="Result: Vote 12 to 0. Liable. Outcome: x.", law=[f"law {i} " + "x" * 80 for i in range(60)],
                  facts=[f"fact {i}" for i in range(5)], answer="line one\nline two\n" + trial.DISCLAIMER)
        text = slack_bridge.summary(st)
        self.assertLessEqual(len(text), slack_bridge.LIMIT)
        self.assertIn("• and 57 more", text)
        self.assertIn("fact 4", text)
        self.assertIn("> line two", text)
        self.assertEqual(text.count(trial.DISCLAIMER), 1)

    def test_slack_falls_back_to_the_engine_when_intern_stops_before_the_trial(self) -> None:
        posts = self._slack("early")
        self.assertEqual(len(posts), 2)
        self.assertIn("The jury engine ran it directly", posts[-1])
        self.assertIn("Result: Vote", posts[-1])
        self.assertIn("Suggested next step", posts[-1])

    def test_slack_keeps_the_trial_when_intern_stops_after_it(self) -> None:
        posts = self._slack("late")
        self.assertEqual(posts[-1].count("Result: Vote"), 1)                         # the trial is not run twice
        self.assertIn("Intern stopped early", posts[-1])

    def _slack_calls(self, reject_blocks: bool = False) -> list[dict]:
        """The keyword arguments of every chat_postMessage call that went out; with reject_blocks, a call that carries blocks raises."""
        calls: list[dict] = []

        def post(**kw) -> None:
            if reject_blocks and "blocks" in kw:
                raise RuntimeError("invalid_blocks")
            calls.append(kw)
        client = mock.Mock()
        client.chat_postMessage.side_effect = post
        with mock.patch.dict(os.environ, {"FAKE_INTERN_FAIL": ""}), mock.patch.object(intern_runner, "ready", return_value=True), \
                mock.patch.object(drex, "resolve_simulate", return_value=True):
            slack_bridge.handle({"ts": "1.0", "channel": "C1", "text": "a man left the hardware store with two tool kits, employer prepaid?"},
                                client, "x", set())
        return calls

    def _slack_state(self, case_id: str = "tx-theft") -> tuple[dict, str]:
        st = slack_bridge._new_state()
        for e in self.run_case(case_id)["events"]:
            slack_bridge._collect(st, e, case_id)
        return st, slack_bridge.summary(st)

    BUTTON = {"c": "tx-theft", "f": "f1", "l": "Exit video shows the defendant leaving without paying", "on": True, "n": 6, "m": 0.59,
              "d": -0.336, "n0": 12, "m0": 0.926, "w": "guilty", "s": False, "b": [93] * 12, "a": [50] * 12}

    def test_slack_summary_draws_text_bars(self) -> None:
        st, text = self._slack_state()
        self.assertEqual((slack_bridge._bar(0.5), slack_bridge._bar(-1), slack_bridge._bar(2, 4)), ("\u2588" * 6 + "\u2591" * 6, "\u2591" * 12, "\u2588" * 4))
        self.assertEqual(slack_bridge._spark([0, 0.5, 1]), "\u2581\u2585\u2588")
        self.assertRegex(text, rf"• `[\u2588\u2591]{{12}}` \d+ of 12 say {st['word']} \u00b7 after opening")
        self.assertRegex(text, r"• `[\u2588\u2591]{12}` 0\.\d\d \u00b7 <")                                   # a law line: bar, p, link
        self.assertRegex(text, r"• `[\u2588\u2591]{12}` \d+ of 12 \([+-]\d\.\d\d\) \u00b7 if (it were NOT|it WERE) true: ")
        self.assertEqual(len(st["final"]), 12)                                                  # final vote of each juror
        self.assertTrue(all(len(votes) == 12 for votes in st["revote"].values()) and len(st["revote"]) == len(st["what_if_rows"]))

    def test_slack_blocks_carry_one_button_per_what_if_row(self) -> None:
        st, text = self._slack_state()
        bl = slack_bridge.blocks(st, text)
        self.assertLessEqual(len(bl), 50)
        sections = [b for b in bl if b["type"] == "section"]
        self.assertTrue(all(len(b["text"]["text"]) <= 3000 and b["text"]["type"] == "mrkdwn" for b in sections))
        buttons = [b["accessory"] for b in sections if "accessory" in b]
        self.assertEqual(len(buttons), len(st["what_if_rows"]))
        self.assertEqual(len(buttons), 5)
        ids = [b["action_id"] for b in buttons]
        self.assertEqual(len(set(ids)), len(ids))
        for b, row in zip(buttons, st["what_if_rows"]):
            self.assertRegex(b["action_id"], r"^whatif_\d+$")
            self.assertLessEqual(len(b["text"]["text"]), 30)
            self.assertEqual(b["text"]["text"], "What if this were NOT true?" if row["on_now"] else "What if this WERE true?")
            self.assertLessEqual(len(b["value"]), 2000)
            v = slack_bridge._parse_value(b["value"])
            self.assertIsNotNone(v)
            self.assertEqual((len(v["b"]), len(v["a"]), v["f"], v["n"], v["n0"]), (12, 12, row["id"], row["liable_if"], st["base"]["liable"]))
        self.assertEqual(bl[-1], {"type": "context", "elements": [{"type": "mrkdwn", "text": trial.DISCLAIMER}]})
        self.assertNotIn(trial.DISCLAIMER, "".join(b["text"]["text"] for b in sections if "accessory" not in b))   # the footnote carries it once
        self.assertIn("divider", [b["type"] for b in bl])
        self.assertIn("*What if?* Pick one fact.", "".join(b["text"]["text"] for b in sections))
        st["what_if_rows"] = []                                                             # no rows: the text and the footnote only
        self.assertEqual([b["type"] for b in slack_bridge.blocks(st, text)][-1], "context")
        self.assertNotIn("divider", [b["type"] for b in slack_bridge.blocks(st, text)])
        chunks = slack_bridge._chunks("\n\n".join(["a" * 1500, "b" * 1500, "c" * 7000]))     # blank-line packing, hard cut of a long paragraph
        self.assertTrue(all(0 < len(c) <= 2900 for c in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), "a" * 1500 + "b" * 1500 + "c" * 7000)

    def test_what_if_reply_wording_and_numbers(self) -> None:
        text = slack_bridge.what_if_reply(self.BUTTON)
        lines = text.splitlines()
        self.assertEqual(lines[0], "*What if this were not true: exit video shows the defendant leaving without paying?*")
        self.assertEqual(lines[1], "`" + "\u2588" * 6 + "\u2591" * 6 + "` 6 of 12 jurors say guilty (as filed: 12 of 12)")
        self.assertEqual(lines[2], "`" + "\u2588" * 7 + "\u2591" * 5 + "` mean probability 0.59 (as filed: 0.93, -0.34)")
        self.assertEqual(lines[3], "Juror by juror, J01 to J12:")
        self.assertEqual(lines[4:6], ["`" + "\u2588" * 12 + "` as filed", "`" + "\u2585" * 12 + "` with this one fact changed"])
        self.assertTrue(text.endswith(f"nothing else in the file changed. {trial.DISCLAIMER}_"))
        self.assertTrue(lines[-1].startswith("_Drex decided this re-vote during the run: 12 decisions;"))
        self.assertIn("_A simulated re-vote from the same run (no Drex call was made);", slack_bridge.what_if_reply({**self.BUTTON, "s": True}))
        off = slack_bridge.what_if_reply({**self.BUTTON, "on": False, "l": "Store <records> & more", "b": [], "a": []})
        self.assertTrue(off.startswith("*What if this were true: store &lt;records&gt; &amp; more?*"))
        self.assertNotIn("Juror by juror", off)
        self.assertEqual(len(off.splitlines()), 4)

    def _click(self, value, posts: list, seen: set, action_id: str = "whatif_0", **message) -> None:
        client = mock.Mock()
        client.chat_postMessage.side_effect = lambda channel, thread_ts, text: posts.append((channel, thread_ts, text))
        body = {"actions": [{"action_id": action_id, "value": value}], "channel": {"id": "C1"}, "message": {"ts": "5.0", **message}}
        slack_bridge.on_action(body, client, seen)

    def test_a_what_if_click_posts_one_reply_in_the_thread(self) -> None:
        posts: list = []
        seen: set = set()
        value = json.dumps(self.BUTTON, separators=(",", ":"))
        self._click(value, posts, seen, thread_ts="4.0")
        self.assertEqual([(c, t) for c, t, _ in posts], [("C1", "4.0")])
        self.assertEqual(posts[0][2], slack_bridge.what_if_reply(self.BUTTON))
        self._click(value, posts, seen, thread_ts="4.0")                                    # the same button again: nothing
        self.assertEqual(len(posts), 1)
        self._click(value, posts, seen, action_id="whatif_1", thread_ts="4.0")              # another button of that message: answered
        self.assertEqual(len(posts), 2)
        self._click(value, posts, seen, action_id="whatif_2")                               # a message with no thread: its own ts
        self.assertEqual((len(posts), posts[-1][:2]), (3, ("C1", "5.0")))

    def test_a_malformed_what_if_click_gets_the_could_not_be_read_line(self) -> None:
        posts: list = []
        seen: set = set()
        for i, bad in enumerate(("not json", json.dumps({**self.BUTTON, "n": 13}), None)):
            self._click(bad, posts, seen, action_id=f"whatif_{i}")
        self.assertEqual([t for _, _, t in posts], ["That what-if could not be read. Post the question again to run the trial."] * 3)
        for body in (None, {}, {"actions": []}, {"actions": [{}]}, {"actions": [{"value": "x"}], "channel": {}, "message": {"ts": "1.0"}},
                     {"actions": [{"value": "x"}], "channel": {"id": "C1"}}, {"actions": "x", "channel": {"id": "C1"}, "message": {}}):
            with self.subTest(body=body):
                client = mock.Mock()
                slack_bridge.on_action(body, client, set())                                  # never raises, never posts
                client.chat_postMessage.assert_not_called()

    def test_slack_posts_the_summary_as_text_when_the_blocks_are_rejected(self) -> None:
        calls = self._slack_calls()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("blocks", calls[0])                                                # the acknowledgment is text only
        self.assertTrue(calls[1]["text"].endswith(f"_{trial.DISCLAIMER}_") and calls[1]["blocks"][-1]["type"] == "context")
        calls = self._slack_calls(reject_blocks=True)
        self.assertEqual(len(calls), 2)                                                     # acknowledgment, then the summary without blocks
        self.assertNotIn("blocks", calls[1])
        self.assertIn("Result: Vote", calls[1]["text"])
        self.assertTrue(calls[1]["text"].endswith(f"_{trial.DISCLAIMER}_"))
        stopped = mock.Mock()
        with mock.patch.object(slack_bridge, "harness", return_value="engine"), mock.patch.object(trial, "run_trial", side_effect=RuntimeError("x")):
            slack_bridge.handle({"ts": "3.0", "channel": "C1", "text": "tool kits"}, stopped, "x", set())
        self.assertEqual([c.kwargs.keys() for c in stopped.chat_postMessage.call_args_list], [{"channel", "thread_ts", "text"}] * 2)
        self.assertIn("The run stopped: RuntimeError", stopped.chat_postMessage.call_args_list[-1].kwargs["text"])

    def test_a_button_value_is_checked_before_it_is_trusted(self) -> None:
        good = json.dumps(self.BUTTON, separators=(",", ":"))
        self.assertEqual(slack_bridge._parse_value(good), self.BUTTON)
        self.assertEqual(slack_bridge._parse_value(json.dumps({**self.BUTTON, "b": [], "a": []})), {**self.BUTTON, "b": [], "a": []})
        bad = [{**self.BUTTON, "n": "6"}, {**self.BUTTON, "on": 1}, {**self.BUTTON, "n": True}, {**self.BUTTON, "b": [93] * 13},
               {**self.BUTTON, "a": [50] * 11}, {**self.BUTTON, "b": [93] * 11 + [101]}, {**self.BUTTON, "a": [50.5] * 12},
               {**self.BUTTON, "l": "x" * 301}, {**self.BUTTON, "l": ""}, {**self.BUTTON, "n0": 13}, {**self.BUTTON, "m": 1.2},
               {**self.BUTTON, "d": -1.5}, {**self.BUTTON, "extra": 1}, {k: v for k, v in self.BUTTON.items() if k != "w"}]
        for value in bad:
            with self.subTest(value=str(value)[:80]):
                self.assertIsNone(slack_bridge._parse_value(json.dumps(value)))
        for raw in ("not json", "[]", "null", "[" * 1500, json.dumps(self.BUTTON) + " " * 2000, None, 5, b"{}"):
            with self.subTest(raw=str(raw)[:20]):
                self.assertIsNone(slack_bridge._parse_value(raw))
        self.assertIsNotNone(slack_bridge._parse_value(json.dumps({**self.BUTTON, "l": "x" * 300})))




class DocumentPathTest(unittest.TestCase):
    """The MCP caller is a model: it may only name a PDF or an image inside cases/ or the runs folder."""

    def test_run_trial_refuses_files_outside_the_case_and_run_folders(self):
        import os
        from engine import mcp_server
        for path in ("/etc/hosts", os.path.expanduser("~/.ssh/id_rsa"), "/etc/hosts.pdf", "cases/../README.md", "cases/cases.json"):
            with self.assertRaises(ValueError, msg=path):
                mcp_server.run_trial({"case": "tx-theft", "document": path, "simulate": True})


if __name__ == "__main__":
    unittest.main()
