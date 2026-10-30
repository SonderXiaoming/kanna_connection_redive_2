"""Anonymous entries inferred from the game's recent fighter counts."""

import json
from datetime import datetime, timezone, timedelta


class EntryTracker:
    def __init__(self, period, saved=''):
        self.period = period
        self.bosses = {}
        self.settled = []
        if saved:
            data = json.loads(saved)
            if data.get('period') == period:
                self.bosses = data['bosses']
                self.settled = data.get('settled', [])

    def dump(self):
        return json.dumps({'period': self.period, 'bosses': self.bosses, 'settled': self.settled})

    def settle(self, history):
        key = f'{history.history_id}:{history.viewer_id}:{history.lap_num}:{history.order_num}:{history.create_time}'
        if key in self.settled:
            return
        self.finish(history.order_num, history.lap_num, history.create_time)
        self.settled.append(key)

    def observe(self, boss, lap, count, now):
        if count is None or count < 0:
            return False
        state = self.bosses.setdefault(str(boss), {'lap': lap, 'count': 0, 'entries': []})
        previous = state['count'] if state['lap'] == lap else 0
        added = max(0, count - previous)
        state['entries'].extend([{'lap': lap, 'time': now} for _ in range(added)])
        # A fall in the game's rolling count is not a settlement.
        state.update(lap=lap, count=count)
        return bool(added)

    def finish(self, boss, lap=None, end=None):
        state = self.bosses.get(str(boss), {})
        entries = state.get('entries', [])
        for i, entry in enumerate(entries):
            if (lap is None or entry['lap'] == lap) and (end is None or entry['time'] <= end):
                return entries.pop(i)
        return None

    def active_bosses(self):
        return [int(boss) for boss, state in self.bosses.items() if state['entries']]

    def describe(self, boss, now):
        entries = self.bosses.get(str(boss), {}).get('entries', [])
        if not entries:
            return ''
        lines = [f'{boss}王当前有{len(entries)}人出刀']
        for entry in entries:
            started = datetime.fromtimestamp(entry['time'], timezone(timedelta(hours=8)))
            minutes = max(0, now - entry['time']) / 60
            lines.append(f"进刀时间{started:%H:%M:%S}，已过去{minutes:.1f}分钟")
        return '\n'.join(lines)
