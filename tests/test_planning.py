import sqlite3

from cinema import reservations
from cinema.capacity import conflicts
from cinema.demo import CYCLE, DISPATCH
from cinema.formats import instant
from cinema.reconstruction import record_run, replay_run
from cinema.settlement import replay as replay_settlement, settle

from .support import LedgerCase


class PlanningTests(LedgerCase):
    def propose(self, start=DISPATCH, end=None):
        return self.write(reservations.propose, CYCLE, instant(start), instant(end) if end else None)

    def commit(self, plan):
        return self.write(reservations.commit, plan["id"])

    def test_standalone_appointments_dedupe_and_overbooked_slot_refused(self):
        self.sample(("cycles.csv", "targets.csv", "roster.csv", "scope.csv", "slots.csv", "bookings.csv"))
        self.changed_csv("bookings", "b3", id="duplicate-appointment")
        report = self.propose()["report"]
        self.assertEqual(report["actual_villages"]["ridge"]["credited"], 0)
        self.assertEqual(report["selected"], ["b-ridge"])
        slots = {slot["id"]: slot for slot in report["slots"]}
        self.assertEqual(len(slots["b-ridge"]["households"]), 2)
        self.assertEqual(len(slots["b-ridge"]["requests"]), 3)
        self.assertEqual(slots["d-overbooked"]["decision"], "over_capacity")
        self.changed_csv("bookings", "b7", rev=2, state="void")
        next_plan = self.propose()["report"]
        revised = next(row for row in next_plan["slots"] if row["id"] == "d-overbooked")
        self.assertEqual(revised["decision"], "candidate")

    def test_half_open_turnaround_and_household_conflict(self):
        one = {"id": "one", "site": "hall1", "crew": "crew1", "kit": "kit1", "start": 0, "end": 30,
               "turnaround": 10, "households": {"h1": "ridge"}}
        two = one | {"id": "two", "site": "hall2", "crew": "crew1", "kit": "kit2", "start": 39, "end": 60}
        self.assertEqual(conflicts(one, two), ["crew"])
        self.assertEqual(conflicts(one, two | {"start": 40}), [])
        self.assertEqual(conflicts(one, two | {"start": 29, "crew": "crew2"}), ["household"])

    def test_portfolio_can_prefer_two_complementary_slots_over_larger_single(self):
        self.sample()
        self.fixture("power-late.csv", "power")
        self.changed_csv("slots", "c-ridge-later", rev=2, start="2026-11-20T18:50:00+08:00")
        report = self.propose()["report"]
        self.assertEqual(report["selected"], ["a-plain", "c-ridge-later"])
        self.assertEqual(report["score"], [1, 3, -2])
        self.assertEqual(report["omitted"]["b-ridge"]["conflicts"], {"a-plain": ["crew", "kit"]})
        short = self.propose(end="2026-11-20T18:30:00+08:00")["report"]
        self.assertEqual(short["selected"], ["b-ridge"])
        self.assertEqual(short["score"], [1, 2, -1])

    def test_duplicate_household_across_nonoverlapping_slots_has_no_marginal_credit(self):
        self.sample()
        self.fixture("power-late.csv", "power")
        self.changed_csv("slots", "c-ridge-later", rev=2, start="2026-11-20T18:50:00+08:00")
        self.changed_csv("bookings", "b1", rev=2, state="void")
        self.changed_csv("bookings", "b2", rev=2, state="void")
        report = self.propose()["report"]
        self.assertEqual(report["selected"], ["b-ridge"])
        self.assertEqual(report["omitted"]["c-ridge-later"]["decision"], "no_additional_gap_reduction")
        self.assertEqual(report["forecast"]["ridge"]["credited"], 3)
        self.assertEqual(report["actual_villages"]["ridge"]["credited"], 1)

    def test_competing_plan_must_rebase_and_cancel_releases_without_erasing_history(self):
        self.sample()
        first = self.propose()
        self.assertEqual(self.propose()["id"], first["id"])
        other = self.propose(start="2026-11-19T01:00:00+08:00")
        committed = self.commit(first)
        self.assertEqual(committed["status"], "committed")
        self.assertEqual(self.commit(first), committed)
        with self.assertRaisesRegex(ValueError, "stale proposal"):
            self.commit(other)
        rebased = self.propose()
        self.assertEqual(rebased["report"]["held_baseline"]["plain"]["gap"], 0)
        self.assertEqual(rebased["report"]["selected"], [])
        with self.assertRaisesRegex(ValueError, "empty proposal"):
            self.commit(rebased)
        cancelled = self.write(reservations.cancel, first["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.write(reservations.cancel, first["id"]), cancelled)
        self.assertEqual(self.propose()["report"]["selected"], ["a-plain"])
        historical = reservations.replay(self.db, rebased["id"])
        self.assertTrue(historical["verified"])
        self.assertEqual(historical["report"]["held_slots"][0]["id"], "a-plain")
        self.assertTrue(reservations.replay(self.db, first["id"])["verified"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM reservation WHERE active=1").fetchone()[0], 0)

    def test_hold_stays_physical_after_scope_void_but_forecast_households_drop(self):
        self.sample()
        first = self.propose()
        self.commit(first)
        self.changed_csv("scope", "hub-plain", rev=2, end="2026-11-18T00:00:00+08:00")
        revised = self.propose()["report"]
        self.assertEqual(revised["held_slots"][0]["id"], "a-plain")
        self.assertEqual(revised["held_baseline"]["plain"]["gap"], 1)
        self.assertEqual({item["decision"] for item in revised["held_assessments"]}, {"outside_service_area"})
        slots = {slot["id"]: slot["decision"] for slot in revised["slots"]}
        self.assertEqual(slots["b-ridge"], "held_conflict")

    def test_failed_reservation_materialization_rolls_back_all_selected_slots(self):
        self.sample()
        self.fixture("power-late.csv", "power")
        self.changed_csv("slots", "c-ridge-later", rev=2, start="2026-11-20T18:50:00+08:00")
        plan = self.propose()
        self.assertEqual(plan["report"]["selected"], ["a-plain", "c-ridge-later"])
        self.db.executescript("""
            CREATE TEMP TRIGGER refuse_second BEFORE INSERT ON reservation WHEN NEW.slot='c-ridge-later'
            BEGIN SELECT RAISE(ABORT, 'injected reservation failure'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected reservation failure"):
            self.commit(plan)
        self.assertEqual(self.db.execute("SELECT count(*) FROM reservation").fetchone()[0], 0)
        self.assertEqual(reservations.replay(self.db, plan["id"])["status"], "proposed")
        self.db.execute("DROP TRIGGER refuse_second")
        self.assertEqual(self.commit(plan)["status"], "committed")


class CrossSystemTests(LedgerCase):
    def test_late_power_reverses_household_award_and_reselects_makeup(self):
        self.sample()
        old_evidence = self.write(record_run, "s-ridge")
        before = self.write(settle, CYCLE)
        first = self.write(reservations.propose, CYCLE, instant(DISPATCH))
        self.assertEqual(first["report"]["selected"], ["a-plain"])
        self.fixture("power-late.csv", "power")
        new_evidence = self.write(record_run, "s-ridge")
        after = self.write(settle, CYCLE)
        self.assertEqual(new_evidence["report"]["decision"], "insufficient_unique_media")
        self.assertEqual(after["postings"], [{"village": "ridge", "household": "h-r1", "delta": -1}])
        self.assertEqual(after["coverage"]["villages"]["ridge"]["gap"], 1)
        with self.assertRaisesRegex(ValueError, "stale proposal"):
            self.write(reservations.commit, first["id"])
        next_plan = self.write(reservations.propose, CYCLE, instant(DISPATCH))
        self.assertEqual(next_plan["report"]["selected"], ["b-ridge"])
        self.assertEqual(next_plan["report"]["forecast"]["ridge"]["gap"], 0)
        self.assertEqual(replay_run(self.db, old_evidence["id"])["report"]["decision"], "proven")
        self.assertEqual(replay_settlement(self.db, after["id"])["previous"], before["id"])

    def test_single_attendance_cut_changes_credit_and_route_with_same_playback(self):
        self.sample()
        before = self.write(record_run, "s-ridge")
        posted = self.write(settle, CYCLE)
        self.assertEqual(self.write(reservations.propose, CYCLE, instant(DISPATCH))["report"]["selected"], ["a-plain"])
        self.changed_csv("attendance", "n-r1", rev=2, start="2026-11-10T18:15:01+08:00")
        after = self.write(record_run, "s-ridge")
        self.assertEqual(after["report"]["playback"], before["report"]["playback"])
        self.assertNotIn("h-r1", after["report"]["audience"]["named_households"])
        revised = self.write(settle, CYCLE)
        self.assertEqual(revised["previous"], posted["id"])
        self.assertEqual(revised["postings"][0]["delta"], -1)
        self.assertEqual(self.write(reservations.propose, CYCLE, instant(DISPATCH))["report"]["selected"], ["b-ridge"])

    def test_valid_history_but_expiring_scope_removes_makeup_candidates(self):
        self.sample()
        self.fixture("power-late.csv", "power")
        initially = self.write(settle, CYCLE)
        self.assertEqual(self.write(reservations.propose, CYCLE, instant(DISPATCH))["report"]["selected"], ["b-ridge"])
        self.changed_csv("scope", "ridge-ridge", rev=2, end="2026-11-19T00:00:00+08:00")
        second = self.write(settle, CYCLE)
        self.assertEqual(set(second["coverage"]["awards"]), set(initially["coverage"]["awards"]))
        self.assertEqual(second["postings"], [])
        report = self.write(reservations.propose, CYCLE, instant(DISPATCH))["report"]
        self.assertEqual(report["selected"], ["a-plain"])
        self.assertEqual(report["forecast"]["ridge"]["gap"], 1)
        ridge = next(slot for slot in report["slots"] if slot["id"] == "b-ridge")
        self.assertEqual(ridge["decision"], "no_eligible_bookings")
        self.assertEqual({item["decision"] for item in ridge["requests"]}, {"outside_service_area"})
