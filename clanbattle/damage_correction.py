"""Cap confirmed kills using consecutive game snapshots, never guessed boss HP."""

from collections import OrderedDict


def history_key(history):
    return (
        history.viewer_id, history.lap_num,
        history.order_num, history.create_time,
    )


def history_identity(history):
    # Keep the server's list order: timestamps alone cannot order combined attacks.
    return (history.history_id, history_key(history), history.damage, history.kill)


class DamageCorrectionTracker:
    def __init__(self):
        self.previous_head = None
        self.previous_bosses = {}
        self.previous_valid = False
        self.events = OrderedDict()
        self.pending_caps = {}

    def reset(self, top):
        self.events.clear()
        self.pending_caps.clear()
        for history in top.damage_history or []:
            self.events[history_identity(history)] = (history_key(history), None)
        self._snapshot(top)

    def _snapshot(self, top):
        histories = top.damage_history or []
        self.previous_head = history_identity(histories[0]) if histories else None
        self.previous_bosses = {
            (boss.lap_num, boss.order_num): boss.current_hp
            for boss in top.boss_info or []
            if isinstance(boss.current_hp, int) and boss.current_hp >= 0
        }
        self.previous_valid = (
            top.damage_history is not None
            and bool(top.boss_info)
            and len(self.previous_bosses) == len(top.boss_info)
        )

    def update(self, top):
        histories = top.damage_history or []
        identities = [history_identity(history) for history in histories]
        # The previous newest entry must still be visible. Otherwise the bounded
        # history may have dropped attacks, so this batch cannot establish HP.
        continuous = (
            self.previous_valid and top.damage_history is not None
            and (self.previous_head is None or identities.count(self.previous_head) == 1)
        )
        if continuous and self.previous_head is not None:
            candidates = histories[:identities.index(self.previous_head)]
        else:
            candidates = histories
        new = [h for h in candidates if history_identity(h) not in self.events]
        # A duplicate event or an unseen entry behind the overlap means that the
        # response cannot be replayed unambiguously.
        continuous = continuous and len(set(identities)) == len(identities)
        if self.previous_head is not None and self.previous_head in identities:
            behind = histories[identities.index(self.previous_head) + 1:]
            continuous = continuous and all(history_identity(h) in self.events for h in behind)

        hp = dict(self.previous_bosses) if continuous else {}
        caps = {}
        invalid = set()
        killed = set()
        for history in reversed(new):
            boss_key = (history.lap_num, history.order_num)
            remaining = hp.get(boss_key)
            damage = history.damage
            if (remaining is None or remaining <= 0
                    or not isinstance(damage, int) or damage < 0):
                invalid.add(boss_key)
                continue
            if history.kill:
                # Also remember a cap when the summary was already capped;
                # battle_log_list can still contain an uncapped total_damage.
                if damage < remaining:
                    invalid.add(boss_key)
                else:
                    caps[history_identity(history)] = remaining
                    killed.add(boss_key)
                    hp[boss_key] = 0
            elif damage >= remaining:
                invalid.add(boss_key)
            else:
                hp[boss_key] = remaining - damage

        current = {
            (boss.lap_num, boss.order_num): boss.current_hp
            for boss in top.boss_info or []
        }
        for boss_key in killed:
            lap, order = boss_key
            advanced = any(b.order_num == order and b.lap_num > lap for b in top.boss_info or [])
            if current.get(boss_key) != 0 and not advanced:
                invalid.add(boss_key)

        for history in new:
            identity = history_identity(history)
            cap = caps.get(identity)
            if (history.lap_num, history.order_num) in invalid:
                cap = None
            self.events[identity] = (history_key(history), cap)

        # A battle log has no history_id. Match by player, lap, boss and second,
        # and decline correction if that tuple identifies multiple attacks.
        counts = {}
        for key, cap in self.events.values():
            counts[key] = counts.get(key, 0) + 1
        for history in new:
            key, cap = self.events[history_identity(history)]
            if cap is not None and counts[key] == 1:
                self.pending_caps[key] = cap
        for key in list(self.pending_caps):
            if counts.get(key) != 1:
                del self.pending_caps[key]

        self._snapshot(top)
        # Keep at most a day's history; leave room for delayed detailed reports.
        newest = max((h.create_time for h in histories), default=0)
        for identity in list(self.events):
            if identity[1][3] < newest - 86400:
                del self.events[identity]
        return new

    def cap_for(self, key):
        matches = [cap for event_key, cap in self.events.values() if event_key == key]
        return matches[0] if len(matches) == 1 else None

    def invalidate_keys(self, keys):
        for identity, (key, cap) in list(self.events.items()):
            if key in keys:
                self.events[identity] = (key, None)
        for key in keys:
            self.pending_caps.pop(key, None)

    def history_damage(self, history):
        cap = self.cap_for(history_key(history))
        return min(history.damage, cap) if cap is not None else history.damage

    def record_damage(self, record):
        key = (record.target_viewer_id, record.lap_num,
               record.order_num, record.battle_end_time)
        cap = self.cap_for(key)
        return min(record.total_damage, cap) if cap is not None else record.total_damage
