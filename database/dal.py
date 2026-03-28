import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from sqlalchemy import asc, delete, desc, insert, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, func

from ..basedata import FilePath, NoticeType
from .models import (
    Account,
    ArenaSetting,
    ClanBattleKPI,
    ClanBattleMember,
    CookieCache,
    DataBase,
    GrandDefenceCache,
    NoticeCache,
    PlayerUnit,
    RecordDao,
    RefreshAccount,
    SLDao,
    SupportUnit,
    WebAccount,
)


def pcr_date(timeStamp: int) -> datetime:
    now = datetime.fromtimestamp(timeStamp, tz=timezone(timedelta(hours=8)))
    if now.hour < 5:
        now -= timedelta(days=1)
    return now.replace(hour=5, minute=0, second=0, microsecond=0)  # 用5点做基准


class SQALA:
    def __init__(self, url: str):
        self.url = f"sqlite+aiosqlite:///{url}"
        self.engine = create_async_engine(
            self.url,
            pool_recycle=1500,  # 连接回收时间
            pool_pre_ping=True,  # 使用前检查连接是否有效
            echo=False,  # 关闭 SQL 日志减少内存
        )
        self.async_session = sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def refresh(self, table: SQLModel, day: int, group_id: Optional[int] = 0):
        async with self.async_session() as session:
            async with session.begin():
                date = pcr_date(datetime.now().timestamp())
                time = date - timedelta(days=day)
                sql = delete(table).where(table.time < time.timestamp())
                if group_id:
                    sql = sql.filter(table.group_id == group_id)
                await session.execute(sql)

    async def create_all(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(DataBase.metadata.create_all)

    # 账号部分
    async def query_account(self, user_id: int) -> List[Account]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(Account).where(Account.user_id == user_id)
                )
                return result.scalars().all()

    async def add_account(self, user_id: int, account: dict):
        async with self.async_session() as session:
            async with session.begin():
                if await self.query_account(user_id):
                    await session.execute(
                        update(Account)
                        .where(Account.user_id == user_id)
                        .values(**account)
                    )
                else:
                    await session.execute(insert(Account).values(**account))

    async def change_access(self, user_id: int, level: int):
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    update(Account)
                    .where(Account.user_id == user_id)
                    .values(allow_others=level)
                )

    async def query_refresh(self, account: str) -> RefreshAccount:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(RefreshAccount).where(RefreshAccount.account == account)
                )
                return result.scalar_one_or_none()

    async def add_refresh(self, account: RefreshAccount):
        async with self.async_session() as session:
            async with session.begin():
                await session.merge(account)

    # 会战部分
    async def add_record(self, dao_list: List[RecordDao]):
        async with self.async_session() as session:
            async with session.begin():
                session.add_all(dao_list)

    async def get_history(self, id: int, group_id: int) -> RecordDao:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(RecordDao).where(
                        RecordDao.battle_log_id == id, RecordDao.group_id == group_id
                    )
                )
                return result.scalars().one_or_none()

    async def get_latest_time(self, group_id: int) -> int:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.max(RecordDao.time)).where(
                        RecordDao.group_id == group_id
                    )
                )
                return result.fetchone()[0] or 0

    async def get_player_records(
        self, pcrid: int, day: int, group_id: int
    ) -> List[RecordDao]:
        latest_time = await self.get_latest_time(group_id)
        async with self.async_session() as session:
            async with session.begin():
                date = pcr_date(latest_time)
                start_day = date - timedelta(days=day)
                result = await session.execute(
                    select(RecordDao)
                    .where(
                        RecordDao.time >= start_day.timestamp(),
                        RecordDao.time <= latest_time,
                        RecordDao.pcrid == pcrid,
                        RecordDao.group_id == group_id,
                    )
                    .order_by(asc(RecordDao.time))
                )
                return result.scalars().all()

    async def get_clan_day(self, group_id: int) -> int:
        latest_time = await self.get_latest_time(group_id)
        async with self.async_session() as session:
            async with session.begin():
                date = pcr_date(latest_time)
                start_day = date - timedelta(days=5)
                result = await session.execute(
                    select(func.min(RecordDao.time)).where(
                        RecordDao.time >= start_day.timestamp(),
                        RecordDao.time <= latest_time,
                        RecordDao.group_id == group_id,
                    )
                )
                time = result.fetchone()[0] or 0
                return ((latest_time - time) // (3600 * 24)) + 1

    async def get_max_dao(self, group_id: int) -> int:
        day = await self.get_clan_day(group_id)
        return day * 3

    async def get_all_records(self, group_id: int) -> List[RecordDao]:
        latest_time = await self.get_latest_time(group_id)
        async with self.async_session() as session:
            async with session.begin():
                date = pcr_date(latest_time)
                start_day = date - timedelta(days=5)
                result = await session.execute(
                    select(RecordDao).where(
                        RecordDao.time >= start_day.timestamp(),
                        RecordDao.time <= latest_time,
                        RecordDao.group_id == group_id,
                    )
                )
                return result.scalars().all()

    async def get_day_rcords(self, timestamp: int, group_id: int) -> List[RecordDao]:
        date = pcr_date(timestamp)
        tomorrow = date + timedelta(days=1)
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(RecordDao).where(
                        RecordDao.time >= date.timestamp(),
                        RecordDao.time <= tomorrow.timestamp(),
                        RecordDao.group_id == group_id,
                    )
                )
                return result.scalars().all()

    async def clanbattle_name2pcrid(self, group_id: int, name: str) -> List[int]:
        latest_time = await self.get_latest_time(group_id)
        date = pcr_date(latest_time)
        start_day = date - timedelta(days=5)
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(RecordDao.pcrid)
                    .where(
                        RecordDao.time >= start_day.timestamp(),
                        RecordDao.time <= latest_time,
                        RecordDao.name == name,
                        RecordDao.group_id == group_id,
                    )
                    .distinct()
                )
                return result.scalars().all()

    async def correct_dao(self, dao_id: int, flag: int, group_id: int):
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(RecordDao).where(
                        RecordDao.battle_log_id == dao_id,
                        RecordDao.group_id == group_id,
                    )
                )
                if result.scalar_one_or_none():
                    await session.execute(
                        update(RecordDao)
                        .where(
                            RecordDao.battle_log_id == dao_id,
                            RecordDao.group_id == group_id,
                        )
                        .values(flag=flag)
                    )
                    return True
        return False

    # 通知部分
    async def get_notice(
        self,
        item: int,
        group_id: int,
        boss: Optional[int] = None,
        lap: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[NoticeCache]:
        async with self.async_session() as session:
            async with session.begin():
                sql = select(NoticeCache).where(
                    NoticeCache.notice_type == item,
                    NoticeCache.group_id == group_id,
                    NoticeCache.time - int(time.time()) <= 24 * 3600,
                )
                if boss:
                    sql = sql.filter(NoticeCache.boss == boss)
                if lap:
                    sql = sql.filter(NoticeCache.lap <= lap)
                if user_id:
                    sql = sql.filter(NoticeCache.user_id == user_id)
                result = await session.execute(sql)
                return result.scalars().all()

    async def delete_notice(
        self,
        item: int,
        group_id: int,
        boss: Optional[int] = None,
        user_id: Optional[int] = None,
        lap: Optional[int] = None,
    ):
        async with self.async_session() as session:
            async with session.begin():
                sql = delete(NoticeCache).where(
                    NoticeCache.notice_type == item, NoticeCache.group_id == group_id
                )
                if boss:
                    sql = sql.filter(NoticeCache.boss == boss)
                if lap:
                    sql = sql.filter(NoticeCache.lap <= lap)
                if user_id:
                    sql = sql.filter(NoticeCache.user_id == user_id)
                await session.execute(sql)

    async def add_notice(self, notice: NoticeCache):
        async with self.async_session() as session:
            async with session.begin():
                notice.time = int(time.time())
                if notice.notice_type == NoticeType.subscribe.value:
                    if await self.get_notice(
                        notice.notice_type,
                        notice.group_id,
                        notice.boss,
                        user_id=notice.user_id,
                    ):
                        await session.execute(
                            update(NoticeCache)
                            .where(
                                NoticeCache.notice_type == notice.notice_type,
                                NoticeCache.group_id == notice.group_id,
                                NoticeCache.boss == notice.boss,
                                NoticeCache.user_id == notice.user_id,
                            )
                            .values(text=notice.text, lap=notice.lap)
                        )
                        return
                elif await self.get_notice(
                    notice.notice_type, notice.group_id, user_id=notice.user_id
                ):
                    await session.execute(
                        update(NoticeCache)
                        .where(
                            NoticeCache.notice_type == notice.notice_type,
                            NoticeCache.group_id == notice.group_id,
                            NoticeCache.user_id == notice.user_id,
                        )
                        .values(boss=notice.boss, text=notice.text, time=notice.time)
                    )
                    return

                await session.merge(notice)

    async def add_sl(self, sl: SLDao) -> bool:
        async with self.async_session() as session:
            async with session.begin():
                if await self.check_sl(sl.user_id, sl.group_id):
                    return False
                await session.merge(sl)
                return True

    async def check_sl(self, uid: int, group_id: int) -> bool:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(SLDao).where(
                        SLDao.user_id == uid,
                        SLDao.group_id == group_id,
                        SLDao.time > pcr_date(datetime.now().timestamp()).timestamp(),
                    )
                )
                return bool(result.scalar_one_or_none())

    async def get_kpis(self, group_id: int) -> List[ClanBattleKPI]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(ClanBattleKPI).where(ClanBattleKPI.group_id == group_id)
                )
                return result.scalars().all()

    async def add_kpi_special(self, kpi: ClanBattleKPI):
        async with self.async_session() as session:
            async with session.begin():
                kpi.time = int(time.time())
                await session.merge(kpi)

    async def delete_kpi(self, group_id: int, pcrid: Optional[int] = None):
        async with self.async_session() as session:
            async with session.begin():
                sql = delete(ClanBattleKPI).where(ClanBattleKPI.group_id == group_id)
                if pcrid:
                    sql = sql.filter(ClanBattleKPI.pcrid == pcrid)
                await session.execute(sql)

    # BOX部分
    async def refresh_player_units(self, unit_list: List[PlayerUnit], user_id: int):
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    delete(PlayerUnit).where(PlayerUnit.user_id == user_id)
                )
                session.add_all(unit_list)

    async def get_player_units(self, user_id: int) -> List[PlayerUnit]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(PlayerUnit).where(PlayerUnit.user_id == user_id)
                )
                return result.scalars().all()

    async def get_player_support_units(self, user_id: int) -> List[PlayerUnit]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(PlayerUnit).where(
                        PlayerUnit.user_id == user_id, PlayerUnit.support_position != 0
                    )
                )
                return result.scalars().all()

    async def refresh_support_units(
        self, support_list: List[SupportUnit], group_id: int
    ):
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    delete(SupportUnit).where(SupportUnit.group_id == group_id)
                )
                session.add_all(support_list)

    async def get_support_units(self, group_id: int) -> List[SupportUnit]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(SupportUnit).where(SupportUnit.group_id == group_id)
                )
                return result.scalars().all()

    # 成员部分
    async def add_member(self, member: ClanBattleMember):
        async with self.async_session() as session:
            async with session.begin():
                await session.merge(member)

    async def delete_member(self, group_id: int, user_id: int):
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    delete(ClanBattleMember).where(
                        ClanBattleMember.user_id == user_id,
                        ClanBattleMember.group_id == group_id,
                    )
                )

    async def get_group_member(self, group_id: int) -> List[ClanBattleMember]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(ClanBattleMember).where(
                        ClanBattleMember.group_id == group_id
                    )
                )
                return result.scalars().all()

    async def get_member_group(self, user_id: int) -> List[ClanBattleMember]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(ClanBattleMember).where(ClanBattleMember.user_id == user_id)
                )
                return result.scalars().all()

    # 竞技场设置
    async def init_jjc_setting(self, user_setting: ArenaSetting):
        async with self.async_session() as session:
            async with session.begin():
                if not await self.get_jjc_setting(user_setting.user_id):
                    session.add(user_setting)

    async def get_jjc_setting(self, user_id: int) -> Union[ArenaSetting, None]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(ArenaSetting).where(ArenaSetting.user_id == user_id)
                )
                return result.scalar_one_or_none()

    async def update_jjc_setting(self, user_id: int, update_valuse: dict):
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    update(ArenaSetting)
                    .where(ArenaSetting.user_id == user_id)
                    .values(**update_valuse)
                )

    # 公主竞技场防守缓存

    async def add_grand_cache(self, historys: List[GrandDefenceCache]):
        if not historys:
            return
        async with self.async_session() as session:
            async with session.begin():
                for history in historys[::-1]:
                    await session.merge(history)

    async def query_grand_cache(self, pcrid: int, row: int) -> Union[int, None]:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(GrandDefenceCache.defence)
                    .where(
                        GrandDefenceCache.pcrid == pcrid, GrandDefenceCache.row == row
                    )
                    .order_by(desc(GrandDefenceCache.vs_time))
                )
                return int(result) if (result := result.scalars().first()) else result

    async def cache_latest_time(self, user_id: int) -> int:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.max(GrandDefenceCache.vs_time)).where(
                        GrandDefenceCache.user_id == user_id
                    )
                )
                return result.scalar_one_or_none() or 0

    # Web
    async def web_check_user(self, account: str, password: str) -> WebAccount:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(WebAccount).where(
                        WebAccount.account == account, WebAccount.password == password
                    )
                )
                return result.scalar_one_or_none()

    async def web_query_user(self, account) -> WebAccount:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(WebAccount).where(WebAccount.account == account)
                )
                return result.scalar_one_or_none()

    async def web_add_user(self, account: WebAccount):
        async with self.async_session() as session:
            async with session.begin():
                account.create_time = time.time()
                if user := await self.web_check_user(account.account, account.password):
                    account.priority = user.priority
                await session.merge(account)

    async def web_add_cookie(self, token: str, user_id: str):
        async with self.async_session() as session:
            async with session.begin():
                await session.merge(CookieCache(token=token, user_id=user_id))

    async def web_delete_cookie(
        self, token: Optional[str] = None, user_id: Optional[str] = None
    ):
        async with self.async_session() as session:
            async with session.begin():
                if not token or user_id:
                    raise ValueError("需要指定token或者user")
                sql = delete(CookieCache)
                if token:
                    sql = sql.filter(CookieCache.token == token)
                if user_id:
                    sql = sql.filter(CookieCache.user_id == user_id)
                await session.execute(sql)

    async def web_query_cookie(self, token: str) -> CookieCache:
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(CookieCache).where(CookieCache.token == token)
                )
                return result.scalar_one_or_none()


pcr_sqla = SQALA(str(FilePath.data.value / "data.db"))
