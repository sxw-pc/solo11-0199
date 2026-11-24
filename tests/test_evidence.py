import json

from cinema.formats import instant
from cinema.journal import Snapshot, audit, watermark
from cinema.reconstruction import record_run, replay_run

from .support import LedgerCase


class ReceiptTests(LedgerCase):
    def test_retry_and_out_of_order_corrections_keep_first_source(self):
        first = self.changed_json("player", "p-ridge", rev=3)
        self.fixture("player.jsonl")
        repeated = self.changed_json("player", "p-ridge", rev=3)
        self.assertTrue(repeated["duplicate"])
        alias = self.changed_json("player", "p-ridge", rev=3, end="2026-11-10T10:30:00Z")
        self.assertEqual(alias["new_revisions"], 0)
        row = next(row for row in Snapshot(self.db).active("player") if row["id"] == "p-ridge")
        self.assertEqual((row["rev"], row["ref"]["receipt"]), (3, first["receipt"]))
        self.assertEqual(audit(self.db)["rows"], 5)

    def test_conflicting_file_rolls_back_receipt_and_preceding_good_row(self):
        self.fixture("player.jsonl")
        player = Snapshot(self.db).active("player")[0]
        template = {"id": "new", "rev": 1, "state": "active", "start": "2026-11-11T10:00:00Z",
                    "end": "2026-11-11T10:30:00Z", "session": "s-hub", "device": "unit1",
                    "film": "river", "offset": 0}
        conflict = template | {"id": player["id"], "offset": 1}
        with self.assertRaisesRegex(ValueError, "conflicting revision"):
            self.put("player", "\n".join(map(json.dumps, (template, conflict))))
        self.assertEqual(watermark(self.db), 1)
        self.assertNotIn("new", {item["id"] for item in Snapshot(self.db).active("player")})
        self.assertTrue(audit(self.db)["verified"])

    def test_untrusted_file_rejected_with_line_and_explicit_offset(self):
        self.sample(("sessions.jsonl",))
        with self.assertRaisesRegex(ValueError, "sessions:1: expected an ISO timestamp"):
            self.changed_json("sessions", "s-ridge", rev=2, start="2026-11-10T18:00:00")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.put("sessions", '{"id":"a","id":"b"}')
        self.assertEqual(watermark(self.db), 1)
        self.assertEqual(instant("2026-11-10T18:00:00+08:00"), instant("2026-11-10T10:00:00Z"))

    def test_audit_detects_normalized_tampering(self):
        self.fixture("player.jsonl")
        self.db.execute("UPDATE revision SET body='{}' WHERE identity='p-ridge'")
        with self.assertRaisesRegex(ValueError, "does not match"):
            audit(self.db)


class EvidenceTests(LedgerCase):
    def load_field(self):
        self.sample(("sessions.jsonl", "player.jsonl", "attendance.csv", "power.csv"))

    def run_ridge(self, cutoff=None):
        return self.write(record_run, "s-ridge", cutoff)

    def test_reconstruct_without_coverage_and_replay_at_pinned_cut(self):
        self.load_field()
        before = self.run_ridge()
        report = before["report"]
        self.assertEqual(report["decision"], "proven")
        self.assertEqual(report["playback"]["media_seconds"], 1800)
        self.assertEqual(report["playback"]["confirmed_outage"], [])
        self.assertEqual(len(report["playback"]["advisory_outage"]), 1)
        self.assertEqual(self.run_ridge()["id"], before["id"])
        self.fixture("power-late.csv", "power")
        later = self.run_ridge()
        self.assertEqual(later["report"]["decision"], "insufficient_unique_media")
        self.assertEqual(later["report"]["playback"]["media_seconds"], 1080)
        self.assertEqual(later["report"]["audience"]["named_households"], {})
        self.assertEqual(replay_run(self.db, before["id"])["report"], report)

    def test_same_offset_overlap_dedupes_but_conflicting_player_positions_are_cut(self):
        self.load_field()
        self.changed_json("player", "p-ridge", id="duplicate-claim", start="2026-11-10T18:05:00+08:00",
                          end="2026-11-10T18:15:00+08:00", offset=300)
        agree = self.run_ridge()["report"]
        self.assertEqual(agree["playback"]["media_seconds"], 1800)
        self.changed_json("player", "p-ridge", id="duplicate-claim", rev=2, start="2026-11-10T18:05:00+08:00",
                          end="2026-11-10T18:15:00+08:00", offset=0)
        conflict = self.run_ridge()["report"]
        self.assertEqual(conflict["playback"]["media_seconds"], 1200)
        self.assertEqual(len(conflict["playback"]["player_conflict"]), 1)
        self.assertEqual(conflict["decision"], "insufficient_unique_media")

    def test_repeated_film_section_cannot_substitute_for_missing_reel(self):
        self.load_field()
        self.changed_json("player", "p-ridge", rev=2, end="2026-11-10T18:15:00+08:00")
        self.changed_json("player", "p-ridge", id="rewind", start="2026-11-10T18:15:00+08:00")
        report = self.run_ridge()["report"]
        self.assertEqual((report["playback"]["wall_seconds"], report["playback"]["media_seconds"]), (1800, 900))
        self.assertEqual(report["decision"], "insufficient_unique_media")

    def test_playback_uses_session_clip_and_exact_eighty_percent_cut(self):
        self.load_field()
        self.changed_json("player", "p-ridge", rev=2, start="2026-11-10T17:54:00+08:00")
        exact = self.run_ridge()["report"]
        self.assertEqual((exact["decision"], exact["playback"]["media"]), ("proven", [[360, 1800]]))
        self.changed_json("player", "p-ridge", rev=3, start="2026-11-10T17:53:59+08:00")
        self.assertEqual(self.run_ridge()["report"]["decision"], "insufficient_unique_media")

    def test_manual_outage_needs_distinct_roles_and_observers_in_overlap(self):
        self.load_field()
        self.changed_csv("power", "out-ridge-dispatch", id="electric", channel="electrician")
        self.assertEqual(self.run_ridge()["report"]["playback"]["confirmed_outage"], [])
        self.changed_csv("power", "out-ridge-dispatch", id="electric", rev=2, channel="electrician",
                         observer="electric1", start="2026-11-10T18:12:00+08:00", end="2026-11-10T18:24:00+08:00")
        decision = self.run_ridge()["report"]
        self.assertEqual(decision["playback"]["confirmed_outage"],
                         [[instant("2026-11-10T10:12:00Z"), instant("2026-11-10T10:20:00Z")]])
        self.assertEqual(decision["decision"], "insufficient_unique_media")

    def test_named_short_and_estimated_observations_have_distinct_boundaries(self):
        self.load_field()
        before = self.run_ridge()["report"]["audience"]
        decisions = {item["source"]["id"]: item["decision"] for item in before["observations"]}
        self.assertEqual(set(before["named_households"]), {"h-r1"})
        self.assertEqual(decisions["n-ridge-est"], "estimate_only")
        self.assertEqual(decisions["n-r3-short"], "insufficient_observed_playback")
        self.changed_csv("attendance", "n-r3-short", id="early-r3", start="2026-11-10T18:00:00+08:00",
                         end="2026-11-10T18:09:00+08:00")
        self.assertNotIn("h-r3", self.run_ridge()["report"]["audience"]["named_households"])
        self.changed_csv("attendance", "n-r3-short", rev=2, start="2026-11-10T18:15:00+08:00")
        self.assertIn("h-r3", self.run_ridge()["report"]["audience"]["named_households"])

    def test_device_claim_collision_then_void_recovers_other_session(self):
        self.load_field()
        self.changed_json("sessions", "s-ridge", id="s-shadow", site="hub")
        collision = self.run_ridge()["report"]
        self.assertEqual(collision["decision"], "device_double_booked")
        self.assertEqual(collision["device_collisions"][0]["id"], "s-shadow")
        self.changed_json("sessions", "s-ridge", id="s-shadow", rev=2, state="void", site="hub")
        self.assertEqual(self.run_ridge()["report"]["decision"], "proven")
        self.assertEqual(self.write(record_run, "s-shadow")["report"]["decision"], "void")

    def test_session_replay_checks_persisted_household_findings(self):
        self.load_field()
        frozen = self.run_ridge()
        self.assertEqual(self.db.execute("SELECT decision FROM presence_finding WHERE run=? AND observation='n-r1'",
                                         (frozen["id"],)).fetchone()[0], "named_presence")
        self.db.execute("DELETE FROM presence_finding WHERE run=? AND observation='n-r1'", (frozen["id"],))
        with self.assertRaisesRegex(ValueError, "evidence run replay mismatch"):
            replay_run(self.db, frozen["id"])
