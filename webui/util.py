import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple, Union
from nonebot import MessageSegment
from fastapi import Cookie, HTTPException, status

from ..basedata import NoticeType
from ..clanbattle.base import day_report
from ..database.dal import pcr_sqla
from ..database.models import ClanBattleMember, CookieCache, RecordDao


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
LOGIN_TICKET_TTL = 5 * 60
_login_tickets: Dict[str, Tuple[str, float]] = {}
_login_ticket_lock = threading.Lock()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return (
        f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    if not encoded.startswith(f"{PASSWORD_ALGORITHM}$"):
        # 兼容旧数据库；登录成功后会自动升级为哈希。
        return hmac.compare_digest(password, encoded)
    try:
        _, iterations, salt, expected = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (TypeError, ValueError):
        return False


def password_needs_upgrade(encoded: str) -> bool:
    return not encoded.startswith(f"{PASSWORD_ALGORITHM}$")


def create_login_ticket(account: str) -> str:
    ticket = secrets.token_urlsafe(32)
    expires_at = time.time() + LOGIN_TICKET_TTL
    with _login_ticket_lock:
        now = time.time()
        expired = [key for key, (_, expiry) in _login_tickets.items() if expiry <= now]
        for key in expired:
            _login_tickets.pop(key, None)
        _login_tickets[ticket] = (account, expires_at)
    return ticket


def consume_login_ticket(ticket: str) -> Optional[str]:
    with _login_ticket_lock:
        item = _login_tickets.pop(ticket, None)
    if not item or item[1] <= time.time():
        return None
    return item[0]


async def verify_cookie(token: str = Cookie(None)) -> CookieCache:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    if cookie := await pcr_sqla.web_query_cookie(token):
        if time.time() - cookie.time < 3600 * 7 * 24:
            return cookie
        await pcr_sqla.web_delete_cookie(token=token)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录过期")


async def require_group_member(
    group_id: int, token: CookieCache
) -> ClanBattleMember:
    member = await pcr_sqla.get_clan_member(group_id, int(token.user_id))
    if not member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "你不是该公会成员")
    return member


async def get_day_dao(dao_data: List[RecordDao]) -> Tuple[int, list]:
    total = 0
    state = {3: [], 2.5: [], 2: [], 1.5: [], 1: [], 0.5: [], 0: []}
    report_info = await day_report(dao_data, {})
    for member in report_info:
        name = member[1]
        dao = min(member[2], 3)
        state[dao].append(name)
        total += dao

    return total, [{"dao_num": dao, "names": state[dao]} for dao in state if state[dao]]


def get_notice_msg(type: int, user_id: int, boss: int, lap: int, msg: str) -> str:
    at_msg = MessageSegment.at(user_id)
    if type == NoticeType.subscribe.value:
        resp = "预约了"
    elif type == NoticeType.apply.value:
        resp = "申请了"
    elif type == NoticeType.tree.value:
        resp = "挂树在了"
    elif type == NoticeType.sl.value:
        return at_msg + "SL了" + (f"\n留言: {msg}" if msg else "")

    resp += f"第{lap}周目" if lap else "当前周目"
    resp += f"{boss}王"
    resp += f"\n留言: {msg}" if msg else ""
    return at_msg + resp


def cancel_notice_msg(
    type: int, user_id: int, boss: int, operator: int = 114514
) -> str:
    operator_msg = (MessageSegment.at(operator) + "使") if operator != user_id else ""
    if type == NoticeType.subscribe.value:
        resp = "取消预约了"
    elif type == NoticeType.apply.value:
        resp = "取消申请了"
    elif type == NoticeType.tree.value:
        resp = "取消挂树在了"
    resp += f"{boss}王"
    return operator_msg + MessageSegment.at(user_id) + resp
