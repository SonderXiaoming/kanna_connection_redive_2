import asyncio
import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from hoshino.modules.priconne import chara
from hoshino.util import pic2b64
from loguru import logger
from nonebot import MessageSegment
from PIL import Image

from ..client import BaseClient
from ..client.common import UnitDataForView
from ..database.dal import pcr_sqla
from ..database.models import ArenaSetting, GrandDefenceCache
from ..basedata import Platform
from ..errorclass import CancelledError
from ..login import check_client, run_group
from ..util.task_pool import PoolBase, PrioritizedQueryItemBase
from ..util.tools import anywhere_send
from .get_img import render_atk_def_teams, generate_player_rank

from .base import id_str2list

name_cache = {}
arena_web_notifier = None


def set_arena_web_notifier(notifier):
    global arena_web_notifier
    arena_web_notifier = notifier


class Arena:
    def __init__(self, user_id: int) -> None:
        self.loop_num = 0
        self.error_count = 0
        self.loop_check = 0
        self.jjc_rank = 0
        self.grand_rank = 0
        self.jjc_group = 0
        self.grand_group = 0
        self.latest_cache_time = 0
        self.user_id = user_id
        self.account_name = ""
        self.viewer_id = 0

    async def init(
        self,
        client: BaseClient,
        group_id: int,
        bot_id: int,
        platform: int,
        account_name: str = "",
        viewer_id: int = 0,
    ):
        self.loop_num += 1
        self.battle_record = []
        self.client = client
        self.platform = platform
        self.group_id = group_id
        self.bot_id = bot_id
        self.account_name = account_name or self.account_name
        self.viewer_id = viewer_id or self.viewer_id
        await pcr_sqla.init_jjc_setting(ArenaSetting(user_id=self.user_id))
        self.setting = await pcr_sqla.get_jjc_setting(self.user_id)
        await self.refresh_jjc_info()
        await self.refresh_jjc_info(True)

    async def notify(self, title: str, body: str):
        if self.group_id:
            group_body = (
                f"{body}[CQ:at,qq={self.user_id}]" if "排名" in title else body
            )
            await anywhere_send(group_body, self.group_id, self.bot_id)
        if arena_web_notifier:
            try:
                await arena_web_notifier(self.user_id, title, body)
            except Exception as error:
                logger.warning(f"竞技场网页通知发送失败：{error}")

    async def refresh_jjc_info(self, grand=False):
        if grand:
            grand_info = await self.client.grand_arena_info()
            self.grand_rank = grand_info.grand_arena_info.rank
            self.grand_group = grand_info.grand_arena_info.group
        else:
            jjc_info = await self.client.arena_info()
            self.jjc_rank = jjc_info.arena_info.rank
            self.jjc_group = jjc_info.arena_info.group

    async def refresh_cache(self):
        if self.grand_rank >= 200:
            return

        battle_history = await self.client.grand_arena_history()
        if not battle_history.grand_arena_history_list:
            return

        self.latest_cache_time = await pcr_sqla.cache_latest_time(self.user_id)
        temp_list: List[GrandDefenceCache] = []

        for history in battle_history.grand_arena_history_list:
            if self.latest_cache_time >= history.versus_time:
                break
            if history.is_challenge:
                history_detial = await self.client.grand_arena_history_detial(
                    history.log_id
                )
                arena_desk = (
                    history_detial.grand_arena_history_detail.vs_user_grand_arena_deck
                )
                for i, defence_data in enumerate(
                    [arena_desk.first, arena_desk.second, arena_desk.third], 1
                ):
                    if not (
                        defence := self.general_defence_cache(
                            defence_data,
                            history.opponent_user.viewer_id,
                            history.versus_time,
                            i,
                        )
                    ):
                        break
                    temp_list.append(defence)
        self.latest_cache_time = battle_history.grand_arena_history_list[0].versus_time
        await pcr_sqla.add_grand_cache(temp_list)

    def general_defence_cache(
        self,
        defence_data: List[UnitDataForView],
        viewer_id: int,
        versus_time: int,
        rank: int,
    ) -> Union[GrandDefenceCache, None]:
        if not defence_data:
            return None
        return GrandDefenceCache(
            pcrid=viewer_id,
            grand_id=self.grand_group,
            defence=int(
                "".join([str(Arena.format_id(unit.id)) for unit in defence_data])
            ),
            row=rank,
            vs_time=versus_time,
            user_id=self.user_id,
        )

    async def jjc_query_id(self, rank: int, is_grand: bool = False) -> int:
        page = math.ceil(rank / 20)
        rank_list = (
            await self.client.grand_rank(page)
            if is_grand
            else await self.client.arena_rank(page)
        )
        return f"位于排名{rank}的玩家\n{await self.get_player_info(rank_list.ranking[rank - (page - 1) * 20 - 1].viewer_id)}"

    async def get_player_info(self, pcr_id: int):
        player = await self.client.profile_get(pcr_id)
        return f"{await chara.fromid(Arena.format_id(player.favorite_unit.id), player.favorite_unit.unit_rarity).get_icon_cqcode()}\n玩家姓名：{player.user_info.user_name}\nUID：{pcr_id}\n竞技场排名：{player.user_info.arena_rank}({player.user_info.arena_group})/{player.user_info.grand_arena_rank}({player.user_info.grand_arena_group})\n"

    async def jjc_query(self, rank: int) -> Image.Image:
        page = math.ceil(rank / 20)
        rank_list = await self.client.arena_rank(page)
        defence = rank_list.ranking[rank - (page - 1) * 20 - 1].arena_deck
        result = await do_query(
            [unit.id for unit in defence],
            1 if self.platform == Platform.b_id.value else 3,
        )
        return await render_atk_def_teams(
            result,
            [[unit.id // 100 for unit in defence]],
            rank_list.ranking[rank - (page - 1) * 20 - 1].user_name,
            rank_list.ranking[rank - (page - 1) * 20 - 1].rank,
        )

    async def grand_query(self, rank: int) -> List[Image.Image]:
        page = math.ceil(rank / 20)
        rank_list = await self.client.grand_rank(page)
        defence = rank_list.ranking[rank - (page - 1) * 20 - 1].grand_arena_deck
        team_list = await self.get_defence(
            rank_list.ranking[rank - (page - 1) * 20 - 1].viewer_id,
            rank,
            [defence.first, defence.second, defence.third],
        )
        team_list = [team[5:] if len(team) > 5 else team for team in team_list]
        return await self.get_3defences_solution(
            team_list,
            rank_list.ranking[rank - (page - 1) * 20 - 1].user_name,
            rank_list.ranking[rank - (page - 1) * 20 - 1].rank,
        )

    async def get_3defences_solution(
        self, team_list: List[List[int]], user_name: str, rank: int
    ):
        all_query_records = [
            [[None, -100, "placeholder"]] for _ in range(len(team_list))
        ]
        for query_index, team in enumerate(team_list):
            records = await do_query(
                [unit_id * 100 + 1 for unit_id in team],
                1 if self.platform == Platform.b_id.value else 3,
            )

            if not records:
                continue

            if records == ["lossunit"]:
                all_query_records[query_index].append([None, 100, "lossunit"])
                continue

            for record in records:
                record_team = tuple(chara_obj.id for chara_obj in record["atk"])
                all_query_records[query_index].append(
                    [record_team, record["val"], record]
                )
        result = await generate_collision_free_team(all_query_records)
        while len(team_list) < 3:
            team_list.append([1000, 1000, 1000, 1000, 1000])
        return await render_atk_def_teams(result, team_list, user_name, rank)

    async def get_defence(
        self, pcr_id: int, rank: int, defences: List[List[UnitDataForView]]
    ) -> List[List[int]]:
        if rank <= 50:
            i = 0
        elif rank <= 200:
            i = 1
        elif rank <= 500:
            i = 2
        else:
            i = 3
        defences = [
            [Arena.format_id(unit.id) for unit in defence] for defence in defences[:i]
        ]

        for rank in range(i + 1, 3 + 1):
            if not (team := await pcr_sqla.query_grand_cache(pcr_id, rank)):
                break
            defences.append(id_str2list(str(team)))

        return defences

    async def jjc_query_page(
        self, page: int, is_grand: bool = False
    ) -> List[Image.Image]:
        rank_list = (
            await self.client.grand_rank(math.ceil(page / 2))
            if is_grand
            else await self.client.arena_rank(math.ceil(page / 2))
        ).ranking[(1 - page % 2) * 10 : (2 - page % 2) * 10]
        players = [
            await self.client.profile_get(player.viewer_id) for player in rank_list
        ]
        return [
            await generate_player_rank(
                player.user_info.user_name,
                Arena.format_id(player.favorite_unit.id),
                rank_list[i].rank,
                player.user_info.viewer_id,
                rank_list[i].winning_number if is_grand else None,
            )
            for i, player in enumerate(players)
            if player.favorite_unit
        ]

    async def jjc_query_simple(
        self, page: int, is_grand: bool = False
    ) -> List[Image.Image]:
        rank_list = (
            await self.client.grand_rank(page)
            if is_grand
            else await self.client.arena_rank(page)
        ).ranking
        return [
            f"{rank_list[i].rank}：{result.user_info.user_name}（{result.user_info.viewer_id}）"
            for i, player in enumerate(rank_list)
            if (result := await self.get_profile_by_cache(player.viewer_id))
        ]

    async def get_profile_by_cache(self, viewer_id: int):
        now = time.time()
        cache_key = (self.platform, viewer_id)
        if cache_key in name_cache:
            if now - name_cache[cache_key][1] < 3600:
                return name_cache[cache_key][0]
            del name_cache[cache_key]
        if result := await self.client.profile_get(viewer_id):
            name_cache[(self.platform, result.user_info.viewer_id)] = result, now
        return result

    @staticmethod
    def format_id(unit_id: int) -> int:
        return unit_id // 100 if unit_id > 100000 else 1000


@dataclass
class ArenaItem:
    arena_info: Arena
    loop_num: int


class PrioritizedQueryItem(PrioritizedQueryItemBase):
    data: ArenaItem


class ArenaPool(PoolBase):
    async def do_single(self, item: PrioritizedQueryItem):
        arena: Arena = item.data.arena_info
        loop_num: int = item.data.loop_num
        async with ArenaHandle(arena, loop_num):
            if loop_num != arena.loop_num:
                raise CancelledError
            arena.loop_check = time.time()
            if arena.setting.jjc_notice:
                arena_info = await arena.client.arena_info()
                new_rank = arena_info.arena_info.rank
                if arena.jjc_rank and new_rank != arena.jjc_rank:
                    improved = new_rank < arena.jjc_rank
                    await arena.notify(
                        f"竞技场排名{'上升' if improved else '下降'}",
                        f"jjc: {arena.jjc_rank}->{new_rank} [{'△' if improved else '▽'}{abs(new_rank - arena.jjc_rank)}]",
                    )
                arena.jjc_rank = new_rank
            if arena.setting.grand_notice:
                await arena.refresh_cache()
                grand_info = await arena.client.grand_arena_info()
                new_rank = grand_info.grand_arena_info.rank
                if arena.grand_rank and new_rank != arena.grand_rank:
                    improved = new_rank < arena.grand_rank
                    await arena.notify(
                        f"公主竞技场排名{'上升' if improved else '下降'}",
                        f"pjjc: {arena.grand_rank}->{new_rank} [{'△' if improved else '▽'}{abs(new_rank - arena.grand_rank)}]",
                    )
                arena.grand_rank = new_rank

            arena.error_count = 0
        asyncio.create_task(
            self.add_task(
                PrioritizedQueryItem(data=ArenaItem(arena, loop_num)),
                int(time.time() - arena.loop_check),
                True,
                f"竞技场{str(arena.user_id)}",
            )
        )


class ArenaHandle:
    def __init__(self, arena_info: Arena, loop_num: int) -> None:
        self.arena_info = arena_info
        self.loop_num = loop_num

    async def __aenter__(self):
        if self.arena_info.group_id:
            run_group[self.arena_info.group_id] = self.arena_info.bot_id
        self.arena_info.loop_check = time.time()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return

        self.arena_info.loop_check = 0
        if self.arena_info.group_id in run_group:
            del run_group[self.arena_info.group_id]

        if self.loop_num != self.arena_info.loop_num:
            await self.arena_info.notify(
                "竞技场监控已关闭",
                f"#编号HN100{self.loop_num}监控已关闭",
            )
            return

        if not await check_client(self.arena_info.client):
            await self.arena_info.notify(
                "竞技场监控已退出",
                "当前账号被顶号，竞技场监控已退出",
            )
            return

        if self.arena_info.error_count > 3:
            self.arena_info.error_count = 0
            await self.arena_info.notify(
                "竞技场监控异常退出",
                f"超过最大重试次数，竞技场监控已退出：{exc_value}",
            )
            return

        logger.error(
            f"竞技场{self.arena_info.user_id}发生错误，{exc_value}: {traceback}"
        )
        self.arena_info.loop_check = time.time()
        self.arena_info.error_count += 1
        if self.arena_info.group_id:
            run_group[self.arena_info.group_id] = self.arena_info.bot_id
        return True


class AreanUsePool:
    def __init__(self) -> None:
        self.jjc_info: Dict[int, Arena] = {}
        self.jjc_info_group: Dict[int, Arena] = {}

    def generate_arena(self, qq_id: int, group_id: Optional[int] = None) -> Arena:
        if qq_id not in self.jjc_info:
            self.jjc_info[qq_id] = Arena(qq_id)
        if group_id is not None:
            if (
                group_id not in self.jjc_info_group
                or self.jjc_info_group[group_id].user_id != qq_id
            ):
                self.jjc_info_group[group_id] = self.jjc_info[qq_id]
            return self.jjc_info_group[group_id]
        return self.jjc_info[qq_id]

    def get_arena(self, qq_id: int, group_id: Optional[int] = None) -> Optional[Arena]:
        if qq_id in self.jjc_info and self.jjc_info[qq_id].loop_check:
            return self.jjc_info[qq_id]
        elif (
            group_id in self.jjc_info_group and self.jjc_info_group[group_id].loop_check
        ):
            return self.jjc_info_group[group_id]
        return None

    def get_owned_arena(self, qq_id: int, platform: Optional[int] = None) -> Optional[Arena]:
        arena = self.jjc_info.get(qq_id)
        if not arena or not arena.loop_check:
            return None
        if platform is not None and arena.platform != platform:
            return None
        return arena

    def active_arenas(self) -> List[Arena]:
        seen = set()
        result = []
        for arena in list(self.jjc_info.values()) + list(self.jjc_info_group.values()):
            marker = id(arena)
            if marker in seen or not arena.loop_check:
                continue
            seen.add(marker)
            result.append(arena)
        return result

    def delete_arena(
        self, qq_id: int, group_id: Optional[int] = None
    ) -> Optional[Arena]:
        if qq_id in self.jjc_info:
            del self.jjc_info[qq_id]
        if group_id in self.jjc_info_group:
            del self.jjc_info_group[group_id]


arena_manager = AreanUsePool()
