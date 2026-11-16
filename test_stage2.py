"""
test_stage2.py
Stage-2 tests for the event-risk agent: classification, the announcement parser, the
JSON store, the verdict windows, the gate that applies vetoes, and the walk-forward
event filter (with its explicit gap/volume proxy).

No network: the parser is exercised against an inline HTML fixture and every store
lives in a temporary directory.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

import event_risk
import walkforward

AS_OF = "2026-11-16"
FIXTURE_HTML = """
<table>
  <tr><th>Date</th><th>Symbol</th><th>Company</th><th>Subject</th></tr>
  <tr><td>2026-11-20</td><td><a href="/announcements/1">MEBL</a></td>
      <td>Meezan Bank Limited</td>
      <td>Board of directors meeting to consider financial results</td></tr>
  <tr><td>20/11/2026</td><td><a href="/announcements/2">OGDC</a></td>
      <td>Oil &amp; Gas Development Company</td>
      <td>Book closure for final cash dividend</td></tr>
  <tr><td>no date here</td><td>BADROW</td><td>x</td><td>should be skipped entirely</td></tr>
</table>
"""


def event(symbol, event_type, event_date, headline="Test announcement"):
    return {
        "symbol": symbol, "type": event_type, "event_date": event_date,
        "headline": headline, "source": "test",
    }


class ClassificationTest(unittest.TestCase):
    def test_classifier_maps_announcement_subjects(self):
        cases = {
            "Board of directors meeting to consider the accounts": "RESULTS",
            "Financial results for the year ended June 30, 2026": "RESULTS",
            "Book closure for final dividend": "BOOK_CLOSURE",
            "Right issue at a premium": "RIGHT_ISSUE",
            "Bonus shares announcement": "BONUS_ISSUE",
            "Notice of AGM": "AGM",
            "Trading suspended for non-compliance": "SUSPENSION",
            "Material information under PSX regulations": "MATERIAL_NOTICE",
            "Change of company secretary": "OTHER",
        }
        for headline, expected in cases.items():
            self.assertEqual(event_risk.classify(headline), expected, headline)

    def test_date_parsing_handles_psx_formats(self):
        self.assertEqual(event_risk._parse_date("2026-11-20"), "2026-11-20")
        self.assertEqual(event_risk._parse_date("20/11/2026"), "2026-11-20")
        self.assertEqual(event_risk._parse_date("Nov 20, 2026"), "2026-11-20")
        self.assertIsNone(event_risk._parse_date("pending"))

    def test_parser_extracts_symbols_dates_and_types(self):
        rows = event_risk.parse_announcements(FIXTURE_HTML, source="fixture")
        self.assertEqual(len(rows), 2, f"expected two parseable rows, got {rows}")
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertEqual(by_symbol["MEBL"]["event_date"], "2026-11-20")
        self.assertEqual(by_symbol["MEBL"]["type"], "RESULTS")
        self.assertEqual(by_symbol["OGDC"]["type"], "BOOK_CLOSURE")
        self.assertIn("dividend", by_symbol["OGDC"]["headline"].lower())
        self.assertEqual(by_symbol["MEBL"]["url"], "/announcements/1")

    def test_parser_returns_nothing_on_an_unknown_layout(self):
        self.assertEqual(event_risk.parse_announcements("<div>maintenance</div>"), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "events.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_upsert_dedupes_and_round_trips(self):
        first = event_risk.upsert_events([event("OGDC", "RESULTS", "2026-11-20")], self.path)
        second = event_risk.upsert_events([event("OGDC", "RESULTS", "2026-11-20")], self.path)
        third = event_risk.upsert_events([event("MEBL", "BOOK_CLOSURE", "2026-11-18")], self.path)

        self.assertEqual(first["added"], 1)
        self.assertEqual(second["added"], 0, "an identical row must not be duplicated")
        self.assertEqual(third["total"], 2)
        store = event_risk.load_events(self.path)
        self.assertEqual(len(store["events"]), 2)
        self.assertIsNone(store["error"])

    def test_corrupt_store_is_reported_not_fatal(self):
        with open(self.path, "w") as handle:
            handle.write("{not json")
        store = event_risk.load_events(self.path)
        self.assertEqual(store["events"], [])
        self.assertIn("unreadable", store["error"])

    def test_missing_store_is_empty_without_error(self):
        store = event_risk.load_events(os.path.join(self._tmp.name, "absent.json"))
        self.assertEqual(store["events"], [])
        self.assertIsNone(store["error"])


class VerdictTest(unittest.TestCase):
    def assess(self, *events, symbol="OGDC"):
        return event_risk.assess(symbol, as_of=AS_OF, events=list(events))

    def test_results_within_two_days_blocks(self):
        verdict = self.assess(event("OGDC", "RESULTS", "2026-11-17"))
        self.assertEqual(verdict["verdict"], "BLOCK")
        self.assertEqual(verdict["risk_score"], 100)
        self.assertIn("financial results", verdict["blockers"][0])
        self.assertEqual(verdict["cited"][0]["days_ahead"], 1)

    def test_results_four_days_out_downgrades_to_watch(self):
        verdict = self.assess(event("OGDC", "RESULTS", "2026-11-20"))
        self.assertEqual(verdict["verdict"], "WATCH")
        self.assertTrue(verdict["warnings"])

    def test_results_ten_days_out_is_clear(self):
        verdict = self.assess(event("OGDC", "RESULTS", "2026-11-26"))
        self.assertEqual(verdict["verdict"], "CLEAR")

    def test_session_after_the_event_still_blocks(self):
        verdict = self.assess(event("OGDC", "RESULTS", "2026-11-15"))
        self.assertEqual(verdict["verdict"], "BLOCK", "the gap repricing session is risk too")

    def test_suspension_window_is_wide(self):
        verdict = self.assess(event("OGDC", "SUSPENSION", "2026-12-06"))
        self.assertEqual(verdict["verdict"], "BLOCK")

    def test_agm_never_hard_blocks_but_warns(self):
        verdict = self.assess(event("OGDC", "AGM", "2026-11-17"))
        self.assertEqual(verdict["verdict"], "WATCH")

    def test_other_symbols_do_not_leak(self):
        verdict = self.assess(event("MEBL", "RESULTS", "2026-11-17"))
        self.assertEqual(verdict["verdict"], "CLEAR")
        self.assertEqual(verdict["events_known"], 0)

    def test_empty_store_is_clear_and_says_so(self):
        verdict = self.assess()
        self.assertEqual(verdict["verdict"], "CLEAR")
        self.assertEqual(verdict["cited"], [])


class GateTest(unittest.TestCase):
    @staticmethod
    def setup_dict(state="BUY_NOW"):
        return {"symbol": "OGDC", "state": state, "status": "TRIGGERED", "blockers": [],
                "trigger_note": "Breakout with volume."}

    def test_block_demotes_buy_now_to_avoid(self):
        assessment = event_risk.assess("OGDC", as_of=AS_OF,
                                       events=[event("OGDC", "RESULTS", "2026-11-17")])
        setup = self.setup_dict("BUY_NOW")
        gated = event_risk.apply_event_gate(setup, assessment)

        self.assertEqual(gated["state"], "AVOID")
        self.assertEqual(gated["status"], "WATCHING")
        self.assertEqual(gated["gate_downgraded_from"], "BUY_NOW")
        self.assertIn("event risk", gated["blockers"][-1])
        self.assertIn("Event gate (BLOCK)", gated["trigger_note"])
        self.assertEqual(setup["state"], "BUY_NOW", "the input setup must not be mutated")

    def test_watch_demotes_buy_zone_to_watch(self):
        assessment = event_risk.assess("OGDC", as_of=AS_OF,
                                       events=[event("OGDC", "RESULTS", "2026-11-20")])
        gated = event_risk.apply_event_gate(self.setup_dict("BUY_ZONE"), assessment)
        self.assertEqual(gated["state"], "WATCH")
        self.assertEqual(gated["gate_downgraded_from"], "BUY_ZONE")

    def test_clear_leaves_the_setup_alone(self):
        gated = event_risk.apply_event_gate(self.setup_dict(), {"verdict": "CLEAR"})
        self.assertEqual(gated["state"], "BUY_NOW")
        self.assertNotIn("gate_downgraded_from", gated)
        self.assertEqual(gated["event_risk"]["verdict"], "CLEAR")

    def test_avoid_stays_avoid(self):
        assessment = event_risk.assess("OGDC", as_of=AS_OF,
                                       events=[event("OGDC", "RESULTS", "2026-11-17")])
        gated = event_risk.apply_event_gate(self.setup_dict("AVOID"), assessment)
        self.assertEqual(gated["state"], "AVOID")
        self.assertNotIn("gate_downgraded_from", gated)


class CliTest(unittest.TestCase):
    def test_add_then_check_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.json")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = event_risk.main(["--path", path, "--add", "OGDC", "RESULTS", "2026-11-17",
                                        "--headline", "Board meeting to consider accounts"])
                self.assertEqual(code, 0)
                code = event_risk.main(["--path", path, "--check", "OGDC"])
                self.assertEqual(code, 0)
            output = buffer.getvalue()
            self.assertIn("1 events stored", output)
            self.assertIn("BLOCK", output)
            self.assertIn("financial results", output)

    def test_unknown_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                code = event_risk.main(["--path", os.path.join(tmp, "e.json"),
                                        "--add", "OGDC", "NOT_A_TYPE", "2026-11-17"])
            self.assertEqual(code, 2)


class WalkForwardEventFilterTest(unittest.TestCase):
    def test_proxy_flags_a_large_move_and_ignores_quiet_tape(self):
        quiet = pd.DataFrame({"Open": [100.0] * 40, "High": [100.5] * 40,
                              "Low": [99.5] * 40, "Close": [100.0] * 40,
                              "Volume": [100_000.0] * 40},
                             index=pd.bdate_range(end="2026-11-16", periods=40))
        self.assertFalse(walkforward.gap_event_proxy(quiet, 35))

        spiky = quiet.copy()
        spiky.iloc[33, spiky.columns.get_loc("Close")] = 108.0
        self.assertTrue(walkforward.gap_event_proxy(spiky, 35),
                        "an 8% session inside the lookback window is event risk")

    def test_event_filter_blocks_entries(self):
        import numpy as np

        rng = np.random.default_rng(5)
        closes = list(100 * np.cumprod(1 + rng.normal(0.001, 0.02, 220)))
        index = pd.bdate_range(end="2026-11-16", periods=len(closes))
        frame = pd.DataFrame({
            "Open": closes, "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes], "Close": closes,
            "Volume": [500_000.0] * len(closes),
        }, index=index)

        open_trades, _ = walkforward.signal_trades({"TEST": frame}, mode="swing_trade")
        blocked_trades, _ = walkforward.signal_trades(
            {"TEST": frame}, mode="swing_trade",
            event_filter=lambda symbol, date, df, idx: True,
        )
        self.assertEqual(blocked_trades, [], "a filter that blocks everything must yield no trades")
        self.assertGreaterEqual(len(open_trades), len(blocked_trades))


class GateLogTest(unittest.TestCase):
    def test_only_vetoes_are_logged_and_jsonl_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "gate.jsonl")
            blocked = event_risk.assess("OGDC", as_of=AS_OF,
                                        events=[event("OGDC", "RESULTS", "2026-11-17")])
            cleared = event_risk.assess("MEBL", as_of=AS_OF, events=[])

            self.assertTrue(event_risk.log_gate_decision(blocked, "BUY_NOW", path))
            self.assertFalse(event_risk.log_gate_decision(cleared, "BUY_NOW", path),
                             "CLEAR verdicts must not bloat the forward log")

            with open(path, "a") as handle:
                handle.write("{broken json\n")   # a partial write must be survivable
            rows = event_risk.load_gate_log(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "OGDC")
            self.assertEqual(rows[0]["verdict"], "BLOCK")
            self.assertEqual(rows[0]["original_state"], "BUY_NOW")
            self.assertEqual(rows[0]["event_type"], "RESULTS")


class ImportTest(unittest.TestCase):
    def test_html_import_upserts_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "page.html")
            with open(html_path, "w") as handle:
                handle.write(FIXTURE_HTML)
            store = os.path.join(tmp, "events.json")
            result = event_risk.import_file(html_path, store)
            self.assertEqual(result["added"], 2)
            self.assertEqual(len(event_risk.load_events(store)["events"]), 2)

    def test_json_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "incoming.json")
            with open(source, "w") as handle:
                json.dump({"events": [event("OGDC", "RESULTS", "2026-11-17")]}, handle)
            store = os.path.join(tmp, "store.json")
            result = event_risk.import_file(source, store)
            self.assertEqual(result["added"], 1)

    def test_unreadable_file_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = event_risk.import_file(os.path.join(tmp, "missing.html"),
                                            os.path.join(tmp, "store.json"))
            self.assertIn("cannot read", result["error"])


SCS_PAYLOAD = {
    "d": [
        {
            "company_code": "MEBL", "company_name": "Meezan Bank Limited", "sector_name": "BANK",
            "bm_date": "/Date(1794769200000)/", "bm_quarter_number": "3Q26",
            "bm_eps_quarter": "7.5", "bm_eps_cum": "21.1", "bm_dividend": "40%",
            "bm_bonus": "", "bm_right_per": "",
            "bm_bc_exp": "/Date(1796065200000)/",
            "bm_PDFLink": "https://dps.psx.com.pk/download/document/1.pdf",
        },
        {
            "company_code": "XYZ", "company_name": "Example Ltd", "sector_name": "OTHER",
            "bm_date": "/Date(1794769200000)/", "bm_quarter_number": "1Q27",
            "bm_eps_quarter": "", "bm_dividend": "", "bm_bonus": "", "bm_right_per": "",
            "bm_bc_exp": "", "bm_PDFLink": "",
        },
    ]
}


class ScstradeFeedTest(unittest.TestCase):
    """The working live feed: real board-meeting / book-closure rows as JSON."""

    def test_dotnet_dates_are_converted(self):
        import datetime as dt

        expected = dt.datetime.fromtimestamp(1794769200).date().isoformat()
        self.assertEqual(event_risk._parse_dotnet_date("/Date(1794769200000)/"), expected)
        self.assertEqual(event_risk._parse_dotnet_date("2026-11-20"), "2026-11-20")
        self.assertIsNone(event_risk._parse_dotnet_date(""))

    def test_row_becomes_results_and_ex_dividend_events(self):
        events = event_risk.parse_scstrade_announcements(SCS_PAYLOAD, symbols=["MEBL"])
        self.assertEqual(len(events), 2, events)
        by_type = {event["type"]: event for event in events}
        self.assertIn("RESULTS", by_type)
        self.assertIn("EX_DIVIDEND", by_type)
        results = by_type["RESULTS"]
        self.assertIn("EPS 7.5", results["headline"])
        self.assertIn("dividend 40%", results["headline"])
        self.assertEqual(results["source"], "scstrade")
        self.assertIn("download/document/1.pdf", results["url"])
        self.assertEqual(by_type["EX_DIVIDEND"]["event_date"],
                         event_risk._parse_dotnet_date("/Date(1796065200000)/"))
        self.assertIn("40%", by_type["EX_DIVIDEND"]["headline"])

    def test_symbol_filter_excludes_other_companies(self):
        events = event_risk.parse_scstrade_announcements(SCS_PAYLOAD, symbols=["MEBL"])
        self.assertTrue(all(event["symbol"] == "MEBL" for event in events))

    def test_row_without_details_is_a_board_meeting(self):
        events = event_risk.parse_scstrade_announcements(SCS_PAYLOAD, symbols=["XYZ"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "BOARD_MEETING")

    def test_bonus_and_right_issues_get_their_own_events(self):
        payload = {"d": [{
            "company_code": "OGDC", "bm_date": "/Date(1794769200000)/",
            "bm_quarter_number": "1Q27", "bm_eps_quarter": "5.1",
            "bm_dividend": "", "bm_bonus": "10%", "bm_right_per": "20%",
            "bm_bc_exp": "/Date(1796065200000)/", "bm_PDFLink": "",
        }]}
        types = {event["type"] for event in event_risk.parse_scstrade_announcements(payload)}
        self.assertEqual(types, {"RESULTS", "BONUS_ISSUE", "RIGHT_ISSUE"})

    def test_empty_payload_returns_nothing(self):
        self.assertEqual(event_risk.parse_scstrade_announcements({"d": []}), [])
        self.assertEqual(event_risk.parse_scstrade_announcements({}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)




