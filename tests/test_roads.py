from cinema.formats import instant
from cinema.journal import audit
from cinema.roads import report
from tests.support import LedgerCase


class RoadItineraryTests(LedgerCase):
    def prepare(self):
        self.fixture("slots.csv")
        return self.fixture("road-windows.csv")["receipt"]

    def test_waits_for_each_road_and_reports_original_witness(self):
        self.prepare()
        view = report(self.db, "a-plain", "c-ridge-later")
        self.assertEqual(view["decision"], "arrival_proven")
        self.assertEqual(view["arrive"], instant("2026-11-20T18:39:00+08:00"))
        self.assertEqual([step["road"]["id"] for step in view["path"]], ["west-lane", "ford-lane"])
        self.assertEqual([step["depart"] for step in view["path"]], [instant("2026-11-20T18:31:00+08:00"),
                                                                    instant("2026-11-20T18:34:00+08:00")])
        self.assertTrue(all(step["road"]["receipt"] == view["cutoff"] for step in view["path"]))

    def test_uses_original_road_window_at_historical_receipt_cutoff(self):
        cutoff = self.prepare()
        self.changed_csv("road-windows", "ford-lane", rev=2, state="void")
        current = report(self.db, "a-plain", "c-ridge-later")
        old = report(self.db, "a-plain", "c-ridge-later", cutoff)
        self.assertEqual(current["decision"], "no_road_itinerary")
        self.assertEqual(old["decision"], "arrival_proven")
        self.assertTrue(audit(self.db)["verified"])

    def test_edge_must_remain_passable_until_arrival(self):
        self.prepare()
        self.changed_csv("road-windows", "ford-lane", rev=2, end="2026-11-20T18:38:59+08:00")
        self.assertEqual(report(self.db, "a-plain", "c-ridge-later")["decision"], "no_road_itinerary")
        self.changed_csv("road-windows", "ford-lane", rev=3, end="2026-11-20T18:39:00+08:00")
        self.assertEqual(report(self.db, "a-plain", "c-ridge-later")["decision"], "arrival_proven")

    def test_rejects_bad_source_and_does_not_route_overlapping_slots(self):
        self.prepare()
        self.assertEqual(report(self.db, "a-plain", "b-ridge")["decision"], "no_road_itinerary")
        with self.assertRaisesRegex(ValueError, "unknown active screening booking"):
            report(self.db, "other", "a-plain")
