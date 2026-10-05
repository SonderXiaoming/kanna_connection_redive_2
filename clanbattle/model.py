import asyncio
import contextlib
import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List

from loguru import logger

from ..basedata import ItemID, NoticeType, stage_dict
from ..client import (
    BaseClient,
    LoadIndexResponse,
    check_client,
)
from ..client.response import (
    BattleInfo,
    ClanBattleLogListResponse,
    ClanBattleTopResponse,
)
from ..database.dal import pcr_sqla
from ..database.models import RecordDao
from ..errorclass import CancelledError
from ..login import run_group
from ..util.task_pool import PoolBase, PrioritizedQueryItemBase
from ..util.tools import anywhere_send
from ..util.auto_boss import clan_boss_info
from .base import find_item, format_bignum, format_precent
from .damage_correction import DamageCorrectionTracker

from traceback import format_exc


class ClanBattle:
    def __init__(self, group_id: int) -> None:
        self.rank = 0  # 会战排名
        self.lap_num = 0  # 周目（会战周目，boss可能多一周目）
        self.loop_num = 0  # 循环编号
        self.error_count = 0  # 失败计数
        self.loop_check = 0  # 循环检查(时间戳)
        self.group_id = group_id
        self.notice_dao = []
        self.notice_tree = []
        self.notice_fighter = []
        self.notice_subscribe = []
        self.boss = [Boss(), Boss(), Boss(), Boss(), Boss()]
        self.clan_name = ""
        self.period = ""
        self.dao_update_time = 0  # 网页端更新标记
        self.damage_correction = DamageCorrectionTracker()

    async def init(self, client: BaseClient, user_id: int, bot_id: int):
        self.loop_num += 1
        self.client = client
        self.bot_id = bot_id
        self.user_id = user_id
        for _ in range(3):
            with contextlib.suppress(Exception):
                home_index = await self.client.home_index()
                self.clan_id = home_index.user_clan.clan_id
                self.coin = await self.get_coin()
                clan_battle_top = await self.get_clanbattle_top()
                break
        else:
            home_index = await self.client.home_index()
            self.clan_id = home_index.user_clan.clan_id
            self.coin = await self.get_coin()
            clan_battle_top = await self.get_clanbattle_top()

        self.clan_battle_id = clan_battle_top.clan_battle_id
        self.clan_name = clan_battle_top.user_clan.clan_name
        self.lap_num = clan_battle_top.lap_num
        self.period = clan_boss_info.lap2stage(self.lap_num)
        self.refresh_latest_time(clan_battle_top)
        self.damage_correction.reset(clan_battle_top)
        self.members: Dict[int, str] = await self.all_member()
        self.dao_update_time = int(time.time())

    async def get_coin(self) -> int:
        load_index: LoadIndexResponse = await self.client.load_index()
        return find_item(load_index.item_list, ItemID.clanbattle_coin.value)

    async def get_clanbattle_top(self) -> ClanBattleTopResponse:
        try:
            return await self.client.clan_battle_top(self.clan_id, self.coin)
        except Exception:
            self.coin = await self.get_coin()  # 更新coin
            return await self.client.clan_battle_top(self.clan_id, self.coin)

    async def refresh_fighter_num(self, lap_num: int, order: int) -> int:
        boss = self.boss[order - 1]
        try:
            if boss.current_hp:
                reload_detail_info = await self.client.clan_battle_detail_info(
                    self.clan_id, self.clan_battle_id, lap_num, order
                )
                if reload_detail_info.fighter_num != boss.fighter_num:
                    boss.fighter_num = reload_detail_info.fighter_num
                    return reload_detail_info.fighter_num
        except Exception:
            return 0
        return 0  # 0不播报，没改变不报，改变了是0也不报

    async def get_battle_log(self, page: int) -> ClanBattleLogListResponse:
        return await self.client.clan_battle_log(page, self.clan_battle_id)

    async def add_record(self, loop_num: int):
        log_list: List[BattleInfo] = []
        dao_list = []
        with contextlib.suppress(Exception):
            # 故意不重试，这循环极端情况下运行10分钟也正常，不如报错趁早退出，下次再来
            log_temp = await self.get_battle_log(1)  # 获取最大页数
            if not log_temp.battle_list:
                return  # 数据空

            latest_time = await pcr_sqla.get_latest_time(self.group_id)
            existing_keys = await pcr_sqla.get_record_keys_at(self.group_id, latest_time)
            for page in range(log_temp.max_page, 0, -1):
                log = await self.get_battle_log(page)
                if not log.battle_list:
                    continue
                if log.battle_list[-1].battle_end_time < latest_time:
                    break
                log_list += log.battle_list[::-1]
            key_counts = Counter(
                (r.target_viewer_id, r.lap_num, r.order_num, r.battle_end_time)
                for r in log_list
            )
            self.damage_correction.invalidate_keys(
                {key for key, count in key_counts.items() if count > 1}
            )
            for record in log_list[::-1]:
                if loop_num != self.loop_num:
                    raise CancelledError
                time = record.battle_end_time
                key = (record.target_viewer_id, record.lap_num, record.order_num, time)
                if time >= latest_time and key not in existing_keys:
                    record_dao = await self.general_single_record(
                        record, time, allow_damage_correction=key_counts[key] == 1
                    )
                    dao_list.append(record_dao)
        if dao_list:
            await pcr_sqla.add_record(dao_list)

    async def general_single_record(
        self, record: BattleInfo, time: int, allow_damage_correction: bool = True
    ) -> RecordDao:
        pcrid = record.target_viewer_id
        time_line = await self.client.time_line_report(
            pcrid, self.clan_battle_id, record.battle_log_id
        )
        temp_dict = {
            "group_id": self.group_id,
            "battle_log_id": time % 10000000,  # 这玩意居然会重复，受不了，直接取时间
            "name": record.user_name,
            "lap": record.lap_num,
            "boss": record.order_num,
            "damage": (
                self.damage_correction.record_damage(record)
                if allow_damage_correction else record.total_damage
            ),
            "time": time,
            "pcrid": pcrid,
            "remain_time": time_line.start_remain_time,
            "battle_time": time_line.battle_time,
            "flag": (
                1
                if time_line.battle_time < time_line.start_remain_time
                else (0.5 if time_line.start_remain_time < 90 else 0)
            ),
        }
        for i, unit in enumerate(record.units):
            temp_dict[f"unit{i+1}"] = unit.unit_id
            temp_dict[f"unit{i+1}_level"] = unit.unit_level
            temp_dict[f"unit{i+1}_damage"] = unit.damage
            temp_dict[f"unit{i+1}_rarity"] = unit.unit_rarity
            temp_dict[f"unit{i+1}_rank"] = unit.promotion_level
            temp_dict[f"unit{i+1}_unique_equip"] = (
                unit.unique_equip_slot[0].enhancement_level
                if unit.unique_equip_slot
                else 0
            )
        return RecordDao(**temp_dict)

    async def notice_text(self, order: int, lap: int, item: int) -> str:
        if not (info := await pcr_sqla.get_notice(item, self.group_id, order, lap)):
            return ""

        # 清除需通知成员
        await pcr_sqla.delete_notice(item, self.group_id, order, lap=lap)

        if item == NoticeType.apply.value:
            return ""

        notice_users = " ".join([f"[CQ:at,qq={notice.user_id}]" for notice in info])

        if item == NoticeType.subscribe.value:
            return f"{notice_users}\n你们预约的{order}王出现了"
        if item == NoticeType.tree.value:
            return "以下成员将自动下树：\n" + notice_users

    async def send_notice(self, types: List[int]):
        if NoticeType.subscribe.value in types:
            await anywhere_send(
                "\n".join(self.notice_subscribe), self.group_id, self.bot_id
            )
            self.notice_subscribe.clear()
        if NoticeType.fighter.value in types:
            await anywhere_send(
                "\n".join(self.notice_fighter), self.group_id, self.bot_id
            )
            self.notice_fighter.clear()
        if NoticeType.dao.value in types:
            await anywhere_send(
                "\n".join(self.notice_dao[::-1]), self.group_id, self.bot_id
            )
            self.notice_dao.clear()
        if NoticeType.tree.value in types:
            await anywhere_send("\n".join(self.notice_tree), self.group_id, self.bot_id)
            self.notice_tree.clear()

    async def refresh_boss(self, clan_battle_top: ClanBattleTopResponse) -> bool:
        change = False
        # 获取当前血量,当前王数
        for i, boss in enumerate(self.boss):
            current_boss = clan_battle_top.boss_info[i]
            # 通知预约
            if current_boss.current_hp and (
                subscribe_text := await self.notice_text(
                    current_boss.order_num,
                    current_boss.lap_num,
                    NoticeType.subscribe.value,
                )
            ):
                self.notice_subscribe.append(subscribe_text)
                # 查看当前出刀人数
            if fighter_num := await self.refresh_fighter_num(
                current_boss.lap_num, current_boss.order_num
            ):
                self.notice_fighter.append(f"{i+1}王当前有{fighter_num}人出刀")
            if (
                current_boss.current_hp != boss.current_hp
                or current_boss.lap_num != boss.lap_num
            ):
                change = True
                boss.refresh(
                    current_boss.current_hp,
                    current_boss.lap_num,
                    current_boss.order_num,
                    current_boss.max_hp,
                )
        return change

    async def record_change(self, clan_battle_top: ClanBattleTopResponse):
        for history in self.damage_correction.update(clan_battle_top):
            damage = self.damage_correction.history_damage(history)
            correction = (
                f"（原始伤害{history.damage}，已扣除超量伤害）"
                if damage < history.damage else ""
            )
            self.notice_dao.append(
                f"{history.name}对{history.lap_num}周目{history.order_num}王造成了{damage}点伤害。{correction}"
            )
            # 通知挂树，清空申请出刀
            if history.kill:
                if offtree_text := await self.notice_text(
                    history.order_num, 0, NoticeType.tree.value
                ):
                    self.notice_tree.append(offtree_text)
                await self.notice_text(history.order_num, 0, NoticeType.apply.value)

        self.dao_update_time = int(time.time())
        self.refresh_latest_time(clan_battle_top)

    async def reconcile_damage(self):
        # A detailed report can become visible after top was fetched in the
        # previous iteration. Fix that already-saved row as well as new inserts.
        pending = dict(self.damage_correction.pending_caps)
        if pending:
            await pcr_sqla.correct_kill_damage(self.group_id, pending)
            for key, cap in pending.items():
                if self.damage_correction.pending_caps.get(key) == cap:
                    del self.damage_correction.pending_caps[key]

    async def all_member(self):
        clan = await self.client.clan_info(self.clan_id)
        return {player.viewer_id: player.name for player in clan.clan.members}

    def refresh_latest_time(self, clan_battle_top: ClanBattleTopResponse) -> int:
        self.latest_time = (
            clan_battle_top.damage_history[0].create_time
            if clan_battle_top.damage_history
            else 0
        )

    def general_boss(self) -> str:
        return (
            "当前进度："
            + f"{self.period}面{stage_dict[self.period]}阶段\n"
            + "\n".join([boss.boss_info() for boss in self.boss])
        )


class Boss:
    def __init__(self) -> None:
        self.stage = 0
        self.order = 0
        self.max_hp = 0
        self.lap_num = 0
        self.stage_num = 0
        self.current_hp = 0
        self.fighter_num = 0

    def refresh(self, current_hp, lap_num, order, max_hp):
        self.current_hp = current_hp
        self.lap_num = lap_num
        self.order = order
        self.max_hp = max_hp
        self.stage = clan_boss_info.lap2stage(lap_num)
        self.stage_num = stage_dict[self.stage]

    def boss_info(self):
        msg = f"{self.lap_num}周目{self.order}王: "
        if self.current_hp:
            msg += f"HP: {format_bignum(self.current_hp)}/{format_bignum(self.max_hp)} {format_precent(self.current_hp/self.max_hp)}"
            if self.fighter_num:
                msg += f" 当前有{self.fighter_num}人挑战"
        else:
            msg += "无法挑战"
        return msg


@dataclass
class ClanbattleItem:
    clan_info: ClanBattle
    loop_num: int


class PrioritizedQueryItem(PrioritizedQueryItemBase):
    data: ClanbattleItem


class ClanBattlePool(PoolBase):
    async def do_single(self, item: PrioritizedQueryItem):
        clan_info: ClanBattle = item.data.clan_info
        loop_num: int = item.data.loop_num
        async with ClanbattleHandle(clan_info, loop_num):
            if loop_num != clan_info.loop_num:
                raise CancelledError

            clan_info.loop_check = time.time()

            # 初始化
            clan_battle_top = await clan_info.get_clanbattle_top()
            clan_info.lap_num = clan_battle_top.lap_num
            clan_info.rank = clan_battle_top.period_rank

            # 换面提醒
            if clan_battle_top.lap_num:  # 有时候网络不好这个就直接是none了
                if clan_info.period < (
                    temp := clan_boss_info.lap2stage(clan_battle_top.lap_num)
                ):
                    await anywhere_send(
                        f"会战阶段从{clan_info.period}面到了{temp}面，请注意轴的切换喵",
                        clan_info.group_id,
                        clan_info.bot_id,
                    )
                    clan_info.period = temp

                    # 刷新状态，提醒预约
            await clan_info.refresh_boss(clan_battle_top)
            await clan_info.send_notice(
                [NoticeType.subscribe.value, NoticeType.fighter.value]
            )

            # Damage histories can change even when a snapshot's HP does not.
            await clan_info.record_change(clan_battle_top)
            await clan_info.send_notice(
                [NoticeType.dao.value, NoticeType.tree.value]
            )
            await clan_info.add_record(loop_num)
            await clan_info.reconcile_damage()
            clan_info.error_count = 0
        asyncio.create_task(
            self.add_task(
                PrioritizedQueryItem(data=ClanbattleItem(clan_info, loop_num)),
                int(time.time() - clan_info.loop_check),
                True,
                f"会战群{str(clan_info.group_id)}",
            )
        )


class ClanbattleHandle:
    def __init__(self, clan_info: ClanBattle, loop_num: int) -> None:
        self.clan_info = clan_info
        self.loop_num = loop_num

    async def __aenter__(self):
        run_group[self.clan_info.group_id] = self.clan_info.bot_id
        self.clan_info.loop_check = time.time()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return

        self.clan_info.loop_check = 0
        if self.clan_info.group_id in run_group:
            del run_group[self.clan_info.group_id]

        if self.loop_num != self.clan_info.loop_num:
            self.clan_info.dao_update_time = int(time.time())
            await anywhere_send(
                f"#编号HN000{self.loop_num}监控已关闭",
                self.clan_info.group_id,
                self.clan_info.bot_id,
            )
            return

        if not await check_client(self.clan_info.client):
            self.clan_info.dao_update_time = int(time.time())
            await anywhere_send(
                "当前账号被顶号，出刀监控已退出",
                self.clan_info.group_id,
                self.clan_info.bot_id,
            )
            return

        if self.clan_info.error_count > 3:
            self.clan_info.error_count = 0
            self.clan_info.dao_update_time = int(time.time())
            await anywhere_send(
                f"超过最大重试次数，出刀监控已退出,{exc_value}: {traceback}",
                self.clan_info.group_id,
                self.clan_info.bot_id,
            )
            return

        logger.error(
            f"公会战{self.clan_info.user_id}发生错误，{exc_value}: {traceback}"
        )
        print(format_exc())
        self.clan_info.loop_check = time.time()
        self.clan_info.error_count += 1
        run_group[self.clan_info.group_id] = self.clan_info.bot_id
        return True
