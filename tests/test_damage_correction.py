"""Run without a bot or game account: python -m unittest discover -s tests -v."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "damage_correction", ROOT / "clanbattle" / "damage_correction.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Tracker = module.DamageCorrectionTracker


def history(identity, damage, kill=0, viewer=1, timestamp=10, lap=1, boss=1):
    return NS(history_id=identity, viewer_id=viewer, damage=damage, kill=kill,
              create_time=timestamp, lap_num=lap, order_num=boss, name=str(viewer))


def top(hp, histories, lap=1, boss=1):
    return NS(boss_info=[NS(current_hp=hp, lap_num=lap, order_num=boss)],
              damage_history=histories)


def record(damage, viewer=1, timestamp=10, lap=1, boss=1):
    return NS(total_damage=damage, target_viewer_id=viewer,
              battle_end_time=timestamp, lap_num=lap, order_num=boss,
              battle_log_id=12, user_name=str(viewer), units=[])


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = Tracker()
        self.anchor = history(1, 200, timestamp=9)
        self.tracker.reset(top(1000, [self.anchor]))

    def test_combined_attacks_cap_only_kill(self):
        first = history(2, 600, viewer=2)
        kill = history(3, 700, 1)
        self.tracker.update(top(0, [kill, first, self.anchor]))
        self.assertEqual(self.tracker.history_damage(first), 600)
        self.assertEqual(self.tracker.history_damage(kill), 400)
        self.assertEqual(self.tracker.record_damage(record(700)), 400)

    def test_same_second_new_event_and_repeat_poll(self):
        first = history(2, 600, viewer=2)
        self.assertEqual(self.tracker.update(top(400, [first, self.anchor])), [first])
        kill = history(3, 700, 1)
        latest = top(0, [kill, first, self.anchor])
        self.assertEqual(self.tracker.update(latest), [kill])
        self.assertEqual(self.tracker.record_damage(record(700)), 400)
        self.assertEqual(self.tracker.update(latest), [])

    def test_missing_anchor_resync_then_next_kill(self):
        first = history(3, 600, viewer=2)
        self.tracker.update(top(400, [first]))
        kill = history(4, 700, 1)
        self.tracker.update(top(0, [kill, first]))
        self.assertEqual(self.tracker.history_damage(kill), 400)

    def test_missing_history_preserves_raw_kill(self):
        kill = history(3, 1700, 1)
        self.tracker.update(top(0, [kill]))
        self.assertEqual(self.tracker.record_damage(record(1700)), 1700)

    def test_no_kill_preserves_damage(self):
        hit = history(2, 1700)
        self.tracker.update(top(0, [hit, self.anchor]))
        self.assertEqual(self.tracker.history_damage(hit), 1700)

    def test_missing_history_field_cannot_reestablish_continuity(self):
        self.tracker.update(top(400, None))
        kill = history(3, 700, 1)
        self.tracker.update(top(0, [kill]))
        self.assertEqual(self.tracker.record_damage(record(700)), 700)

    def test_verified_initial_empty_history_can_track_first_kill(self):
        self.tracker.reset(top(1000, []))
        kill = history(2, 1700, 1)
        self.tracker.update(top(0, [kill]))
        self.assertEqual(self.tracker.record_damage(record(1700)), 1000)

    def test_already_capped_summary_caps_raw_detailed_report(self):
        kill = history(2, 1000, 1)
        self.tracker.update(top(0, [kill, self.anchor]))
        self.assertEqual(self.tracker.history_damage(kill), 1000)
        self.assertEqual(self.tracker.record_damage(record(1700)), 1000)

    def test_boss_advance_keeps_old_lap_cap(self):
        kill = history(2, 1700, 1)
        self.tracker.update(top(3000, [kill, self.anchor], lap=2))
        self.assertEqual(self.tracker.record_damage(record(1700)), 1000)
        self.assertEqual(self.tracker.record_damage(record(1700, lap=2)), 1700)

    def test_entire_unobserved_lap_is_not_guessed(self):
        kill = history(2, 1700, 1, lap=2)
        self.tracker.update(top(3000, [kill, self.anchor], lap=3))
        self.assertEqual(self.tracker.record_damage(record(1700, lap=2)), 1700)

    def test_ambiguous_same_player_second_preserves_raw(self):
        first = history(2, 600)
        kill = history(3, 700, 1)
        self.tracker.update(top(0, [kill, first, self.anchor]))
        self.assertEqual(self.tracker.record_damage(record(700)), 700)
        self.assertEqual(self.tracker.pending_caps, {})

    def test_inconsistent_kill_snapshot_preserves_raw(self):
        kill = history(2, 1700, 1)
        self.tracker.update(top(200, [kill, self.anchor]))
        self.assertEqual(self.tracker.record_damage(record(1700)), 1700)

    def test_nonkill_overflow_invalidates_following_kill(self):
        first = history(2, 1100, viewer=2)
        kill = history(3, 1700, 1)
        self.tracker.update(top(0, [kill, first, self.anchor]))
        self.assertEqual(self.tracker.record_damage(record(1700)), 1700)

    def test_duplicate_kills_preserve_raw(self):
        first = history(2, 1100, 1, viewer=2)
        kill = history(3, 1700, 1)
        self.tracker.update(top(0, [kill, first, self.anchor]))
        self.assertEqual(self.tracker.history_damage(first), 1100)
        self.assertEqual(self.tracker.record_damage(record(1700)), 1700)

    def test_reset_removes_previous_battle_caps(self):
        kill = history(2, 1700, 1)
        self.tracker.update(top(0, [kill, self.anchor]))
        self.tracker.reset(top(1000, []))
        self.assertEqual(self.tracker.record_damage(record(1700)), 1700)


def load_methods(path, class_name, methods, namespace):
    # Execute the actual integration methods without importing Hoshino's bot
    # startup decorators, account store, or network clients.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    cls.bases = []
    cls.body = [n for n in cls.body if getattr(n, "name", None) in methods]
    isolated = ast.Module(body=[cls], type_ignores=[])
    namespace["__name__"] = "test_integration"
    exec(compile(isolated, str(path), "exec"), namespace)
    return namespace[class_name]


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_second_log_arrival_is_inserted_once(self):
        import contextlib
        from collections import Counter
        from typing import List
        cls = load_methods(ROOT / "clanbattle/model.py", "ClanBattle",
                           {"add_record", "general_single_record"},
                           {"BattleInfo": NS, "RecordDao": NS, "List": List,
                            "contextlib": contextlib, "Counter": Counter})
        existing = {(2, 1, 1, 10)}
        saved = []
        async def latest(*args):
            return 10
        async def keys(*args):
            return set(existing)
        async def add(rows):
            saved.extend(rows)
            existing.update((r.pcrid, r.lap, r.boss, r.time) for r in rows)
        cls.add_record.__globals__["pcr_sqla"] = NS(
            get_latest_time=latest, get_record_keys_at=keys, add_record=add)
        instance = cls()
        instance.group_id = 1
        instance.clan_battle_id = 1
        instance.loop_num = 1
        instance.damage_correction = Tracker()
        anchor = history(1, 200, timestamp=9)
        instance.damage_correction.reset(top(1000, [anchor]))
        kill = history(3, 700, 1)
        first = history(2, 600, viewer=2)
        instance.damage_correction.update(top(0, [kill, first, anchor]))
        async def logs(*args):
            return NS(max_page=1, battle_list=[record(600, viewer=2), record(700)])
        async def timeline(*args):
            return NS(start_remain_time=90, battle_time=30)
        instance.get_battle_log = logs
        instance.client = NS(time_line_report=timeline)
        await instance.add_record(1)
        self.assertEqual([(r.pcrid, r.damage) for r in saved], [(1, 400)])
        await instance.add_record(1)
        self.assertEqual(len(saved), 1)

        # Multiple detailed logs for the same player/boss/second cannot be
        # uniquely matched to the single summary event; keep both raw totals.
        existing.clear()
        saved.clear()
        async def ambiguous_logs(*args):
            return NS(max_page=1, battle_list=[record(700), record(800)])
        instance.get_battle_log = ambiguous_logs
        await instance.add_record(1)
        self.assertEqual([r.damage for r in saved], [700, 800])
        self.assertEqual(instance.damage_correction.pending_caps, {})

    async def test_broadcast_and_saved_record_share_cap(self):
        import time
        cls = load_methods(ROOT / "clanbattle/model.py", "ClanBattle",
                           {"record_change", "general_single_record", "refresh_latest_time"},
                           {"ClanBattleTopResponse": NS, "BattleInfo": NS,
                            "RecordDao": NS, "time": time})
        instance = cls()
        instance.group_id = 1
        instance.clan_battle_id = 1
        instance.notice_dao = []
        instance.notice_tree = []
        instance.damage_correction = Tracker()
        anchor = history(1, 200, timestamp=9)
        instance.damage_correction.reset(top(1000, [anchor]))
        first = history(2, 600, viewer=2)
        kill = history(3, 700, 1)

        # Keep the existing notice side effects independent of this feature.
        class NoticeType:
            tree = NS(value=1)
            apply = NS(value=2)
        instance.record_change.__func__.__globals__["NoticeType"] = NoticeType
        async def notice(*args):
            return ""
        instance.notice_text = notice
        await instance.record_change(top(0, [kill, first, anchor]))
        self.assertIn("造成了400点伤害", instance.notice_dao[0])

        async def timeline(*args):
            return NS(start_remain_time=90, battle_time=30)
        instance.client = NS(time_line_report=timeline)
        saved = await instance.general_single_record(record(700), 10)
        self.assertEqual(saved.damage, 400)
        self.assertEqual(saved.flag, 1)

    async def test_reconcile_saved_rows_in_real_sqlite(self):
        from sqlalchemy import Column, Integer, select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import declarative_base, sessionmaker
        base = declarative_base()
        class Row(base):
            __tablename__ = "recorddao"
            id = Column(Integer, primary_key=True)
            group_id = Column(Integer)
            pcrid = Column(Integer)
            lap = Column(Integer)
            boss = Column(Integer)
            time = Column(Integer)
            damage = Column(Integer)
        cls = load_methods(ROOT / "database/dal.py", "SQALA",
                           {"correct_kill_damage", "get_record_keys_at"},
                           {"RecordDao": Row, "select": select})
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.create_all)
            sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with sessions() as session:
                async with session.begin():
                    session.add_all([
                        Row(id=1, group_id=1, pcrid=1, lap=1, boss=1, time=10, damage=700),
                        Row(id=2, group_id=2, pcrid=1, lap=1, boss=1, time=10, damage=700),
                        Row(id=3, group_id=1, pcrid=2, lap=1, boss=1, time=10, damage=600),
                        Row(id=4, group_id=1, pcrid=3, lap=1, boss=1, time=10, damage=700),
                        Row(id=5, group_id=1, pcrid=3, lap=1, boss=1, time=10, damage=700),
                    ])
            dao = cls()
            dao.async_session = sessions
            self.assertEqual(await dao.get_record_keys_at(2, 10), {(1, 1, 1, 10)})
            caps = {(1, 1, 1, 10): 400, (3, 1, 1, 10): 400}
            await dao.correct_kill_damage(1, caps)
            await dao.correct_kill_damage(1, caps)
            async with sessions() as session:
                rows = (await session.execute(select(Row).order_by(Row.id))).scalars().all()
                self.assertEqual([r.damage for r in rows], [400, 700, 600, 700, 700])
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
