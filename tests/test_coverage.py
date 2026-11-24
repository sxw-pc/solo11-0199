from cinema.demo import CYCLE
from cinema.formats import instant
from cinema.journal import Snapshot
from cinema.scope import ServiceArea
from cinema.settlement import replay, settle

from .support import LedgerCase


class CoverageTests(LedgerCase):
    def test_service_eligibility_and_zero_award_settlement_without_field_imports(self):
        self.sample(("cycles.csv", "targets.csv", "roster.csv", "scope.csv"))
        window = tuple(map(instant, ("2026-11-10T18:00:00+08:00", "2026-11-10T18:30:00+08:00")))
        area = ServiceArea(Snapshot(self.db), CYCLE)
        self.assertEqual(area.eligibility("ridge-hall", "h-r1", window)["decision"], "eligible")
        self.assertEqual(area.eligibility("ridge-hall", "h-p1", window)["decision"], "outside_service_area")
        self.assertEqual(area.eligibility("ridge-hall", "h-new", window)["decision"], "unknown_household")
        self.assertEqual(area.eligibility("ridge-hall", "h-r1", (window[0], area.policy["end"] + 1))["decision"],
                         "outside_cycle")
        coverage = self.write(settle, CYCLE)["coverage"]
        self.assertEqual(coverage["awards"], {})
        self.assertEqual(coverage["villages"]["ridge"]["gap"], 2)

    def test_scope_requires_full_continuous_window_across_agreements(self):
        self.sample()
        self.changed_csv("scope", "ridge-ridge", rev=2, end="2026-11-10T18:12:00+08:00")
        self.changed_csv("scope", "ridge-ridge", id="ridge-extra", start="2026-11-10T18:12:01+08:00")
        partial = self.write(settle, CYCLE)
        attempts = next(row["attendance"] for row in partial["coverage"]["sessions"] if row["session"] == "s-ridge")
        self.assertEqual(attempts[0]["decision"], "outside_service_area")
        self.assertNotIn("h-r1", partial["coverage"]["awards"])
        self.changed_csv("scope", "ridge-ridge", id="ridge-extra", rev=2, start="2026-11-10T18:12:00+08:00")
        closed = self.write(settle, CYCLE)
        self.assertEqual(closed["coverage"]["awards"]["h-r1"]["session"], "s-ridge")
        self.assertEqual(closed["postings"], [{"village": "ridge", "household": "h-r1", "delta": 1}])

    def test_repeat_household_promotes_alternate_proof_without_second_posting(self):
        self.sample()
        before = self.write(settle, CYCLE)
        self.assertEqual(before["coverage"]["awards"]["h-r2"]["session"], "s-hub")
        self.assertEqual(len(before["coverage"]["awards"]["h-r2"]["proof"]["attendance_sources"]), 2)
        alternate = next(row for row in before["coverage"]["sessions"] if row["session"] == "s-repeat")
        self.assertEqual(alternate["attendance"][0]["decision"], "repeat_household")
        self.changed_csv("attendance", "n-r2", rev=2, state="void")
        self.changed_csv("attendance", "n-r2-extra", rev=2, state="void")
        after = self.write(settle, CYCLE)
        self.assertEqual(after["coverage"]["awards"]["h-r2"]["session"], "s-repeat")
        self.assertEqual(after["postings"], [])
        self.changed_csv("attendance", "n-r2-repeat", rev=2, state="void")
        lost = self.write(settle, CYCLE)
        self.assertEqual(lost["postings"], [{"village": "ridge", "household": "h-r2", "delta": -1}])
        self.assertEqual(lost["coverage"]["villages"]["ridge"]["gap"], 1)

    def test_reassigning_home_village_reverses_then_reissues_once(self):
        self.sample()
        before = self.write(settle, CYCLE)
        self.changed_csv("roster", "h-r2", rev=2, village="plain")
        after = self.write(settle, CYCLE)
        self.assertEqual(after["postings"], [{"village": "ridge", "household": "h-r2", "delta": -1},
                                             {"village": "plain", "household": "h-r2", "delta": 1}])
        self.assertEqual(after["coverage"]["villages"]["ridge"]["gap"], 1)
        self.assertTrue(after["coverage"]["villages"]["plain"]["met"])
        self.assertEqual(self.write(settle, CYCLE), after)
        self.assertEqual(replay(self.db, before["id"])["postings"], before["postings"])
        with self.assertRaisesRegex(ValueError, "older cutoff"):
            self.write(settle, CYCLE, before["coverage"]["cutoff"] - 1)

    def test_void_cycle_reverses_prior_entitlements_and_replay_detects_missing_posting(self):
        self.sample()
        before = self.write(settle, CYCLE)
        self.changed_csv("cycles", CYCLE, rev=2, state="void")
        after = self.write(settle, CYCLE)
        self.assertEqual(after["coverage"]["decision"], "cycle_void")
        self.assertEqual({row["household"] for row in after["postings"]}, set(before["coverage"]["awards"]))
        self.assertTrue(all(row["delta"] == -1 for row in after["postings"]))
        self.db.execute("DELETE FROM entitlement_posting WHERE settlement=? AND household='h-r1'", (after["id"],))
        with self.assertRaisesRegex(ValueError, "replay mismatch"):
            replay(self.db, after["id"])
