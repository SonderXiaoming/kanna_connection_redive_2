import secrets
from hoshino import Service
from nonebot import NoticeSession, on_command, logger
from ..database.dal import pcr_sqla
from ..database.models import WebAccount
from ..setting import WebSetting
from .api import *
from .util import create_login_ticket, hash_password

sv = Service(
    name="环奈网页端管理",  # 功能名
    visible=False,  # 可见性
    enable_on_default=True,  # 默认启用
)


@on_command("apply_login", aliases=("网页端登录"), only_to_me=True)
async def apply_login(session: NoticeSession):
    qq_id = str(session.ctx.user_id)
    if not await pcr_sqla.web_query_user(qq_id):
        await pcr_sqla.web_add_user(
            WebAccount(
                account=qq_id,
                password=hash_password(secrets.token_urlsafe(24)),
            )
        )
    ticket = create_login_ticket(qq_id)
    await session.send(
        f"{WebSetting.web_public_url.value.rstrip('/')}/login?ticket={ticket}",
        ensure_private=True,
    )


@on_command("set_web_password", aliases=("设置网页密码",), only_to_me=True)
async def set_web_password(session: NoticeSession):
    content = session.ctx.message.extract_plain_text().split(maxsplit=1)
    if len(content) != 2 or not content[1].strip():
        await session.send("格式：设置网页密码 新密码", ensure_private=True)
        return

    password = content[1].strip()
    if len(password) < 8:
        await session.send("网页密码至少需要 8 个字符", ensure_private=True)
        return
    if len(password) > 128:
        await session.send("网页密码不能超过 128 个字符", ensure_private=True)
        return

    qq_id = str(session.ctx.user_id)
    await pcr_sqla.web_add_user(
        WebAccount(
            account=qq_id,
            password=hash_password(password),
            temp=False,
        )
    )
    await session.send("网页密码设置成功", ensure_private=True)
