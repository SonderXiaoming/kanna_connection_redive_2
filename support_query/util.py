import base64
import contextlib
import functools
import gzip
import json
import time

import traceback
from typing import Dict, List, Union

import pandas as pd
from hoshino.modules.priconne.chara import fromid
from hoshino.util import pic2b64
from ..client.common import ExtraEquipInfo, SupportUnitSetting, UnitDataForClanMember
from ..database.models import Account, PlayerUnit, SupportUnit
from ..login import query
from ..database.dal import pcr_sqla
from ..basedata import EquipRankExp, FilePath
from ..client.response import (
    ClanBattleSupportUnitList2Response,
    InventoryInfo,
    LoadIndexResponse,
    ClanBattleSupportUnitLight,
    ProfileGetResponse,
    UnitData,
    UserChara,
)
from .create_img import generate_box_img
from nonebot import MessageSegment

unique_equipment_enhance_data = [
    [
        (0, 10),
        (90, 15),
        (240, 25),
        (490, 40),
        (890, 50),
        (1390, 60),
        (1990, 75),
        (2740, 100),
    ],
    [50, 60, 75, 95, 120, 150],
]
ex_equip_enhance_data = [
    (150, 400, 800),
    (150, 400, 800, 1300),
    (800, 1800, 3000, 4400, 6000),  # 后面一样了
]
bonus_dict = {
    "atk": "物理攻击力",
    "crt": "物理暴击率",
    "matk": "魔法攻击力",
    "mcrt": "魔法暴击率",
    "erec": "TP自动回复",
    "hp": "血量",
    "def": "物理防御力",
    "mdef": "魔法防御力",
    "hrec_rate": "回复量上升",
    "erec_rate": "TP上升",
}


def search_target(
    targets: List[int], units: List[Union[PlayerUnit, SupportUnit]]
) -> List[Union[PlayerUnit, SupportUnit]]:
    return (
        units
        if targets[0] == -1
        else [unit for unit in units if unit.unit_id in targets]
    )


async def get_support_list(
    info: str, account: Account
) -> Union[ClanBattleSupportUnitList2Response, LoadIndexResponse]:
    client = await query(account)
    if info == "support_query":
        home_index = await client.home_index()
        return await client.support_unit_list_2(home_index.user_clan.clan_id)
    if info == "self_query":
        return await client.load_index()


async def get_clan_members_info(account: Account) -> List[ProfileGetResponse]:
    client = await query(account)
    home_index = await client.home_index()
    clan_info = await client.clan_info(home_index.user_clan.clan_id)
    return [
        await client.profile_get(member.viewer_id) for member in clan_info.clan.members
    ]


def equip_exp2star(num: int, exp: int, rank: int) -> str:
    if num == 0:
        return "未装备"
    elif rank < 4:  # 没星
        return "已装备"
    elif 4 <= rank < 7:  # 3星
        return next(
            (
                str(i)
                for i in range(3)
                if EquipRankExp.sliver.value[i]
                <= exp
                < EquipRankExp.sliver.value[i + 1]
            ),
            "3",
        )
    elif 7 <= rank < 11:
        for i in range(5):
            if EquipRankExp.golden.value[i] <= exp < EquipRankExp.golden.value[i + 1]:
                return str(i)
    else:  # 以后都一样了
        for i in range(5):
            if EquipRankExp.purple.value[i] <= exp < EquipRankExp.purple.value[i + 1]:
                return str(i)
    return "5"


def ex_equip_exp2star(exp: int, equipment_id: int) -> int:
    if not equipment_id:
        return 0
    rank = equipment_id % 1000 // 100
    rank = 2 if rank > 3 else rank - 1
    exp_list = ex_equip_enhance_data[rank]
    return next(
        (i for i, star_exp in enumerate(exp_list) if exp < star_exp),
        len(exp_list),
    )


def get_unique_equip_level_from_pt(is_have: int, exp: int, slot_num=1) -> int:
    if is_have == 0:
        return -1
    if slot_num == 1:
        unquie_equip = unique_equipment_enhance_data[0]
        if exp <= unquie_equip[0][0]:  # z专武初始1级
            level = 1 + (exp / unquie_equip[0][1])
        elif exp <= unquie_equip[-1][0]:  # 一开始毫无规律，打表
            for index, stage in enumerate(unquie_equip):
                low = stage[0]
                if low < exp <= unquie_equip[index + 1][0]:
                    level = (exp - low) / stage[1] + index * 10
        else:  # 后面每升10级，升一级所需经验值+25，10级为一组，等差数列
            exp -= unquie_equip[-1][0]
            # 等差公式求出经验值多余多少个10级，向下取整
            n = int((-7 + (49 + 4 * exp / 125) ** 0.5) / 2)
            if n <= 7:  # 算出多多少级
                level = 70 + 10 * n + (exp - 875 * n - 125 * (n) ** 2) / (75 + 25 * n)
            else:  # 每次需要250经验值值时不再增长
                n = 7
                level = 70 + 10 * n + (exp - 875 * n - 125 * (n) ** 2) / 250
        return int(level)
    else:  # slot_num == 2
        unquie_equip = unique_equipment_enhance_data[1]
        return next(
            (index for index, stage in enumerate(unquie_equip) if exp <= stage),
            0,
        )


def letter2chinese(bonus_param: dict) -> str:
    bouns = ""
    for letter in list(bonus_param):
        if not bonus_param[letter]:
            continue
        if letter in bonus_dict:
            bouns += f"{bonus_dict[letter]}：{bonus_param[letter]}，"
    return bouns[:-1]


def parse_unit_data(unit_data: UnitData) -> dict:
    unit_info = {
        "unit_id": unit_data.id // 100,
        "rarity": unit_data.unit_rarity,
        "battle_rarity": unit_data.battle_rarity,
        "unique_level": -1,
        "unique_level2": -1,
        "level": unit_data.unit_level,
        "rank": unit_data.promotion_level,
        **{f"cb_ex_equip_{i}": None for i in range(1, 3 + 1)},
        **{f"cb_ex_equip_{i}_level": None for i in range(1, 3 + 1)},
    }

    if unit_data.unique_equip_slot:
        unique_level = get_unique_equip_level_from_pt(
            unit_data.unique_equip_slot[0].is_slot,
            unit_data.unique_equip_slot[0].enhancement_pt,
        )
        unit_info["unique_level"] = unique_level
        if len(unit_data.unique_equip_slot) > 1:
            unique_level2 = get_unique_equip_level_from_pt(
                unit_data.unique_equip_slot[1].is_slot,
                unit_data.unique_equip_slot[1].enhancement_pt,
                slot_num=2,
            )
            unit_info["unique_level2"] = unique_level2

    for equip_id in range(6):
        unit_info[f"equip_{equip_id+1}"] = equip_exp2star(
            unit_data.equip_slot[equip_id].is_slot,
            unit_data.equip_slot[equip_id].enhancement_pt,
            unit_data.promotion_level,
        )

    unit_info["union_burst"] = unit_data.union_burst[0].skill_level
    with contextlib.suppress(IndexError):
        unit_info["main_1"] = unit_data.main_skill[0].skill_level
        unit_info["main_2"] = unit_data.main_skill[1].skill_level
        unit_info["ex"] = unit_data.ex_skill[0].skill_level

    for i, ex_equip in enumerate(unit_data.cb_ex_equip_slot, 1):
        unit_info[f"cb_ex_equip_{i}"] = ex_equip.ex_equipment_id
        unit_info[f"cb_ex_equip_{i}_level"] = ex_equip_exp2star(
            ex_equip.enhancement_pt, ex_equip.ex_equipment_id
        )
    return unit_info


async def save_support_units(
    support_units: List[Union[ClanBattleSupportUnitLight, UnitData]],
    gid: int,
    name: str,
    viewer_id: int,
):
    temp_units = []
    for unit in support_units:
        if isinstance(unit, ClanBattleSupportUnitLight):
            unit_data = unit.unit_data
            unit_info = {
                "pcrid": unit.owner_viewer_id,
                "name": unit.owner_name,
            }
        else:
            unit_data = unit
            unit_info = {"pcrid": viewer_id, "name": name}
        unit_info.update(parse_unit_data(unit_data))
        unit_info.update(
            {
                "group_id": gid,
                "special_attribute": (
                    letter2chinese(unit_data.bonus_param.dict())
                    if unit_data.bonus_param
                    else "刷新者本人，不显示加成"
                ),
            }
        )
        temp_units.append(SupportUnit(**unit_info))
    await pcr_sqla.refresh_support_units(temp_units, gid)


async def save_player_units(
    units: List[UnitData],
    loves: List[UserChara],
    user_ex_equip: List[ExtraEquipInfo],
    qid: int,
    name: str,
    viewer_id: int,
    friend_support_list: List[SupportUnitSetting],
    support_list: List[UnitDataForClanMember],
):
    temp_units = []
    love_dict = {love.chara_id: love.love_level for love in loves}
    friend_support_ids = {
        support.unit_id: support.position for support in friend_support_list
    }
    clan_supposrt_ids = {support.unit_id: support.position for support in support_list}

    unit_ex_equip_dict = {
        equip.serial_id: (equip.ex_equipment_id, equip.enhancement_pt)
        for equip in user_ex_equip
    }
    for unit_data in units:
        for equip in unit_data.cb_ex_equip_slot:
            if equip.serial_id:
                equip.ex_equipment_id, equip.enhancement_pt = unit_ex_equip_dict.get(
                    equip.serial_id, (0, 0)
                )
        unit_info = parse_unit_data(unit_data)
        unit_info.update(
            {
                "love_level": love_dict[unit_info["unit_id"]],
                "user_id": qid,
                "pcrid": viewer_id,
                "name": name,
                "support_position": friend_support_ids.get(unit_data.id, 0),
            }
        )
        if not unit_info["support_position"] and unit_data.id in clan_supposrt_ids:
            unit_info["support_position"] = clan_supposrt_ids[unit_data.id] + 2

        temp_units.append(PlayerUnit(**unit_info))
    await pcr_sqla.refresh_player_units(temp_units, qid)


def generate_unit2library(target_rank: float, target_units: List[UnitData]) -> list:
    return [
        {
            "e": "".join(["1" if equip.is_slot else "0" for equip in unit.equip_slot]),
            "p": unit.promotion_level,
            "r": str(unit.unit_rarity),
            "u": hex(unit.id // 100)[2:],
            "t": str(target_rank),
            "q": (
                str(unit.unique_equip_slot[0].enhancement_level)
                if unit.unique_equip_slot and unit.unique_equip_slot[0].rank > 0
                else "0"
            ),
            "b": "true" if unit.exceed_stage else "false",
            "f": False,
        }
        for unit in target_units
    ]


def get_library_equip_data(equip_list: List[InventoryInfo]) -> list:
    return [
        {
            "c": hex(equip.stock)[2:],
            "e": hex(equip.id)[2:],
            "a": "1",
        }
        for equip in equip_list
    ]


def get_library_memory_data(target_units: List[UnitData]) -> list:
    return [{"c": "0", "u": hex(unit.id)[2:]} for unit in target_units]


async def export_library(target_units: Dict[float, List[int]], account: Account) -> str:
    index = await get_support_list("self_query", account)
    query_units: Dict[float, List[UnitData]] = {
        target_rank: [] for target_rank in target_units
    }
    for unit in index.unit_list:
        if not target_units:
            break
        for target_rank in target_units:
            if unit.id // 100 in target_units[target_rank]:
                query_units[target_rank].append(unit)
                target_units[target_rank].remove(unit.id // 100)
                if not target_units[target_rank]:
                    del target_units[target_rank]
                break
    units = []
    all_units = []
    for rank in query_units:
        units += generate_unit2library(rank, query_units[rank])
        all_units += query_units[rank]

    json_str = json.dumps(
        [
            units,
            get_library_equip_data(index.user_equip),
            get_library_memory_data(all_units),
        ]
    )
    return base64.b64encode(gzip.compress(json_str.encode("utf-8"))).decode("utf-8")


mode2str = {1: "地下城", 2: "团队战/露娜塔", 3: "关卡"}
str2mode = {v: k for k, v in mode2str.items()}
str2mode.update({"公会": 2, "露娜": 2, "地下": 1, "工会": 2, "会战": 2})


async def change_support_unit(
    account: Account,
    support_unit_id: int,
    mode: int,
    include_image: bool = True,
):
    result = ""
    try:
        client = await query(account)
        player_info = await client.load_index()
        support_unit_name = fromid(support_unit_id).name
        unit_id = support_unit_id * 100 + 1
        mode_str = mode2str[mode]

        support_info = await client.get_support_unit_setting()

        current_support: List[List[SupportUnitSetting]] = [[], [], []]
        for unit in support_info.clan_support_units:
            if unit.position <= 2:
                current_support[0].append(unit)
            else:
                current_support[1].append(unit)

        for unit in support_info.friend_support_units:
            current_support[2].append(unit)

        for i in mode2str:
            for unit in current_support[i - 1]:
                if unit.unit_id == unit_id:
                    return f"{support_unit_name}已经在{mode2str[i]}支援中"

        unit_info = next(
            (unit for unit in player_info.unit_list if unit.id == unit_id),
            None,
        )
        if not unit_info:
            return f"未找到{support_unit_name}的数据，可能是未解锁"
        if unit_info.unit_level <= 10:
            return f"{support_unit_name}的等级过低(<=10级)，不可设置支援"

        # 地下城：clan_support_units support_type=1 position=1/2 mode=1
        # 团队战/露娜塔：clan_support_units support_type=1 position=3/4 mode=2
        # 关卡：friend_support_units support_type=2 position=1/2 mode=3
        # "action": 1=上 2=下
        # "unit_id": xxxx01
        target_support = current_support[mode - 1]
        num_support = len(target_support)

        try_position = {1, 2} if mode != 2 else {3, 4}  # 查询目标支援是否有坑位。
        if num_support == 0:  # 若有坑位，记录坑位。
            try_position = try_position.pop()
        elif num_support == 1:
            try_position = (try_position - {target_support[0].position}).pop()
        else:  # 若无坑位，查询是否可终止原支援
            available_change = [
                x for x in target_support if time.time() - x.support_start_time > 1800
            ]
            if not available_change:  # 若无法终止原支援，程序终止
                return f"{mode_str}当前已挂满且均不足30分钟，无法结束支援"
            # 若两个都可终止，终止挂的时间较早的那个
            try_change = min(available_change, key=lambda x: x.support_start_time)
            try_position = try_change.position

            await client.change_support_unit(
                support_type=2 if mode == 3 else 1,
                position=try_position,
                action=2,
                unit_id=try_change.unit_id,
            )
            result += (
                f"成功终止{fromid(try_change.unit_id // 100).name}的{mode_str}支援\n"
            )

        await client.change_support_unit(
            support_type=2 if mode == 3 else 1,
            position=try_position,
            action=1,
            unit_id=unit_id,
        )
        result += f"成功将{support_unit_name}挂上{mode_str}支援\n"

        unit_ex_equip_dict = {
            equip.serial_id: (equip.ex_equipment_id, equip.enhancement_pt)
            for equip in player_info.user_ex_equip
            if equip.serial_id
            in {equip.serial_id for equip in player_info.user_ex_equip}
        }
        for equip in unit_info.cb_ex_equip_slot:
            if equip.serial_id:
                equip.ex_equipment_id, equip.enhancement_pt = unit_ex_equip_dict.get(
                    equip.serial_id, (0, 0)
                )
        unit_info = parse_unit_data(unit_info)
        unit_info["support_position"] = try_position
        unit_info["user_id"] = account.user_id
        unit_info["pcrid"] = account.viewer_id
        unit_info["name"] = account.name
        unit_info["love_level"] = next(
            (
                love.love_level
                for love in player_info.user_chara_info
                if love.chara_id == support_unit_id
            ),
            0,
        )
        if include_image:
            result += MessageSegment.image(
                pic2b64(await generate_box_img([PlayerUnit(**unit_info)]))
            )
    except Exception as e:
        traceback.print_exc()
        return f"更换失败：{e}"
    return result


@functools.lru_cache(maxsize=128)
def read_knight_exp_rank(target_value: int) -> int:
    df = pd.read_csv(FilePath.data.value / "rank_exp.csv")
    exp_values = df.iloc[:, 0].values  # 第一列是经验值
    rank_values = df.iloc[:, 1].values  # 第二列是等级

    # 找到所有满足条件的索引
    valid_indices = exp_values <= target_value

    return int(rank_values[valid_indices][-1]) if valid_indices.any() else 1
