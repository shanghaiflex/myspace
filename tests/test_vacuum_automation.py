"""Без соединения с роботом: границы расписания, безопасный повтор, жизненный цикл уведомлений."""
import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import vacuum_schedule as VS
import vacuum_monitor as VM

MSK = ZoneInfo("Europe/Moscow")
READY = {"state": "charging", "stateText": "на базе", "busy": False, "error": None,
         "dock": {"cleanWater": False, "dirtyWater": False, "dustBag": False, "error": None}}
CLEANING = dict(READY, state="cleaning", busy=True)


class AutomationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for obj, name, value in ((VS, "STATE", self.tmp.name + "/day.json"),
                                 (VM, "STATE", self.tmp.name + "/alerts.json"),
                                 (VS, "ON", True), (VS, "AT", "10:30"),
                                 (VS, "DAYS", "0,1,2,3,4,5,6"), (VS, "MODE", "vac_then_mop"),
                                 (VS, "GRACE", 120), (VS, "CALENDAR", False)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.now = dt.datetime(2026, 10, 22, 10, 30, tzinfo=MSK)
        self.command = patch.object(VS.VC, "command", return_value=CLEANING).start()
        self.addCleanup(patch.stopall)
        p = patch.object(VS, "conflict", side_effect=AssertionError("calendar must not be consulted"))
        p.start()
        self.addCleanup(p.stop)

    def run_at(self, now=None, snapshot=None, **kwargs):
        return VS.run(now=now or self.now, snapshot=snapshot or READY, **kwargs)

    def test_exact_time_and_once_per_day(self):
        self.run_at(self.now - dt.timedelta(seconds=1))
        self.command.assert_not_called()
        self.run_at()
        self.run_at(self.now + dt.timedelta(minutes=1))
        self.command.assert_called_once_with("start", mode="vac_then_mop")

    def test_uses_moscow_even_if_now_is_utc(self):
        self.run_at(self.now.astimezone(dt.timezone.utc))
        self.command.assert_called_once()

    def test_weekends_and_next_day(self):
        for day in (24, 25, 26):
            self.run_at(self.now.replace(day=day))
        self.assertEqual(self.command.call_count, 3)

    def test_no_late_or_disabled_start(self):
        self.run_at(self.now + dt.timedelta(hours=2))
        with patch.object(VS, "ON", False):
            self.run_at()
        self.command.assert_not_called()

    def test_dry_run_has_no_effects(self):
        self.run_at(dry=True)
        self.command.assert_not_called()
        self.assertEqual(VS.load_state(), {})
        self.assertEqual(VM.load(), {})

    def test_empty_water_retries_after_refill(self):
        bad = dict(READY, dock={"cleanWater": True, "error": "water_empty"})
        self.run_at(snapshot=bad)
        self.command.assert_not_called()
        self.assertEqual(VS.load_state(), {})
        self.run_at(self.now + dt.timedelta(minutes=1))
        self.command.assert_called_once()

    def test_morning_manual_clean_does_not_cancel_scheduled_clean(self):
        earlier = self.now - dt.timedelta(hours=1)
        self.run_at(snapshot=dict(READY, lastClean=earlier.timestamp(), area=39))
        self.command.assert_called_once()

    def test_already_cleaning_or_finished_since_schedule_is_not_restarted(self):
        self.run_at(snapshot=CLEANING)
        self.command.assert_not_called()
        self.run_at(self.now + dt.timedelta(minutes=70), snapshot=dict(READY, lastClean=self.now.timestamp(), area=39))
        self.command.assert_not_called()

    def test_uncertain_command_is_rechecked_before_retry(self):
        self.command.side_effect = TimeoutError()
        self.run_at()
        self.assertEqual(VS.load_state(), {})
        self.run_at(self.now + dt.timedelta(minutes=1), snapshot=CLEANING)
        self.assertEqual(self.command.call_count, 1)

    def test_command_without_start_confirmation_does_not_mark_day_done(self):
        self.command.return_value = READY
        self.run_at()
        self.assertEqual(VS.load_state(), {})

    def test_tick_outside_window_only_polls_and_updates_alerts(self):
        with patch.object(VS.VC, "state", return_value=READY) as state:
            VS.tick(self.now - dt.timedelta(hours=1))
        state.assert_called_once_with(details=False)
        self.command.assert_not_called()
        self.assertIn("checkedAt", VM.load())

    def test_water_notification_deduplicates_and_returns_after_recurrence(self):
        water = dict(READY, dock={"cleanWater": True, "error": "water_empty"})
        VM.update(water, self.now)
        first = VM.items(self.now)
        self.assertEqual(len(first), 1)
        self.assertIn("чистой воды", first[0]["body"])
        VM.update(water, self.now + dt.timedelta(minutes=1))
        self.assertEqual(first[0]["id"], VM.items(self.now)[0]["id"])
        VM.update(READY, self.now + dt.timedelta(minutes=2))
        self.assertEqual(VM.items(self.now), [])
        VM.update(water, self.now + dt.timedelta(minutes=3))
        self.assertNotEqual(first[0]["id"], VM.items(self.now)[0]["id"])

    def test_other_problem_resolution_does_not_repeat_existing_alert(self):
        VM.update(dict(READY, dock={"cleanWater": True, "dirtyWater": True}), self.now)
        original = VM.load()["problems"]["water"]["id"]
        VM.update(dict(READY, dock={"cleanWater": True}), self.now + dt.timedelta(minutes=1))
        self.assertEqual(VM.items(self.now)[0]["id"], original)

    def test_all_reported_faults_have_alerts_including_unknown_codes(self):
        for code in VM.DOCK_ERRORS:
            self.assertTrue(VM.problems(dict(READY, dock={"error": code})))
        for field in ("cleanWater", "dirtyWater", "dustBag"):
            self.assertTrue(VM.problems(dict(READY, dock={field: True})))
        self.assertTrue(VM.problems(dict(READY, dock={"error": "future_error"})))
        self.assertTrue(VM.problems(dict(READY, error="wheel_blocked")))
        self.assertTrue(VM.problems(dict(READY, state="charging_problem")))

    def test_different_dock_error_gets_a_new_notification(self):
        VM.update(dict(READY, dock={"error": "auto_empty_dock_fan_error"}), self.now)
        first = VM.items(self.now)[0]["id"]
        VM.update(dict(READY, dock={"error": "duct_blockage"}), self.now + dt.timedelta(minutes=1))
        self.assertNotEqual(first, VM.items(self.now)[0]["id"])

    def test_daily_connection_failure_recovery_and_staleness(self):
        VM.update(now=self.now)
        self.assertEqual(len(VM.items(self.now)), 1)
        VM.update(READY, self.now + dt.timedelta(minutes=11))
        self.assertEqual(VM.items(self.now + dt.timedelta(minutes=11)), [])
        VM.update(dict(READY, error="wheel_blocked"), self.now + dt.timedelta(minutes=12))
        self.assertEqual(len(VM.items(self.now + dt.timedelta(minutes=42))), 1)
        self.assertEqual(VM.items(self.now + dt.timedelta(minutes=133)), [])

    def test_precheck_at_ten_does_not_start_robot(self):
        with patch.object(VS.VC, "state", return_value=READY) as state:
            VS.tick(self.now.replace(minute=0))
        state.assert_called_once_with(details=False)
        self.command.assert_not_called()

    def test_failed_poll_preserves_known_water_problem(self):
        VM.update(dict(READY, dock={"cleanWater": True}), self.now)
        original = VM.items(self.now)[0]["id"]
        VM.update(now=self.now + dt.timedelta(minutes=1))
        self.assertEqual(VM.items(self.now)[0]["id"], original)


if __name__ == "__main__":
    unittest.main()
