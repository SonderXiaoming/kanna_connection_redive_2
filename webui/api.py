import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import threading
import time
import traceback
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import httpx
import uvicorn
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from hoshino.modules.priconne.chara import fromid
from hoshino.modules.priconne._pcr_data import CHARA_NAME

from ..util.tools import daoflag2str, anywhere_send

from ..clanbattle import (
    clanbattle_info,
    clanbattle_pool,
    notice_update_time,
)
from ..clanbattle.model import ClanBattle, ClanbattleItem, PrioritizedQueryItem
from ..util.auto_boss import clan_boss_info
from ..clanbattle.base import clanbattle_report, cuidao
from ..database.dal import CookieCache, SLDao, pcr_sqla
from ..database.models import (
    Account,
    ArenaSetting,
    ClanBattleKPI,
    RefreshAccount,
    WebNotificationEvent,
    WebNotificationSetting,
)
from ..basedata import NoticeType, Platform
from ..client import check_client, get_access_key, pcrclient, tw_pcrclient
from ..login import query
from ..support_query.util import (
    change_support_unit,
    get_support_list,
    save_player_units,
    save_support_units,
)
from ..setting import WebSetting
from .util import *
from .web_model import *
from nonebot import logger, on_startup
from nonebot import get_bot
from hoshino import get_self_ids
from hoshino.config import SUPERUSERS

from sse_starlette.sse import EventSourceResponse

api_app = FastAPI()

report_versions: Dict[int, int] = {}
notice_versions: Dict[int, int] = {}
avatar_cache: Dict[int, Tuple[float, bytes, str]] = {}
AVATAR_CACHE_TTL = 3600
SUPERUSER_IDS = {str(user_id) for user_id in SUPERUSERS}
ROLE_EMPLOYEE = 1
ROLE_FOREMAN = 21
ROLE_MANAGER = 22
ROLE_CHAIRMAN = 999
ROLE_NAMES = {
    ROLE_EMPLOYEE: "员工",
    ROLE_FOREMAN: "工头",
    ROLE_MANAGER: "经理",
    ROLE_CHAIRMAN: "董事长",
}
QQ_ROLE_LEVELS = {"member": ROLE_EMPLOYEE, "admin": ROLE_FOREMAN, "administrator": ROLE_FOREMAN, "owner": ROLE_MANAGER}
NOTIFICATION_EVENT_TYPES = {"notice", "report", "monitor", "arena", "role", "account", "system"}
bot_group_cache: Tuple[float, List[dict]] = (0.0, [])
game_action_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def require_game_login_confirmation(
    confirmed: Optional[str] = Header(None, alias="X-Game-Login-Confirmed"),
):
    if confirmed != "yes":
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            "该操作会登录PCR账号并可能顶掉游戏客户端，请在确认提示中手动继续",
        )
    return True


def role_name(level: int) -> str:
    if level >= ROLE_CHAIRMAN:
        return ROLE_NAMES[ROLE_CHAIRMAN]
    if level >= ROLE_MANAGER:
        return ROLE_NAMES[ROLE_MANAGER]
    if level >= ROLE_FOREMAN:
        return ROLE_NAMES[ROLE_FOREMAN]
    return ROLE_NAMES[ROLE_EMPLOYEE]


async def connected_bot_groups(force: bool = False) -> List[dict]:
    global bot_group_cache
    now = time.time()
    if not force and now - bot_group_cache[0] < 30:
        return list(bot_group_cache[1])
    groups: Dict[int, dict] = {}
    try:
        bot = get_bot()
        for self_id in get_self_ids():
            try:
                for group in await bot.get_group_list(self_id=self_id):
                    group_id = int(group["group_id"])
                    groups[group_id] = {
                        **group,
                        "group_id": group_id,
                        "self_id": int(self_id),
                    }
            except Exception:
                continue
    except Exception:
        pass
    if groups or force:
        bot_group_cache = (now, list(groups.values()))
    return list(groups.values())


async def connected_group(group_id: int) -> Optional[dict]:
    return next(
        (group for group in await connected_bot_groups() if group["group_id"] == group_id),
        None,
    )


async def bot_member_info(group_id: int, user_id: int) -> Optional[dict]:
    group = await connected_group(group_id)
    if not group:
        return None
    try:
        return await get_bot().get_group_member_info(
            self_id=group["self_id"], group_id=group_id, user_id=user_id
        )
    except Exception:
        return None


async def effective_group_role(group_id: int, user_id: int) -> Tuple[int, Optional[object], Optional[dict]]:
    local_member = await pcr_sqla.get_clan_member(group_id, user_id)
    if is_hoshino_superuser(user_id):
        # Superusers do not need a per-group QQ member lookup. Apart from being
        # unnecessary for authorization, doing one network request for every
        # group made the group selector increasingly slow as the bot joined more
        # groups.
        return ROLE_CHAIRMAN, local_member, None
    if await connected_group(group_id):
        live_member = await bot_member_info(group_id, user_id)
        if not live_member:
            return 0, local_member, None
        live_level = QQ_ROLE_LEVELS.get(live_member.get("role", ""), ROLE_EMPLOYEE)
        delegated_level = (
            ROLE_FOREMAN
            if local_member and (local_member.priority or 0) >= ROLE_FOREMAN
            else ROLE_EMPLOYEE
        )
        return max(live_level, delegated_level), local_member, live_member
    if local_member and (local_member.priority or 0) >= ROLE_MANAGER:
        return ROLE_MANAGER, local_member, None
    if local_member and (local_member.priority or 0) >= ROLE_FOREMAN:
        return ROLE_FOREMAN, local_member, None
    return (ROLE_EMPLOYEE if local_member else 0), local_member, None


async def require_group_role(
    group_id: int,
    token: CookieCache,
    minimum: int = ROLE_EMPLOYEE,
) -> Tuple[int, Optional[object], Optional[dict]]:
    identity = await effective_group_role(group_id, int(token.user_id))
    if identity[0] < minimum:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有该公会的操作权限")
    return identity


def parse_json_list(value: str, fallback: List):
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def is_quiet_time(start: str, end: str) -> bool:
    if not start or not end:
        return False
    try:
        now = datetime.now().strftime("%H:%M")
        if start <= end:
            return start <= now < end
        return now >= start or now < end
    except Exception:
        return False


async def publish_group_notification(
    group_id: int,
    event_type: str,
    title: str,
    body: str,
    url: str,
):
    if event_type not in NOTIFICATION_EVENT_TYPES:
        return
    recipient_ids = {
        member.user_id for member in await pcr_sqla.get_group_member(group_id)
    }
    recipient_ids.update(
        int(user_id) for user_id in SUPERUSER_IDS if str(user_id).isdigit()
    )
    for user_id in recipient_ids:
        if (await effective_group_role(group_id, user_id))[0] < ROLE_EMPLOYEE:
            continue
        setting = await pcr_sqla.web_get_notification_setting(user_id)
        if not setting or not setting.enabled:
            continue
        selected_groups = {int(item) for item in parse_json_list(setting.group_ids, []) if str(item).isdigit()}
        selected_types = set(parse_json_list(setting.event_types, []))
        if group_id not in selected_groups or event_type not in selected_types:
            continue
        await pcr_sqla.web_add_notification_event(
            WebNotificationEvent(
                user_id=user_id,
                group_id=group_id,
                event_type=event_type,
                title=title,
                body=body,
                url=url,
            )
        )


async def publish_user_notification(
    user_id: int,
    event_type: str,
    title: str,
    body: str,
    url: str = "/usercenter?tab=notifications",
):
    setting = await pcr_sqla.web_get_notification_setting(user_id)
    if not setting or not setting.enabled:
        return
    if event_type not in set(parse_json_list(setting.event_types, [])):
        return
    await pcr_sqla.web_add_notification_event(
        WebNotificationEvent(
            user_id=user_id,
            event_type=event_type,
            title=title,
            body=body,
            url=url,
        )
    )


async def publish_arena_web_notification(user_id: int, title: str, body: str):
    await publish_user_notification(user_id, "arena", title, body, "/arena")


from ..jjckiller.model import set_arena_web_notifier

set_arena_web_notifier(publish_arena_web_notification)


async def live_group_members(group_id: int) -> Tuple[List[dict], Optional[dict]]:
    group = await connected_group(group_id)
    if not group:
        return [], None
    try:
        members = await get_bot().get_group_member_list(
            self_id=group["self_id"], group_id=group_id
        )
        return list(members), group
    except Exception:
        return [], group


async def group_display_name(group_id: int) -> str:
    if group := await connected_group(group_id):
        return group.get("group_name") or "环奈连结"
    members = await pcr_sqla.get_group_member(group_id)
    return members[0].group_name if members else "环奈连结"


def dao_weight(flag: float) -> float:
    return 1.0 if not flag else 0.5


def pcr_day_label(timestamp: int) -> str:
    value = datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8)))
    if value.hour < 5:
        value -= timedelta(days=1)
    return value.strftime("%m-%d")


async def build_clan_analytics(group_id: int) -> ClanAnalyticsResponse:
    records = await pcr_sqla.get_all_records(group_id)
    member_data = defaultdict(
        lambda: {
            "name": "",
            "dao": 0.0,
            "damage": 0,
            "full": 0,
            "tail": 0,
            "compensate": 0,
            "last": 0,
        }
    )
    boss_data = defaultdict(lambda: {"dao": 0.0, "damage": 0})
    trend_data = defaultdict(lambda: {"dao": 0.0, "damage": 0})
    composition_data = defaultdict(lambda: {"uses": 0, "damage": 0})
    for record in records:
        weight = dao_weight(record.flag)
        member = member_data[record.pcrid]
        member["name"] = record.name
        member["dao"] += weight
        member["damage"] += record.damage
        member["last"] = max(member["last"], record.time)
        if not record.flag:
            member["full"] += 1
        elif record.flag == 1:
            member["tail"] += 1
        else:
            member["compensate"] += 1

        boss = boss_data[record.boss]
        boss["dao"] += weight
        boss["damage"] += record.damage
        trend = trend_data[pcr_day_label(record.time)]
        trend["dao"] += weight
        trend["damage"] += record.damage
        units = tuple(
            unit
            for unit in (
                record.unit1,
                record.unit2,
                record.unit3,
                record.unit4,
                record.unit5,
            )
            if unit
        )
        composition_data[units]["uses"] += 1
        composition_data[units]["damage"] += record.damage

    total_damage = sum(record.damage for record in records)
    total_dao = sum(dao_weight(record.flag) for record in records)
    members = [
        AnalyticsMemberInfo(
            pcrid=pcrid,
            name=value["name"],
            dao=value["dao"],
            damage=value["damage"],
            full_count=value["full"],
            tail_count=value["tail"],
            compensate_count=value["compensate"],
            last_dao_time=value["last"],
        )
        for pcrid, value in member_data.items()
    ]
    members.sort(key=lambda item: item.damage, reverse=True)
    bosses = [
        AnalyticsBossInfo(
            boss=boss,
            dao=value["dao"],
            damage=value["damage"],
        )
        for boss, value in boss_data.items()
    ]
    bosses.sort(key=lambda item: item.boss)
    trends = [
        AnalyticsTrendInfo(date=date, dao=value["dao"], damage=value["damage"])
        for date, value in trend_data.items()
    ]
    trends.sort(key=lambda item: item.date)
    compositions = [
        AnalyticsCompositionInfo(
            units=list(units),
            uses=value["uses"],
            damage=value["damage"],
        )
        for units, value in composition_data.items()
    ]
    compositions.sort(key=lambda item: (item.uses, item.damage), reverse=True)
    return ClanAnalyticsResponse(
        total_damage=total_damage,
        total_dao=total_dao,
        members=members,
        bosses=bosses,
        trends=trends,
        compositions=compositions[:10],
    )


async def verify_group_access(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    level, _, _ = await require_group_role(group_id, token)
    return SimpleNamespace(priority=level)


def is_hoshino_superuser(user_id: int) -> bool:
    return str(user_id) in SUPERUSER_IDS


def effective_web_priority(user_id: int, web_user=None) -> int:
    if is_hoshino_superuser(user_id):
        return 100
    return (web_user.priority or 0) if web_user else 0


async def require_superuser(
    token: CookieCache = Depends(verify_cookie),
) -> CookieCache:
    if not is_hoshino_superuser(int(token.user_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅Hoshino超级用户可操作")
    return token


def account_info(account: Account) -> PcrAccountInfo:
    return PcrAccountInfo(
        platform=account.platform,
        viewer_id=account.viewer_id,
        name=account.name,
        allow_others=account.allow_others or 0,
    )


async def owned_game_account(user_id: int, platform: int) -> Account:
    account = next(
        (
            item
            for item in await pcr_sqla.query_account(user_id)
            if item.platform == platform
        ),
        None,
    )
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到指定PCR账号")
    return account


def support_mode_name(position: int) -> str:
    if position in (1, 2):
        return "关卡"
    if position in (3, 4):
        return "地下城"
    if position in (5, 6):
        return "团队战/露娜塔"
    return ""


def web_game_unit(unit) -> GameUnitInfo:
    unit_id = int(getattr(unit, "unit_id", 1000) or 1000)
    position = int(getattr(unit, "support_position", 0) or 0)
    aliases = CHARA_NAME.get(unit_id, [])
    if isinstance(aliases, str):
        aliases = [aliases]
    return GameUnitInfo(
        unit_id=unit_id,
        name=fromid(unit_id).name,
        owner_name=getattr(unit, "name", "") or "",
        owner_id=getattr(unit, "pcrid", None),
        rarity=int(getattr(unit, "rarity", 0) or 0),
        battle_rarity=int(getattr(unit, "battle_rarity", 0) or 0),
        level=int(getattr(unit, "level", 0) or 0),
        rank=int(getattr(unit, "rank", 0) or 0),
        unique_level=int(getattr(unit, "unique_level", -1) or 0),
        unique_level2=int(getattr(unit, "unique_level2", -1) or 0),
        love_level=int(getattr(unit, "love_level", 0) or 0),
        support_position=position,
        support_mode=support_mode_name(position),
        special_attribute=getattr(unit, "special_attribute", "") or "",
        equipment=[
            str(getattr(unit, f"equip_{index}", "") or "") for index in range(1, 7)
        ],
        aliases=[str(alias) for alias in aliases if alias],
    )


async def refresh_own_box(account: Account) -> int:
    player_info = await get_support_list("self_query", account)
    await save_player_units(
        player_info.unit_list or [],
        player_info.user_chara_info or [],
        player_info.user_ex_equip or [],
        account.user_id,
        player_info.user_info.user_name,
        player_info.user_info.viewer_id,
        friend_support_list=player_info.friend_support_units or [],
        support_list=player_info.dispatch_units or [],
    )
    return len(player_info.unit_list or [])


async def refresh_group_support_cache(account: Account, group_id: int) -> int:
    support = await get_support_list("support_query", account)
    player_info = await get_support_list("self_query", account)
    dispatch_ids = {
        unit.unit_id
        for unit in (player_info.dispatch_units or [])
        if unit.position in (3, 4)
    }
    self_units = [
        unit for unit in (player_info.unit_list or []) if unit.id in dispatch_ids
    ]
    extra_equipment = {
        equip.serial_id: (equip.ex_equipment_id, equip.enhancement_pt)
        for equip in (player_info.user_ex_equip or [])
    }
    for unit in self_units:
        for equip in unit.cb_ex_equip_slot or []:
            if equip.serial_id:
                equip.ex_equipment_id, equip.enhancement_pt = extra_equipment.get(
                    equip.serial_id, (0, 0)
                )
    units = list(support.support_unit_list or []) + self_units
    await save_support_units(
        units,
        group_id,
        player_info.user_info.user_name,
        player_info.user_info.viewer_id,
    )
    return len(units)


def base_unit_id(unit_id: Optional[int]) -> int:
    return int(unit_id // 100) if unit_id and unit_id > 100000 else 1000


def arena_summary(info) -> ArenaSummaryInfo:
    return ArenaSummaryInfo(
        rank=int(getattr(info, "rank", 0) or 0),
        group=int(getattr(info, "group", 0) or 0),
        highest_rank=int(getattr(info, "highest_rank", 0) or 0),
        season_highest_rank=int(getattr(info, "season_highest_rank", 0) or 0),
        battle_number=int(getattr(info, "battle_number", 0) or 0),
        max_battle_number=int(getattr(info, "max_battle_number", 0) or 0),
        interval_end_time=int(getattr(info, "interval_end_time", 0) or 0),
    )


async def notification_setting_for(user_id: int) -> NotificationSettingForm:
    allowed_group_ids = await notification_allowed_group_ids(user_id)
    setting = await pcr_sqla.web_get_notification_setting(user_id)
    if not setting:
        return NotificationSettingForm(group_ids=sorted(allowed_group_ids))
    selected_group_ids = {
        int(group_id)
        for group_id in parse_json_list(setting.group_ids, [])
        if str(group_id).isdigit()
    }
    event_types = [
        event_type
        for event_type in parse_json_list(setting.event_types, [])
        if event_type in NOTIFICATION_EVENT_TYPES
    ]
    return NotificationSettingForm(
        enabled=setting.enabled,
        delivery=setting.delivery,
        group_ids=sorted(selected_group_ids & allowed_group_ids),
        event_types=event_types or ["notice", "report", "monitor", "arena", "role"],
        quiet_start=setting.quiet_start or "",
        quiet_end=setting.quiet_end or "",
    )


async def notification_allowed_group_ids(user_id: int) -> set:
    allowed_group_ids = {
        member.group_id for member in await pcr_sqla.get_member_group(user_id)
    }
    if is_hoshino_superuser(user_id):
        allowed_group_ids.update(
            int(group_id)
            for group_id, _, _ in await pcr_sqla.get_all_clan_groups()
        )
    return allowed_group_ids


async def validate_pcr_account(
    form: PcrAccountBindingForm, user_id: int
) -> Tuple[Account, Optional[RefreshAccount]]:
    account_value = (form.account or "").strip()
    password_value = (form.password or "").strip()
    transfer_code = (form.transfer_code or "").strip()
    if (
        len(account_value) > 512
        or len(password_value) > 4096
        or len(transfer_code) > 512
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "账号凭据过长")

    refresh_account = None
    if form.platform == Platform.b_id.value:
        if not account_value or not password_value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "请填写B站账号和密码"
            )
        refresh_account = RefreshAccount(
            account=account_value, password=password_value
        )
        uid, access_key = await get_access_key(
            refresh_account.account, refresh_account.password, user_id
        )
        account = Account(
            user_id=user_id,
            platform=form.platform,
            account=str(uid),
            password=access_key,
            refresh=refresh_account.account,
        )
        client = pcrclient(account.account, account.password, account.platform)
    elif form.platform == Platform.qu_id.value:
        if not account_value or not password_value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "请填写 login_id 和 token"
            )
        account = Account(
            user_id=user_id,
            platform=form.platform,
            account=account_value,
            password=password_value,
        )
        client = pcrclient(account.account, account.password, account.platform)
    elif form.platform == Platform.tw_id.value:
        if not form.viewer_id or form.viewer_id <= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "请填写有效的游戏ID"
            )
        if transfer_code:
            account_value, password_value = (
                await tw_pcrclient.get_account_by_transfer_code(
                    form.viewer_id, transfer_code
                )
            )
        if not account_value or not password_value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "请填写 short_udid 和 udid，或使用引继码",
            )
        account = Account(
            user_id=user_id,
            platform=form.platform,
            viewer_id=form.viewer_id,
            account=account_value,
            password=password_value,
        )
        client = tw_pcrclient(
            account.account, account.password, account.viewer_id
        )
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "不支持的服务器")

    await client.login()
    load_index = await check_client(client)
    if not load_index:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "登录校验失败，请检查凭据")
    account.viewer_id = load_index.user_info.viewer_id
    account.name = load_index.user_info.user_name
    return account, refresh_account


@api_app.post("/login")
async def check_user(user: User, response: Response):
    ticket_login = False
    if user.ticket:
        account = consume_login_ticket(user.ticket)
        if not account:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录链接无效或已过期")
        web_user = await pcr_sqla.web_query_user(account)
        ticket_login = True
    elif user.account and user.password:
        web_user = await pcr_sqla.web_query_user(user.account)
        if not web_user or not verify_password(user.password, web_user.password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误")
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "缺少登录凭据")

    if web_user:
        if web_user.temp and time.time() - (web_user.create_time or 0) > 7 * 24 * 3600:
            if not ticket_login:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "临时密码过期，请重新获取登录链接"
                )
        if user.password and password_needs_upgrade(web_user.password):
            await pcr_sqla.web_update_password(
                web_user.account, hash_password(user.password)
            )
        elif ticket_login and web_user.temp and password_needs_upgrade(web_user.password):
            await pcr_sqla.web_update_password(
                web_user.account, hash_password(secrets.token_urlsafe(24))
            )
        token = secrets.token_urlsafe(16)
        cookie_age = 3600 * 24 * 7
        response.set_cookie(
            "token",
            token,
            max_age=cookie_age,
            expires=cookie_age,
            path="/",
            secure=WebSetting.cookie_secure.value,
            httponly=True,
            samesite="lax",
        )
        await pcr_sqla.web_delete_expired_cookies(cookie_age)
        await pcr_sqla.web_add_cookie(token, web_user.account)
    else:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在")


@api_app.post("/logout")
async def logout(response: Response, token: Optional[str] = Cookie(None)):
    if token:
        await pcr_sqla.web_delete_cookie(token=token)
    response.delete_cookie("token", path="/")


@api_app.get("/avatar/{qq_id}")
async def qq_avatar(qq_id: int, token: CookieCache = Depends(verify_cookie)):
    cached = avatar_cache.get(qq_id)
    if cached and time.time() - cached[0] < AVATAR_CACHE_TTL:
        return Response(
            content=cached[1],
            media_type=cached[2],
            headers={"Cache-Control": "private, max-age=3600"},
        )

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            remote = await client.get(
                "https://q1.qlogo.cn/g", params={"b": "qq", "nk": qq_id, "s": 140}
            )
            remote.raise_for_status()
        media_type = remote.headers.get("content-type", "image/jpeg").split(";", 1)[0]
        if not media_type.startswith("image/") or len(remote.content) > 2 * 1024 * 1024:
            raise ValueError("QQ头像响应无效")
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "QQ头像获取失败")

    if len(avatar_cache) >= 256:
        avatar_cache.pop(next(iter(avatar_cache)))
    avatar_cache[qq_id] = (time.time(), remote.content, media_type)
    return Response(
        content=remote.content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@api_app.get("/user")
async def user_info(token: CookieCache = Depends(verify_cookie)):
    user_id = int(token.user_id)
    web_user = await pcr_sqla.web_query_user(user_id)
    accounts = await pcr_sqla.query_account(user_id)
    groups = await pcr_sqla.get_member_group(user_id)
    if is_hoshino_superuser(user_id):
        local_groups = {
            int(group_id): group_name or "环奈连结"
            for group_id, group_name, _ in await pcr_sqla.get_all_clan_groups()
        }
        clan_info = [
            ClanInfo(
                group_id=group_id,
                group_name=local_groups.get(group_id, "环奈连结"),
                priority=ROLE_CHAIRMAN,
                role=ROLE_NAMES[ROLE_CHAIRMAN],
            )
            for group_id in sorted(local_groups)
        ]
    else:
        clan_info = []
        for group in groups:
            level, _, _ = await effective_group_role(group.group_id, user_id)
            if level < ROLE_EMPLOYEE:
                continue
            clan_info.append(
                ClanInfo(
                    group_id=group.group_id,
                    group_name=group.group_name,
                    priority=level,
                    role=role_name(level),
                )
            )
    return UserResponse(
        priority=effective_web_priority(user_id, web_user),
        is_superuser=is_hoshino_superuser(user_id),
        user_id=user_id,
        accounts=[account_info(account) for account in accounts],
        clan=clan_info,
        notification=await notification_setting_for(user_id),
    ).dict()


@api_app.post("/pcr-account")
async def bind_pcr_account(
    form: PcrAccountBindingForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    user_id = int(token.user_id)
    try:
        account, refresh_account = await validate_pcr_account(form, user_id)
    except HTTPException:
        raise
    except Exception:
        logger.error(traceback.format_exc())
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "绑定失败，请检查账号凭据、验证码或网络状态",
        )
    existing_account = next(
        (
            item
            for item in await pcr_sqla.query_account(user_id)
            if item.platform == account.platform
        ),
        None,
    )
    if existing_account:
        account.allow_others = existing_account.allow_others or 0
    await pcr_sqla.add_account(user_id, account.dict(exclude_none=True))
    if refresh_account:
        await pcr_sqla.add_refresh(refresh_account)
    await publish_user_notification(
        user_id,
        "account",
        "PCR账号绑定成功",
        f"已绑定 {account.name or account.viewer_id}（服务器 {account.platform}）",
        "/usercenter?tab=pcr",
    )
    return account_info(account).dict()


@api_app.delete("/pcr-account/{platform}")
async def unbind_pcr_account(
    platform: int, token: CookieCache = Depends(verify_cookie)
):
    user_id = int(token.user_id)
    accounts = await pcr_sqla.query_account(user_id)
    if not any(account.platform == platform for account in accounts):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到这个PCR账号")
    await pcr_sqla.delete_account(user_id, platform)
    await publish_user_notification(
        user_id,
        "account",
        "PCR账号已解绑",
        f"服务器 {platform} 的账号已解除绑定",
        "/usercenter?tab=pcr",
    )
    return {"message": "解绑成功"}


@api_app.patch("/pcr-account/{platform}/access")
async def update_pcr_account_access(
    platform: int,
    form: PcrAccountAccessForm,
    token: CookieCache = Depends(verify_cookie),
):
    user_id = int(token.user_id)
    if form.allow_others not in (0, 1, 2):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效触发权限")
    accounts = await pcr_sqla.query_account(user_id)
    if not any(account.platform == platform for account in accounts):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到这个PCR账号")
    await pcr_sqla.change_access(user_id, form.allow_others, platform=platform)
    await publish_user_notification(
        user_id,
        "account",
        "PCR触发权限已修改",
        f"服务器 {platform} 的触发权限等级更新为 {form.allow_others}",
        "/usercenter?tab=pcr",
    )
    return {"message": "触发权限修改成功", "allow_others": form.allow_others}


@api_app.get("/game/unit-icon/{unit_id}")
async def game_unit_icon(
    unit_id: int,
    rarity: int = Query(3, ge=1, le=6),
    token: CookieCache = Depends(verify_cookie),
):
    try:
        icon = await fromid(unit_id, rarity).get_icon(rarity)
    except Exception:
        icon = await fromid(1000, 3).get_icon(3)
    return FileResponse(
        icon.path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@api_app.get("/box")
async def player_box(
    search: str = "",
    support_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=120),
    token: CookieCache = Depends(verify_cookie),
):
    user_id = int(token.user_id)
    units = (
        await pcr_sqla.get_player_support_units(user_id)
        if support_only
        else await pcr_sqla.get_player_units(user_id)
    )
    keyword = search.strip().lower()
    web_units = [web_game_unit(unit) for unit in units]
    if keyword:
        web_units = [
            unit
            for unit in web_units
            if keyword in unit.name.lower()
            or keyword in str(unit.unit_id)
            or keyword in unit.owner_name.lower()
            or any(keyword in alias.lower() for alias in unit.aliases)
        ]
    web_units.sort(
        key=lambda unit: (unit.rank, unit.level, unit.rarity, unit.unit_id),
        reverse=True,
    )
    total = len(web_units)
    offset = (page - 1) * page_size
    visible = web_units[offset : offset + page_size]
    first = web_units[0] if web_units else None
    return GameUnitPage(
        units=visible,
        page=page,
        page_size=page_size,
        total=total,
        cache_name=first.owner_name if first else "",
        cache_viewer_id=first.owner_id if first else None,
    ).dict()


@api_app.post("/box/refresh")
async def refresh_player_box(
    form: GameAccountForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    user_id = int(token.user_id)
    account = await owned_game_account(user_id, form.platform)
    async with game_action_locks[user_id]:
        try:
            count = await refresh_own_box(account)
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"BOX刷新失败：{error}"
            )
    return {"message": f"已刷新 {count} 名角色", "count": count}


@api_app.post("/supports/set")
async def set_player_support(
    form: SupportChangeForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    user_id = int(token.user_id)
    if form.mode not in (1, 2, 3):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效助战类型")
    if not any(
        unit.unit_id == form.unit_id
        for unit in await pcr_sqla.get_player_units(user_id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "请先刷新BOX后再选择角色")
    account = await owned_game_account(user_id, form.platform)
    async with game_action_locks[user_id]:
        result = await change_support_unit(
            account, form.unit_id, form.mode, include_image=False
        )
        if "成功" not in result and "已经在" not in result:
            raise HTTPException(status.HTTP_409_CONFLICT, result or "助战设置失败")
        await refresh_own_box(account)
    return {"message": result.strip()}


@api_app.post("/supports/remove")
async def remove_player_support(
    form: SupportRemoveForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    user_id = int(token.user_id)
    account = await owned_game_account(user_id, form.platform)
    target_unit_id = form.unit_id * 100 + 1
    async with game_action_locks[user_id]:
        try:
            client = await query(account)
            support_info = await client.get_support_unit_setting()
            target = next(
                (
                    (unit, 1)
                    for unit in (support_info.clan_support_units or [])
                    if unit.unit_id == target_unit_id
                ),
                None,
            ) or next(
                (
                    (unit, 2)
                    for unit in (support_info.friend_support_units or [])
                    if unit.unit_id == target_unit_id
                ),
                None,
            )
            if not target:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "该角色当前不在助战位")
            unit, support_type = target
            await client.change_support_unit(
                support_type=support_type,
                position=unit.position,
                action=2,
                unit_id=unit.unit_id,
            )
            await refresh_own_box(account)
        except HTTPException:
            raise
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"下助战失败：{error}")
    return {"message": f"已将 {fromid(form.unit_id).name} 移出助战位"}


@api_app.get("/groups/{group_id}/supports")
async def group_supports(
    group_id: int,
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=120),
    token: CookieCache = Depends(verify_cookie),
):
    await require_group_role(group_id, token)
    units = [web_game_unit(unit) for unit in await pcr_sqla.get_support_units(group_id)]
    keyword = search.strip().lower()
    if keyword:
        units = [
            unit
            for unit in units
            if keyword in unit.name.lower()
            or keyword in str(unit.unit_id)
            or keyword in unit.owner_name.lower()
            or any(keyword in alias.lower() for alias in unit.aliases)
        ]
    units.sort(key=lambda unit: (unit.unit_id, unit.owner_name))
    total = len(units)
    offset = (page - 1) * page_size
    return GameUnitPage(
        units=units[offset : offset + page_size],
        page=page,
        page_size=page_size,
        total=total,
    ).dict()


@api_app.post("/groups/{group_id}/supports/refresh")
async def refresh_group_supports(
    group_id: int,
    form: GameAccountForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    user_id = int(token.user_id)
    await require_group_role(group_id, token)
    account = await owned_game_account(user_id, form.platform)
    async with game_action_locks[user_id]:
        try:
            count = await refresh_group_support_cache(account, group_id)
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"助战刷新失败：{error}"
            )
    return {"message": f"已刷新 {count} 条助战数据", "count": count}


def active_arena_for(source_user_id: int, platform: int):
    from ..jjckiller.model import arena_manager

    arena = arena_manager.get_owned_arena(source_user_id, platform)
    if not arena:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "这个场当前没有运行中的竞技场监控，请由账号所有者手动启动",
        )
    return arena


@api_app.get("/arena/sources")
async def arena_sources(token: CookieCache = Depends(verify_cookie)):
    from ..jjckiller.model import arena_manager

    user_id = int(token.user_id)
    own_accounts = await pcr_sqla.query_account(user_id)
    sources = {}
    for account in own_accounts:
        sources[(user_id, account.platform)] = ArenaSourceInfo(
            source_user_id=user_id,
            platform=account.platform,
            viewer_id=account.viewer_id,
            account_name=account.name or "",
            own=True,
        )
    for arena in arena_manager.active_arenas():
        accounts = await pcr_sqla.query_account(arena.user_id)
        account = next(
            (item for item in accounts if item.platform == arena.platform), None
        )
        key = (arena.user_id, arena.platform)
        sources[key] = ArenaSourceInfo(
            source_user_id=arena.user_id,
            platform=arena.platform,
            viewer_id=(account.viewer_id if account else None) or arena.viewer_id or None,
            account_name=(account.name if account else "") or arena.account_name,
            own=arena.user_id == user_id,
            monitored=True,
            last_check=int(arena.loop_check or 0),
            arena_group=int(arena.jjc_group or 0),
            arena_rank=int(arena.jjc_rank or 0),
            grand_group=int(arena.grand_group or 0),
            grand_rank=int(arena.grand_rank or 0),
        )
    ordered = sorted(
        sources.values(),
        key=lambda item: (not item.own, not item.monitored, item.platform, item.source_user_id),
    )
    return ArenaSourcesResponse(sources=ordered).dict()


@api_app.post("/arena/monitor/start")
async def start_arena_monitor(
    form: ArenaMonitorStartForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    from ..jjckiller import arena_pool as jjc_pool
    from ..jjckiller.model import ArenaItem, PrioritizedQueryItem as ArenaQueryItem, arena_manager

    user_id = int(token.user_id)
    account = await owned_game_account(user_id, form.platform)
    current = arena_manager.get_owned_arena(user_id)
    if current:
        if current.platform == form.platform:
            return {"message": "竞技场监控已在运行", "loop_num": current.loop_num}
        raise HTTPException(status.HTTP_409_CONFLICT, "请先停止当前服务器的竞技场监控")
    async with game_action_locks[user_id]:
        try:
            client = await query(account)
            arena = arena_manager.generate_arena(user_id)
            await arena.init(
                client,
                0,
                0,
                account.platform,
                account.name or "",
                account.viewer_id or 0,
            )
            loop_num = arena.loop_num
            await jjc_pool.add_task(
                ArenaQueryItem(data=ArenaItem(arena, loop_num))
            )
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"竞技场监控启动失败：{error}")
    await publish_user_notification(
        user_id,
        "arena",
        "竞技场监控已启动",
        f"{account.name or account.viewer_id} 的普通竞技场与公主竞技场已开始监控",
        "/arena",
    )
    return {"message": "竞技场监控已启动", "loop_num": loop_num}


@api_app.post("/arena/monitor/stop")
async def stop_arena_monitor(
    platform: int = 0, token: CookieCache = Depends(verify_cookie)
):
    from ..jjckiller.model import arena_manager

    user_id = int(token.user_id)
    arena = arena_manager.get_owned_arena(user_id, platform)
    if not arena:
        raise HTTPException(status.HTTP_409_CONFLICT, "当前没有运行中的竞技场监控")
    arena.loop_num += 1
    arena.loop_check = 0
    arena_manager.delete_arena(user_id)
    await publish_user_notification(
        user_id,
        "arena",
        "竞技场监控已停止",
        f"{arena.account_name or arena.viewer_id or '当前账号'} 的竞技场监控已停止",
        "/arena",
    )
    return {"message": "竞技场监控已停止"}


@api_app.get("/arena")
async def arena_overview(
    source_user_id: int,
    platform: int = 0,
    token: CookieCache = Depends(verify_cookie),
):
    viewer_user_id = int(token.user_id)
    arena = active_arena_for(source_user_id, platform)
    account = next(
        (
            item
            for item in await pcr_sqla.query_account(source_user_id)
            if item.platform == platform
        ),
        None,
    )
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控账号已不存在")
    async with game_action_locks[source_user_id]:
        try:
            arena_info, grand_info = await asyncio.gather(
                arena.client.arena_info(), arena.client.grand_arena_info()
            )
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"竞技场数据获取失败：{error}")
    await pcr_sqla.init_jjc_setting(ArenaSetting(user_id=source_user_id))
    setting = await pcr_sqla.get_jjc_setting(source_user_id)
    return ArenaOverviewResponse(
        account=account_info(account),
        arena=arena_summary(arena_info.arena_info),
        grand_arena=arena_summary(grand_info.grand_arena_info),
        settings=ArenaSettingForm(
            jjc_notice=bool(setting.jjc_notice),
            grand_notice=bool(setting.grand_notice),
        ),
        source_user_id=source_user_id,
        monitored=True,
        can_manage=source_user_id == viewer_user_id,
    ).dict()


@api_app.put("/arena/settings")
async def set_arena_settings(
    form: ArenaSettingForm, token: CookieCache = Depends(verify_cookie)
):
    user_id = int(token.user_id)
    await pcr_sqla.init_jjc_setting(ArenaSetting(user_id=user_id))
    await pcr_sqla.update_jjc_setting(
        user_id,
        {"jjc_notice": form.jjc_notice, "grand_notice": form.grand_notice},
    )
    return {"message": "竞技场提醒设置已保存"}


@api_app.get("/arena/rankings")
async def arena_rankings(
    platform: int = 0,
    source_user_id: int = 0,
    arena_type: str = "arena",
    page: int = Query(1, ge=1, le=5),
    token: CookieCache = Depends(verify_cookie),
):
    if arena_type not in ("arena", "grand"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效竞技场类型")
    arena = active_arena_for(source_user_id, platform)
    async with game_action_locks[source_user_id]:
        try:
            ranking = (
                await arena.client.grand_rank(page)
                if arena_type == "grand"
                else await arena.client.arena_rank(page)
            )
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"排行榜获取失败：{error}"
            )
    players = []
    for player in ranking.ranking or []:
        favorite = getattr(player, "favorite_unit", None)
        if arena_type == "grand":
            deck = getattr(player, "grand_arena_deck", None)
            defence = [
                [base_unit_id(unit.id) for unit in (team or [])]
                for team in (
                    getattr(deck, "first", None),
                    getattr(deck, "second", None),
                    getattr(deck, "third", None),
                )
                if team
            ]
        else:
            defence = [
                [base_unit_id(unit.id) for unit in (player.arena_deck or [])]
            ]
        players.append(
            ArenaRankPlayerInfo(
                viewer_id=int(player.viewer_id),
                rank=int(player.rank or 0),
                user_name=player.user_name or "",
                team_level=int(player.team_level or 0),
                winning_number=getattr(player, "winning_number", None),
                favorite_unit_id=base_unit_id(getattr(favorite, "id", None)),
                favorite_unit_rarity=int(
                    getattr(favorite, "unit_rarity", 0) or 0
                ),
                defence=defence,
            )
        )
    return ArenaRankingResponse(
        arena_type=arena_type,
        page=page,
        source_user_id=source_user_id,
        group=arena.grand_group if arena_type == "grand" else arena.jjc_group,
        players=players,
    ).dict()


@api_app.get("/arena/players/{viewer_id}")
async def arena_player_profile(
    viewer_id: int,
    source_user_id: int,
    platform: int = 0,
    token: CookieCache = Depends(verify_cookie),
):
    arena = active_arena_for(source_user_id, platform)
    async with game_action_locks[source_user_id]:
        try:
            profile = await arena.get_profile_by_cache(viewer_id)
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"玩家资料获取失败：{error}")
    if not profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未找到该玩家资料")
    user = profile.user_info
    favorite = profile.favorite_unit
    return ArenaPlayerProfileInfo(
        viewer_id=int(user.viewer_id or viewer_id),
        user_name=user.user_name or "",
        team_level=int(user.team_level or 0),
        clan_name=profile.clan_name or "",
        favorite_unit_id=base_unit_id(getattr(favorite, "id", None)),
        favorite_unit_rarity=int(getattr(favorite, "unit_rarity", 0) or 0),
        arena_rank=int(user.arena_rank or 0),
        arena_group=int(user.arena_group or 0),
        grand_arena_rank=int(user.grand_arena_rank or 0),
        grand_arena_group=int(user.grand_arena_group or 0),
    ).dict()


@api_app.get("/notification-settings")
async def get_notification_settings(token: CookieCache = Depends(verify_cookie)):
    return (await notification_setting_for(int(token.user_id))).dict()


@api_app.put("/notification-settings")
async def set_notification_settings(
    form: NotificationSettingForm, token: CookieCache = Depends(verify_cookie)
):
    user_id = int(token.user_id)
    if form.delivery not in (0, 1, 2):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "不支持的通知方式")
    if not set(form.event_types).issubset(NOTIFICATION_EVENT_TYPES):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "包含无效通知类型")
    for value in (form.quiet_start, form.quiet_end):
        if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "免打扰时间格式无效")
    allowed_group_ids = await notification_allowed_group_ids(user_id)
    selected_group_ids = set(form.group_ids)
    if not selected_group_ids.issubset(allowed_group_ids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不能订阅未加入的群")
    setting = WebNotificationSetting(
        user_id=user_id,
        enabled=form.enabled,
        delivery=form.delivery,
        group_ids=json.dumps(sorted(selected_group_ids)),
        event_types=json.dumps(sorted(set(form.event_types))),
        quiet_start=form.quiet_start,
        quiet_end=form.quiet_end,
    )
    await pcr_sqla.web_set_notification_setting(setting)
    return (await notification_setting_for(user_id)).dict()


@api_app.get("/notifications/stream")
async def notification_stream(token: CookieCache = Depends(verify_cookie)):
    user_id = int(token.user_id)

    async def notification_generator():
        latest = await pcr_sqla.web_notification_inbox(user_id, limit=1)
        last_event_id = latest[0].id if latest else 0
        last_report_times: Dict[int, int] = {}
        last_monitor_states: Dict[int, Tuple[bool, int]] = {}
        yield json.dumps({"type": "ready"})
        while True:
            preference = await notification_setting_for(user_id)
            if not preference.enabled:
                return
            selected_group_ids = set(preference.group_ids)
            selected_types = set(preference.event_types)
            quiet = is_quiet_time(preference.quiet_start, preference.quiet_end)

            events = await pcr_sqla.web_list_notification_events(
                user_id, after_id=last_event_id
            )
            for event in events:
                last_event_id = max(last_event_id, event.id or 0)
                if quiet or event.event_type not in selected_types:
                    continue
                if event.group_id and event.group_id not in selected_group_ids:
                    continue
                yield json.dumps(
                    {
                        "id": event.id,
                        "type": event.event_type,
                        "group_id": event.group_id,
                        "title": event.title,
                        "body": event.body,
                        "url": event.url,
                    },
                    ensure_ascii=False,
                )

            if not quiet:
                for group_id in selected_group_ids:
                    clan_info = clanbattle_info.get(group_id)
                    if "report" in selected_types and clan_info:
                        current_report_time = int(clan_info.dao_update_time or 0)
                        previous_report_time = last_report_times.setdefault(
                            group_id, current_report_time
                        )
                        if current_report_time > previous_report_time:
                            last_report_times[group_id] = current_report_time
                            yield json.dumps(
                                {
                                    "type": "report",
                                    "group_id": group_id,
                                    "title": "会战战报已更新",
                                    "body": "检测到新的出刀记录",
                                    "url": f"/{group_id}/reporttable",
                                },
                                ensure_ascii=False,
                            )
                    if "monitor" in selected_types:
                        running = bool(clan_info and clan_info.loop_check)
                        loop_num = int(clan_info.loop_num if clan_info else 0)
                        current_state = (running, loop_num)
                        previous_state = last_monitor_states.setdefault(
                            group_id, current_state
                        )
                        if current_state != previous_state:
                            last_monitor_states[group_id] = current_state
                            yield json.dumps(
                                {
                                    "type": "monitor",
                                    "group_id": group_id,
                                    "title": "会战监控状态变化",
                                    "body": "监控已启动" if running else "监控已停止",
                                    "url": f"/clans?group={group_id}&tab=operations",
                                },
                                ensure_ascii=False,
                            )
            await asyncio.sleep(3)

    return EventSourceResponse(content=notification_generator())


@api_app.get("/notifications")
async def notification_inbox(token: CookieCache = Depends(verify_cookie)):
    events = await pcr_sqla.web_notification_inbox(int(token.user_id), limit=100)
    return NotificationInboxResponse(
        events=[
            NotificationEventInfo(
                id=event.id,
                group_id=event.group_id,
                event_type=event.event_type,
                title=event.title,
                body=event.body,
                url=event.url,
                time=event.time,
                read=event.read,
            )
            for event in events
        ],
        unread=sum(not event.read for event in events),
    ).dict()


@api_app.patch("/notifications/{event_id}/read")
async def mark_notification_read(
    event_id: int, token: CookieCache = Depends(verify_cookie)
):
    await pcr_sqla.web_mark_notifications_read(int(token.user_id), event_id)
    return {"message": "已读"}


@api_app.post("/notifications/read-all")
async def mark_all_notifications_read(token: CookieCache = Depends(verify_cookie)):
    await pcr_sqla.web_mark_notifications_read(int(token.user_id))
    return {"message": "已全部标记已读"}


@api_app.get("/admin/users")
async def admin_users(
    query: Optional[str] = None,
    token: CookieCache = Depends(require_superuser),
):
    async def summarize(user) -> AdminUserInfo:
        user_id = int(user.account)
        accounts, clans, sessions = await asyncio.gather(
            pcr_sqla.query_account(user_id),
            pcr_sqla.get_member_group(user_id),
            pcr_sqla.web_count_cookies(user.account),
        )
        return AdminUserInfo(
            account=user.account,
            priority=effective_web_priority(user_id, user),
            temp=bool(user.temp),
            create_time=int(user.create_time or 0),
            is_superuser=is_hoshino_superuser(user_id),
            pcr_accounts=len(accounts),
            clans=len(clans),
            active_sessions=sessions,
        )

    users = await pcr_sqla.web_list_users(query=(query or "").strip() or None)
    valid_users = [user for user in users if user.account.isdigit()]
    summaries = []
    for user in valid_users:
        summaries.append(await summarize(user))
    return AdminUsersResponse(users=summaries).dict()


@api_app.patch("/admin/users/{account}")
async def admin_update_user(
    account: str,
    form: AdminUserUpdateForm,
    token: CookieCache = Depends(require_superuser),
):
    if not account.isdigit():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效QQ号")
    if is_hoshino_superuser(int(account)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hoshino超级用户角色不可修改")
    if form.priority not in (0, 21):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效站点角色")
    if not await pcr_sqla.web_query_user(account):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    await pcr_sqla.web_update_priority(account, form.priority)
    return {"message": "用户角色修改成功", "priority": form.priority}


@api_app.post("/admin/users/{account}/revoke-sessions")
async def admin_revoke_user_sessions(
    account: str,
    token: CookieCache = Depends(require_superuser),
):
    if not await pcr_sqla.web_query_user(account):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    await pcr_sqla.web_delete_cookie(user_id=account)
    return {"message": "用户已强制下线"}


@api_app.delete("/admin/users/{account}")
async def admin_delete_user(
    account: str,
    token: CookieCache = Depends(require_superuser),
):
    operator = str(token.user_id)
    if account == operator:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不能删除当前登录账号")
    if not account.isdigit():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效QQ号")
    if is_hoshino_superuser(int(account)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不能删除Hoshino超级用户")
    if not await pcr_sqla.web_query_user(account):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    await pcr_sqla.web_delete_user(account)
    return {"message": "网页登录用户已移除"}


@api_app.get("/groups")
async def accessible_groups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    query: str = Query("", max_length=80),
    pinned_group_id: Optional[int] = Query(None, ge=1),
    token: CookieCache = Depends(verify_cookie),
):
    user_id = int(token.user_id)
    local_rows = await pcr_sqla.get_all_clan_groups()
    local_groups = {
        int(group_id): {
            "group_id": int(group_id),
            "group_name": group_name or "环奈连结",
            "member_count": int(member_count or 0),
        }
        for group_id, group_name, member_count in local_rows
    }
    superuser = is_hoshino_superuser(user_id)

    # A ClanBattleMember row is written by the `绑定本群公会` command and is the
    # explicit acknowledgement that a group uses this service. Merely sharing a
    # group with the bot must not make it appear in the web console.
    if superuser:
        candidate_ids = set(local_groups)
        role_levels = {group_id: ROLE_CHAIRMAN for group_id in candidate_ids}
    else:
        candidate_ids = {
            member.group_id for member in await pcr_sqla.get_member_group(user_id)
        }
        identities = await asyncio.gather(
            *(effective_group_role(group_id, user_id) for group_id in candidate_ids)
        )
        role_levels = {
            group_id: identity[0]
            for group_id, identity in zip(candidate_ids, identities)
            if identity[0] >= ROLE_EMPLOYEE
        }
        candidate_ids = set(role_levels)

    keyword = query.strip().casefold()
    if keyword:
        candidate_ids = {
            group_id
            for group_id in candidate_ids
            if keyword in str(group_id)
            or keyword
            in local_groups.get(group_id, {}).get("group_name", "").casefold()
        }

    ordered_ids = sorted(
        candidate_ids,
        key=lambda group_id: (
            group_id == pinned_group_id,
            role_levels.get(group_id, ROLE_EMPLOYEE),
            local_groups.get(group_id, {}).get("group_name", "").casefold(),
        ),
        reverse=True,
    )
    total = len(ordered_ids)
    offset = (page - 1) * page_size
    page_ids = ordered_ids[offset : offset + page_size]
    live_groups = {
        group["group_id"]: group for group in await connected_bot_groups()
    }

    response = []
    for group_id in page_ids:
        level = role_levels.get(group_id, ROLE_EMPLOYEE)
        source = live_groups.get(group_id) or local_groups.get(group_id, {})
        response.append(
            GroupSummary(
                group_id=group_id,
                group_name=source.get("group_name") or "环奈连结",
                member_count=int(source.get("member_count") or local_groups.get(group_id, {}).get("member_count") or 0),
                role=role_name(level),
                role_level=level,
                bot_online=group_id in live_groups,
            )
        )
    return {
        "groups": [item.dict() for item in response],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": offset + len(response) < total,
    }


@api_app.get("/groups/{group_id}/management")
async def clan_management(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    operator_level, _, _ = await require_group_role(group_id, token)
    live_members, live_group = await live_group_members(group_id)
    local_members = {
        member.user_id: member for member in await pcr_sqla.get_group_member(group_id)
    }
    if live_members:
        source_members = live_members
    else:
        source_members = [
            {
                "user_id": member.user_id,
                "nickname": "",
                "card": "",
                "role": "member",
                "join_time": 0,
                "last_sent_time": 0,
            }
            for member in local_members.values()
        ]
    user_ids = [int(member["user_id"]) for member in source_members]
    accounts = await pcr_sqla.query_accounts_by_users(user_ids)
    accounts_by_user = defaultdict(list)
    for account in accounts:
        accounts_by_user[account.user_id].append(account)
    record_summary = defaultdict(lambda: {"dao": 0.0, "last": 0})
    for record in await pcr_sqla.get_all_records(group_id):
        record_summary[record.pcrid]["dao"] += dao_weight(record.flag)
        record_summary[record.pcrid]["last"] = max(
            record_summary[record.pcrid]["last"], record.time
        )

    member_rows = []
    for member in source_members:
        member_id = int(member["user_id"])
        local = local_members.get(member_id)
        qq_level = QQ_ROLE_LEVELS.get(member.get("role", "member"), ROLE_EMPLOYEE)
        delegated = bool(local and (local.priority or 0) >= ROLE_FOREMAN and qq_level < ROLE_FOREMAN)
        if is_hoshino_superuser(member_id):
            member_level = ROLE_CHAIRMAN
        else:
            member_level = max(
                qq_level,
                ROLE_FOREMAN if delegated else ROLE_EMPLOYEE,
            )
        account = accounts_by_user.get(member_id, [None])[0]
        record = record_summary.get(account.viewer_id if account else 0, {"dao": 0, "last": 0})
        member_rows.append(
            ClanMemberInfo(
                user_id=member_id,
                nickname=member.get("nickname") or "",
                card=member.get("card") or "",
                qq_role=member.get("role") or "member",
                role=role_name(member_level),
                role_level=member_level,
                delegated=delegated,
                join_time=int(member.get("join_time") or 0),
                last_sent_time=int(member.get("last_sent_time") or 0),
                game_name=account.name if account else "",
                viewer_id=account.viewer_id if account else None,
                platform=account.platform if account else None,
                dao_count=record["dao"],
                last_dao_time=record["last"],
            )
        )
    member_rows.sort(key=lambda item: (item.role_level, item.dao_count), reverse=True)
    group_name = (live_group or {}).get("group_name") or await group_display_name(group_id)
    return ClanManagementResponse(
        group=GroupSummary(
            group_id=group_id,
            group_name=group_name,
            member_count=len(member_rows),
            role=role_name(operator_level),
            role_level=operator_level,
            bot_online=bool(live_group),
        ),
        members=member_rows,
        can_manage_roles=operator_level >= ROLE_MANAGER,
        can_operate=operator_level >= ROLE_FOREMAN,
    ).dict()


@api_app.post("/groups/{group_id}/members/sync")
async def sync_clan_members(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    await require_group_role(group_id, token, ROLE_MANAGER)
    members, group = await live_group_members(group_id)
    if not group:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "机器人未连接该群")
    local_members = {
        member.user_id: member for member in await pcr_sqla.get_group_member(group_id)
    }
    for member in members:
        user_id = int(member["user_id"])
        existing = local_members.get(user_id)
        qq_level = QQ_ROLE_LEVELS.get(member.get("role", "member"), ROLE_EMPLOYEE)
        if qq_level >= ROLE_MANAGER:
            stored_level = ROLE_MANAGER
        elif qq_level >= ROLE_FOREMAN:
            stored_level = ROLE_FOREMAN
        else:
            stored_level = (
                ROLE_FOREMAN
                if existing and (existing.priority or 0) == ROLE_FOREMAN
                else 0
            )
        await pcr_sqla.update_clan_member_role(
            group_id,
            user_id,
            stored_level,
            group.get("group_name") or "环奈连结",
        )
    return {"message": f"已同步 {len(members)} 名群成员"}


@api_app.patch("/groups/{group_id}/members/{target_user_id}/role")
async def update_clan_member_role(
    group_id: int,
    target_user_id: int,
    form: GroupRoleUpdateForm,
    token: CookieCache = Depends(verify_cookie),
):
    operator_level, _, _ = await require_group_role(group_id, token, ROLE_MANAGER)
    target_local = await pcr_sqla.get_clan_member(group_id, target_user_id)
    target_live = await bot_member_info(group_id, target_user_id)
    if await connected_group(group_id) and not target_live:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到该群成员")
    if not target_local and not target_live:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到该群成员")
    if is_hoshino_superuser(target_user_id) or (target_live or {}).get("role") == "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不能修改董事长或经理身份")
    normalized = {"employee": ROLE_EMPLOYEE, "员工": ROLE_EMPLOYEE, "foreman": ROLE_FOREMAN, "工头": ROLE_FOREMAN}
    if form.role not in normalized:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "只能设置员工或工头")
    target_level = normalized[form.role]
    if target_level == ROLE_EMPLOYEE and QQ_ROLE_LEVELS.get((target_live or {}).get("role", ""), 0) >= ROLE_FOREMAN:
        raise HTTPException(status.HTTP_409_CONFLICT, "该成员仍是QQ群管理员，请先在群内取消管理员")
    group_name = await group_display_name(group_id)
    await pcr_sqla.update_clan_member_role(
        group_id, target_user_id, ROLE_FOREMAN if target_level == ROLE_FOREMAN else 0, group_name
    )
    detail = f"{role_name(operator_level)}将 {target_user_id} 设置为{role_name(target_level)}"
    await publish_group_notification(
        group_id,
        "role",
        f"{group_name} 成员权限调整",
        detail,
        f"/clans?group={group_id}&tab=members",
    )
    return {"message": "成员权限已更新", "role": role_name(target_level)}


@api_app.get("/groups/{group_id}/operations")
async def clan_operations(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    user_id = int(token.user_id)
    level, _, _ = await require_group_role(group_id, token)
    local_members = await pcr_sqla.get_group_member(group_id)
    accounts = await pcr_sqla.query_accounts_by_users(
        [member.user_id for member in local_members]
    )
    usable_accounts = []
    for account in accounts:
        if account.user_id == user_id or level >= ROLE_CHAIRMAN or (account.allow_others or 0) >= 1:
            usable_accounts.append(
                OperationAccountInfo(
                    user_id=account.user_id,
                    owner_name=account.name or str(account.user_id),
                    platform=account.platform,
                    viewer_id=account.viewer_id,
                    name=account.name,
                    allow_others=account.allow_others or 0,
                )
            )
    current = clanbattle_info.get(group_id)
    analytics = await build_clan_analytics(group_id)
    names = {member.pcrid: member.name for member in analytics.members}
    kpis = await pcr_sqla.get_kpis(group_id)
    return ClanOperationsResponse(
        role=role_name(level),
        role_level=level,
        can_operate=level >= ROLE_FOREMAN,
        monitor=MonitorStateInfo(
            running=bool(current and current.loop_check),
            operator_id=getattr(current, "user_id", None) if current else None,
            operator_name=str(getattr(current, "user_id", "")) if current else "",
            loop_num=int(current.loop_num if current else 0),
            last_check=int(current.loop_check if current else 0),
            error_count=int(current.error_count if current else 0),
            rank=int(current.rank if current else 0),
            stage=str(current.period if current else "暂无信息"),
        ),
        accounts=usable_accounts,
        kpis=[
            KpiInfo(
                pcrid=kpi.pcrid,
                name=names.get(kpi.pcrid, ""),
                bonus=kpi.bouns,
                time=kpi.time or 0,
            )
            for kpi in kpis
        ],
        subscribe_count=len(await pcr_sqla.get_notice(NoticeType.subscribe.value, group_id)),
        apply_count=len(await pcr_sqla.get_notice(NoticeType.apply.value, group_id)),
        tree_count=len(await pcr_sqla.get_notice(NoticeType.tree.value, group_id)),
    ).dict()


@api_app.post("/groups/{group_id}/monitor/start")
async def start_clan_monitor(
    group_id: int,
    form: MonitorStartForm,
    token: CookieCache = Depends(verify_cookie),
    _: bool = Depends(require_game_login_confirmation),
):
    operator_id = int(token.user_id)
    level, _, _ = await require_group_role(group_id, token, ROLE_FOREMAN)
    account_owner = form.account_user_id or operator_id
    account = next(
        (
            item
            for item in await pcr_sqla.query_account(account_owner)
            if item.platform == form.platform
        ),
        None,
    )
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到指定PCR账号")
    if account_owner != operator_id and level < ROLE_CHAIRMAN and (account.allow_others or 0) < 1:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该账号不允许工头触发")
    group = await connected_group(group_id)
    if not group:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "机器人未连接该群")
    current = clanbattle_info.setdefault(group_id, ClanBattle(group_id))
    try:
        await current.init(await query(account), account_owner, group["self_id"])
    except Exception:
        try:
            await current.init(await query(account, True), account_owner, group["self_id"])
        except Exception as error:
            logger.error(traceback.format_exc())
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"监控启动失败：{error}")
    loop_num = current.loop_num
    await clanbattle_pool.add_task(
        PrioritizedQueryItem(data=ClanbattleItem(current, loop_num))
    )
    group_name = await group_display_name(group_id)
    await publish_group_notification(
        group_id,
        "monitor",
        f"{group_name} 会战监控已启动",
        f"操作人 {operator_id}，监控账号 {account.name or account_owner}",
        f"/clans?group={group_id}&tab=operations",
    )
    await anywhere_send(
        f"网页端已启动出刀监控，监控账号：{account.name or account_owner}",
        group_id,
        group["self_id"],
    )
    return {"message": "出刀监控已启动", "loop_num": loop_num}


@api_app.post("/groups/{group_id}/monitor/stop")
async def stop_clan_monitor(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    operator_id = int(token.user_id)
    await require_group_role(group_id, token, ROLE_FOREMAN)
    current = clanbattle_info.get(group_id)
    if not current or not current.loop_check:
        raise HTTPException(status.HTTP_409_CONFLICT, "当前没有运行中的监控")
    current.loop_num += 1
    current.loop_check = 0
    group_name = await group_display_name(group_id)
    await publish_group_notification(
        group_id,
        "monitor",
        f"{group_name} 会战监控已停止",
        f"操作人 {operator_id}",
        f"/clans?group={group_id}&tab=operations",
    )
    await anywhere_send("网页端已停止出刀监控", group_id)
    return {"message": "出刀监控已停止"}


@api_app.post("/groups/{group_id}/urge")
async def urge_clan_members(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    await require_group_role(group_id, token, ROLE_FOREMAN)
    current = clanbattle_info.get(group_id)
    all_members = getattr(current, "members", {}) if current else {}
    if not all_members:
        raise HTTPException(status.HTTP_409_CONFLICT, "需要先启动会战监控获取游戏成员")
    from ..clanbattle.base import day_report

    today_records = await pcr_sqla.get_day_rcords(int(time.time()), group_id)
    report = await day_report(today_records, all_members)
    await anywhere_send(cuidao(report), group_id)
    return {"message": "催刀提醒已发送"}


@api_app.delete("/groups/{group_id}/notices")
async def clear_clan_notices(
    group_id: int,
    notice_type: Optional[int] = None,
    token: CookieCache = Depends(verify_cookie),
):
    operator_id = int(token.user_id)
    await require_group_role(group_id, token, ROLE_FOREMAN)
    types = [notice_type] if notice_type is not None else [0, 1, 2]
    if any(item not in (0, 1, 2) for item in types):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效通知类型")
    for item in types:
        await pcr_sqla.delete_notice(item, group_id)
    notice_update_time[group_id] = int(time.time())
    notice_versions[group_id] = notice_versions.get(group_id, 0) + 1
    await publish_group_notification(
        group_id,
        "notice",
        "会战通知已清理",
        f"操作人 {operator_id}",
        f"/{group_id}/noticetable",
    )
    return {"message": "通知已清理"}


@api_app.put("/groups/{group_id}/kpi")
async def update_clan_kpi(
    group_id: int,
    form: KpiUpdateForm,
    token: CookieCache = Depends(verify_cookie),
):
    await require_group_role(group_id, token, ROLE_FOREMAN)
    if abs(form.bonus) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "KPI补正超出范围")
    await pcr_sqla.add_kpi_special(
        ClanBattleKPI(group_id=group_id, pcrid=form.pcrid, bouns=form.bonus)
    )
    return {"message": "KPI补正已保存"}


@api_app.delete("/groups/{group_id}/kpi/{pcrid}")
async def delete_clan_kpi(
    group_id: int, pcrid: int, token: CookieCache = Depends(verify_cookie)
):
    await require_group_role(group_id, token, ROLE_FOREMAN)
    await pcr_sqla.delete_kpi(group_id, pcrid)
    return {"message": "KPI补正已删除"}


@api_app.get("/groups/{group_id}/analytics")
async def clan_analytics(
    group_id: int, token: CookieCache = Depends(verify_cookie)
):
    await require_group_role(group_id, token)
    return (await build_clan_analytics(group_id)).dict()


@api_app.get("/home")
async def home_info(token: CookieCache = Depends(verify_cookie)):
    user_id = int(token.user_id)
    response = HomeResponse()
    response.user_id = user_id
    web_user = await pcr_sqla.web_query_user(user_id)
    response.priority = effective_web_priority(user_id, web_user)
    response.is_superuser = is_hoshino_superuser(user_id)
    if response.is_superuser:
        response.status = "董事长"
    elif response.priority > 20:
        response.status = "站点管理员"

    if pcr_user := (await pcr_sqla.query_account(user_id)):
        pcr_user = pcr_user[0]
        response.name = pcr_user.name
    if groups := await pcr_sqla.get_member_group(user_id):
        response.clan = [group.dict() for group in groups]
    return response.dict()


@api_app.get("/{group_id}/dashboard")
async def dashboard_info(
    group_id: int,
    token: CookieCache = Depends(verify_cookie),
    member=Depends(verify_group_access),
):
    now = int(time.time())
    boss_info = clan_boss_info.boss_info
    user_id = int(token.user_id)
    response = DashboardResponse()
    response.boss = [
        BossInfoCounter(
            name=boss_info[i].name,
            id=boss_info[i].boss_id,
        )
        for i in range(5)
    ]
    response.user_id = user_id
    response.clan_priority = member.priority or 0
    web_user = await pcr_sqla.web_query_user(user_id)
    response.priority = effective_web_priority(user_id, web_user)

    if clan_info := clanbattle_info.get(group_id, None):
        response.clan_name = clan_info.clan_name
        response.stage = f"{clan_info.period}面{clan_info.lap_num}周目"
        response.rank = clan_info.rank
        response.name = clan_info.user_id
        if clan_info.loop_check:
            response.state = "开启" + (
                "(高占用)" if now - clan_info.loop_check > 30 else ""
            )
            response.boss = [
                BossInfoCounter(
                    name=boss_info[i].name,
                    id=boss_info[i].boss_id,
                    fighter=boss.fighter_num,
                    current_hp=boss.current_hp,
                    max_hp=boss.max_hp,
                    lap=boss.lap_num,
                )
                for i, boss in enumerate(clan_info.boss)
            ]
    if dao_data := await pcr_sqla.get_day_rcords(now, group_id):
        response.dao, response.report = await get_day_dao(dao_data)
    if dao_data := await pcr_sqla.get_day_rcords(now - 3600 * 24, group_id):
        response.yesterday_dao, _ = await get_day_dao(dao_data)

    if subscribes := await pcr_sqla.get_notice(NoticeType.subscribe.value, group_id):
        for subscribe in subscribes:
            response.boss[subscribe.boss - 1].subscribe += 1
    if applies := await pcr_sqla.get_notice(NoticeType.apply.value, group_id):
        for apply in applies:
            response.boss[apply.boss - 1].apply += 1
    if trees := await pcr_sqla.get_notice(NoticeType.tree.value, group_id):
        for tree in trees:
            response.boss[tree.boss - 1].tree += 1
    response.day_num = await pcr_sqla.get_clan_day(group_id)
    return response.dict()


@api_app.get("/{group_id}/notice")
async def clan_notice(
    group_id: int,
    token: CookieCache = Depends(verify_cookie),
    member=Depends(verify_group_access),
):
    user_id = int(token.user_id)
    response = NoticeResponse()
    response.user_id = user_id
    web_user = await pcr_sqla.web_query_user(user_id)
    response.priority = effective_web_priority(user_id, web_user)
    if subscribe := await pcr_sqla.get_notice(NoticeType.subscribe.value, group_id):
        response.subscribe = subscribe
    if apply := await pcr_sqla.get_notice(NoticeType.apply.value, group_id):
        response.apply = apply
    if tree := await pcr_sqla.get_notice(NoticeType.tree.value, group_id):
        response.tree = tree
    return response.dict()


@api_app.get("/{group_id}/report")
async def clan_report(
    group_id: int,
    token: CookieCache = Depends(verify_cookie),
    member=Depends(verify_group_access),
):
    user_id = int(token.user_id)
    response = ReportResponse()
    response.user_id = user_id
    web_user = await pcr_sqla.web_query_user(user_id)
    response.priority = effective_web_priority(user_id, web_user)
    if info := await pcr_sqla.get_all_records(group_id):
        players, all_damage, all_score = clanbattle_report(
            info, await pcr_sqla.get_max_dao(group_id)
        )
        response.all = [
            DaoInfo(
                name=member[1],
                damage=member[3],
                score=member[4],
                dao=member[2],
                damage_rate=f"{member[3]/all_damage*100:.2f}%",
                score_rate=f"{member[4]/all_score*100:.2f}%",
            )
            for member in players
        ]
        response.detail = [
            DaoInfo(
                name=player.name,
                damage=player.damage,
                score=int(
                    clan_boss_info.get_boss_rate(player.lap, player.boss)
                    * player.damage
                ),
                type=daoflag2str(player.flag),
                date=player.time,
                boss=player.boss,
                lap=player.lap,
                dao_id=player.battle_log_id,
            )
            for player in info[::-1]
        ]
    if pcr_user := (await pcr_sqla.query_account(user_id)):
        pcr_user = pcr_user[0]
        response.name = pcr_user.name
        if info := await pcr_sqla.get_player_records(pcr_user.viewer_id, 5, group_id):
            knife = 0
            for dao in info:
                knife += 1 if dao.flag == 0 else 0.5
                response.me.append(
                    DaoInfo(
                        dao=knife,
                        damage=dao.damage,
                        score=int(
                            clan_boss_info.get_boss_rate(dao.lap, dao.boss) * dao.damage
                        ),
                        type=daoflag2str(dao.flag),
                        boss=dao.boss,
                        lap=dao.lap,
                        date=dao.time,
                        dao_id=dao.battle_log_id,
                    )
                )
        response.me = response.me[::-1]
    return response.dict()


@api_app.post("/set_notice")
async def set_notice(notice: NoticeCache, token: CookieCache = Depends(verify_cookie)):
    user_id = int(token.user_id)
    await require_group_role(int(notice.group_id), token)
    notice.user_id = user_id
    if notice.notice_type == NoticeType.sl.value:
        if not await pcr_sqla.add_sl(
            SLDao(group_id=notice.group_id, user_id=user_id, time=int(time.time()))
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "已经sl过了")
    else:
        await pcr_sqla.add_notice(notice)
    notice_update_time[int(notice.group_id)] = int(time.time())
    notice_versions[int(notice.group_id)] = notice_versions.get(int(notice.group_id), 0) + 1
    await anywhere_send(
        get_notice_msg(
            notice.notice_type, user_id, notice.boss, notice.lap, notice.text
        ),
        group_id=notice.group_id,
    )
    await publish_group_notification(
        int(notice.group_id),
        "notice",
        "会战通知有更新",
        get_notice_msg(notice.notice_type, user_id, notice.boss, notice.lap, notice.text),
        f"/{notice.group_id}/noticetable",
    )
    return "成功"


@api_app.post("/delete_notice")
async def delete_notice(notice: NoticeCache, token: CookieCache = Depends(verify_cookie)):
    user_id = int(token.user_id)
    await require_group_role(int(notice.group_id), token)
    notice.user_id = user_id
    if notice.notice_type == NoticeType.sl.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "那你自己心里清楚")
    else:
        await pcr_sqla.delete_notice(
            notice.notice_type,
            notice.group_id,
            notice.boss,
            user_id=user_id,
        )
    notice_update_time[int(notice.group_id)] = int(time.time())
    notice_versions[int(notice.group_id)] = notice_versions.get(int(notice.group_id), 0) + 1
    await anywhere_send(
        cancel_notice_msg(notice.notice_type, user_id, notice.boss, user_id),
        group_id=notice.group_id,
    )
    await publish_group_notification(
        int(notice.group_id),
        "notice",
        "会战通知已取消",
        cancel_notice_msg(notice.notice_type, user_id, notice.boss, user_id),
        f"/{notice.group_id}/noticetable",
    )
    return "取消成功"


@api_app.post("/delete_notice_special")
async def delete_notice_special(
    notice: SpecialNoticeForm, token: CookieCache = Depends(verify_cookie)
):
    user_id = int(token.user_id)
    group_level, _, _ = await require_group_role(int(notice.group_id), token)
    web_user = await pcr_sqla.web_query_user(user_id)
    is_admin = is_hoshino_superuser(user_id) or group_level >= ROLE_FOREMAN or (
        web_user is not None and (web_user.priority or 0) > 20
    )
    if user_id != notice.user_id and not is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "只能删除自己的通知"
        )
    await pcr_sqla.delete_notice(
        notice.notice_type,
        notice.group_id,
        notice.boss,
        user_id=notice.user_id,
        lap=notice.lap,
    )
    notice_update_time[int(notice.group_id)] = int(time.time())
    notice_versions[int(notice.group_id)] = notice_versions.get(int(notice.group_id), 0) + 1
    await anywhere_send(
        cancel_notice_msg(
            notice.notice_type, notice.user_id, notice.boss, operator=user_id
        ),
        group_id=notice.group_id,
    )
    return "取消成功"


@api_app.get("/{group_id}/renew_dashboard")
async def renew_dashboard(
    group_id: int,
    token: CookieCache = Depends(verify_cookie),
    member=Depends(verify_group_access),
):
    async def dashboard_generator():
        last_report_time = int(time.time())
        last_notice_time = int(time.time())
        last_report_version = report_versions.get(group_id, 0)
        last_notice_version = notice_versions.get(group_id, 0)
        while True:
            await asyncio.sleep(10)
            current_report_version = report_versions.get(group_id, 0)
            current_notice_version = notice_versions.get(group_id, 0)
            if clan_info := clanbattle_info.get(group_id, None):
                if (
                    clan_info.dao_update_time > last_report_time
                    or current_report_version != last_report_version
                ):
                    last_report_time = clan_info.dao_update_time
                    last_report_version = current_report_version
                    yield json.dumps(await dashboard_info(group_id, token, member))
                    continue
            update_timestamp = notice_update_time.get(group_id, 0)
            if (
                update_timestamp > last_notice_time
                or current_notice_version != last_notice_version
            ):
                last_notice_time = update_timestamp
                last_notice_version = current_notice_version
                yield json.dumps(await dashboard_info(group_id, token, member))

    return EventSourceResponse(content=dashboard_generator())


@api_app.get("/{group_id}/renew_report")
async def renew_report(
    group_id: int,
    token: CookieCache = Depends(verify_cookie),
    member=Depends(verify_group_access),
):
    async def report_generator():
        last_report_time = int(time.time())
        last_report_version = report_versions.get(group_id, 0)
        while True:
            await asyncio.sleep(10)
            current_report_version = report_versions.get(group_id, 0)
            if clan_info := clanbattle_info.get(group_id, None):
                if (
                    clan_info.dao_update_time > last_report_time
                    or current_report_version != last_report_version
                ):
                    yield json.dumps(await clan_report(group_id, token, member))
                    last_report_time = clan_info.dao_update_time
                    last_report_version = current_report_version
            elif current_report_version != last_report_version:
                yield json.dumps(await clan_report(group_id, token, member))
                last_report_version = current_report_version

    return EventSourceResponse(content=report_generator())


@api_app.get("/{group_id}/renew_notice")
async def renew_notice(
    group_id: int,
    token: CookieCache = Depends(verify_cookie),
    member=Depends(verify_group_access),
):
    async def notice_generator():
        last_notice_time = int(time.time())
        last_notice_version = notice_versions.get(group_id, 0)
        while True:
            await asyncio.sleep(10)
            update_timestamp = notice_update_time.get(group_id, 0)
            current_notice_version = notice_versions.get(group_id, 0)
            if (
                update_timestamp > last_notice_time
                or current_notice_version != last_notice_version
            ):
                yield json.dumps(await clan_notice(group_id, token, member))
                last_notice_time = update_timestamp
                last_notice_version = current_notice_version

    return EventSourceResponse(content=notice_generator())


@api_app.post("/correct_dao")
async def correct_dao(correct: CorrectDaoInfo, token: CookieCache = Depends(verify_cookie)):
    user_id = int(token.user_id)
    group_level, _, _ = await require_group_role(correct.group_id, token)
    record = await pcr_sqla.get_history(correct.dao_id, correct.group_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有找到这条出刀记录")
    web_user = await pcr_sqla.web_query_user(user_id)
    accounts = await pcr_sqla.query_account(user_id)
    owns_record = any(
        account.viewer_id == record.pcrid for account in (accounts or [])
    )
    is_admin = is_hoshino_superuser(user_id) or group_level >= ROLE_FOREMAN or (
        web_user is not None and (web_user.priority or 0) > 20
    )
    if not owns_record and not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能修改自己的出刀记录")
    if await pcr_sqla.correct_dao(
        correct.dao_id,
        0 if correct.type == "完整刀" else 1 if correct.type == "尾刀" else 0.5,
        correct.group_id,
    ):
        report_versions[correct.group_id] = report_versions.get(correct.group_id, 0) + 1
        return "修改成功"
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "请检查你输入了正确的出刀编号")


app = api_app


@on_startup
async def kanna_web():
    web = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": app,
            "host": WebSetting.api_host.value,
            "port": int(WebSetting.api_port.value),
        },
        daemon=True,
    )
    web.start()
