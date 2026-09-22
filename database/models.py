from typing import Optional
from sqlmodel import Field, SQLModel
import time

from sqlalchemy.orm import registry

# 防止多个sqlmodel冲突


class DataBase(SQLModel, registry=registry()):
    pass


class Account(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    user_id: int = Field(title="玩家QQ")
    platform: int = Field(title="服务器编号")
    viewer_id: Optional[int] = Field(default=None, title="游戏ID")
    allow_others: Optional[int] = Field(default=0, title="允许他人触发")
    account: Optional[str] = Field(default=None, title="uid")
    password: Optional[str] = Field(default=None, title="access_key")
    name: Optional[str] = Field(default=None, title="游戏昵称")
    refresh: Optional[str] = Field(default=None, title="b站账号")


class WebAccount(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    account: str = Field(primary_key=True, title="玩家账号")
    password: str = Field(title="密码")
    temp: Optional[bool] = Field(title="临时", default=True)
    priority: Optional[int] = Field(title="权限等级", default=0)
    create_time: Optional[int] = Field(title="过期时间", default=0)


class WebNotificationSetting(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    user_id: int = Field(primary_key=True, title="玩家QQ")
    enabled: bool = Field(default=False, title="是否启用网页通知")
    delivery: int = Field(default=0, title="通知方式")
    group_ids: str = Field(default="[]", title="接收通知的群")
    event_types: str = Field(
        default='["notice", "report", "monitor", "arena", "role"]',
        title="接收的事件类型",
    )
    quiet_start: str = Field(default="", title="免打扰开始时间")
    quiet_end: str = Field(default="", title="免打扰结束时间")
    update_time: int = Field(default_factory=lambda: int(time.time()), title="更新时间")


class WebNotificationEvent(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    user_id: int = Field(index=True, title="接收用户QQ")
    group_id: Optional[int] = Field(default=None, index=True, title="所属群")
    event_type: str = Field(default="notice", title="事件类型")
    title: str = Field(title="标题")
    body: str = Field(default="", title="正文")
    url: str = Field(default="", title="网页地址")
    time: int = Field(default_factory=lambda: int(time.time()), title="创建时间")
    read: bool = Field(default=False, title="是否已读")


class RefreshAccount(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    account: str = Field(primary_key=True, title="b站账号")
    password: str = Field(title="b站密码")


class RecordDao(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    group_id: int = Field(title="所属群")
    battle_log_id: int = Field(title="出刀编号")
    lap: int = Field(title="周目")
    boss: int = Field(title="boss编号")
    time: int = Field(title="时间")
    pcrid: int = Field(title="玩家ID")
    damage: int = Field(title="伤害")
    name: str = Field(title="玩家昵称")
    remain_time: int = Field(title="战斗剩余时间")
    battle_time: int = Field(title="战斗时间")
    flag: float = Field(title="出刀类型")
    unit1: int = Field(title="出战角色1-ID")
    unit2: Optional[int] = Field(default=0, title="出战角色2-ID")
    unit3: Optional[int] = Field(default=0, title="出战角色3-ID")
    unit4: Optional[int] = Field(default=0, title="出战角色4-ID")
    unit5: Optional[int] = Field(default=0, title="出战角色5-ID")
    unit1_level: int = Field(title="出战角色1-等级")
    unit2_level: Optional[int] = Field(default=0, title="出战角色2-等级")
    unit3_level: Optional[int] = Field(default=0, title="出战角色3-等级")
    unit4_level: Optional[int] = Field(default=0, title="出战角色4-等级")
    unit5_level: Optional[int] = Field(default=0, title="出战角色5-等级")
    unit1_damage: int = Field(title="出战角色1-伤害")
    unit2_damage: Optional[int] = Field(default=0, title="出战角色2-伤害")
    unit3_damage: Optional[int] = Field(default=0, title="出战角色3-伤害")
    unit4_damage: Optional[int] = Field(default=0, title="出战角色4-伤害")
    unit5_damage: Optional[int] = Field(default=0, title="出战角色5-伤害")
    unit1_rarity: int = Field(title="出战角色1-星级")
    unit2_rarity: Optional[int] = Field(default=0, title="出战角色2-星级")
    unit3_rarity: Optional[int] = Field(default=0, title="出战角色3-星级")
    unit4_rarity: Optional[int] = Field(default=0, title="出战角色4-星级")
    unit5_rarity: Optional[int] = Field(default=0, title="出战角色5-星级")
    unit1_rank: int = Field(title="出战角色1-品级")
    unit2_rank: Optional[int] = Field(default=0, title="出战角色2-品级")
    unit3_rank: Optional[int] = Field(default=0, title="出战角色3-品级")
    unit4_rank: Optional[int] = Field(default=0, title="出战角色4-品级")
    unit5_rank: Optional[int] = Field(default=0, title="出战角色5-品级")
    unit1_unique_equip: int = Field(title="出战角色1-专武等级")
    unit2_unique_equip: Optional[int] = Field(default=0, title="出战角色2-专武等级")
    unit3_unique_equip: Optional[int] = Field(default=0, title="出战角色3-专武等级")
    unit4_unique_equip: Optional[int] = Field(default=0, title="出战角色4-专武等级")
    unit5_unique_equip: Optional[int] = Field(default=0, title="出战角色5-专武等级")


class NoticeCache(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    group_id: int = Field(title="所属群")
    notice_type: int = Field(title="通知类型")
    user_id: int = Field(title="用户QQ")
    boss: int = Field(title="Boss编号")
    lap: Optional[int] = Field(default=0, title="周目")
    text: str = Field(title="留言")
    time: Optional[int] = Field(default=0, title="时间")


class SLDao(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    group_id: int = Field(primary_key=True, title="所属群")
    user_id: int = Field(primary_key=True, title="用户QQ")
    time: Optional[int] = Field(default=0, title="上次SL")


class ClanBattleKPI(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    group_id: int = Field(primary_key=True, title="所属群")
    pcrid: int = Field(primary_key=True, title="游戏ID")
    bouns: int = Field(title="补正")
    time: Optional[int] = Field(default=0, title="创建时间")


class PlayerUnit(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    user_id: int = Field(title="玩家QQ")
    pcrid: int = Field(title="玩家ID")
    unit_id: int = Field(title="角色ID")
    name: str = Field(title="玩家昵称")
    rarity: int = Field(title="星级")
    battle_rarity: Optional[int] = Field(default=0, title="战斗星级")
    unique_level: Optional[int] = Field(default=0, title="专武等级")
    unique_level2: Optional[int] = Field(default=0, title="专武等级2")
    love_level: int = Field(title="好感等级")
    level: int = Field(title="等级")
    rank: int = Field(title="品级")
    main_1: Optional[int] = Field(default=0, title="1技能")
    main_2: Optional[int] = Field(default=0, title="2技能")
    ex: Optional[int] = Field(default=0, title="ex技能")
    union_burst: Optional[int] = Field(default=0, title="连结爆发")
    equip_1: str = Field(title="左上")
    equip_2: str = Field(title="右上")
    equip_3: str = Field(title="左中")
    equip_4: str = Field(title="右中")
    equip_5: str = Field(title="左下")
    equip_6: str = Field(title="右下")
    support_position: Optional[int] = Field(
        default=0, title="支援位置"
    )  # 1, 2 好友支援， 3-6 工会战地下城支援
    cb_ex_equip_1: Optional[int] = Field(default=0, title="会战ex装备1")
    cb_ex_equip_2: Optional[int] = Field(default=0, title="会战ex装备2")
    cb_ex_equip_3: Optional[int] = Field(default=0, title="会战ex装备3")
    cb_ex_equip_1_level: Optional[int] = Field(default=0, title="会战ex装备1等级")
    cb_ex_equip_2_level: Optional[int] = Field(default=0, title="会战ex装备2等级")
    cb_ex_equip_3_level: Optional[int] = Field(default=0, title="会战ex装备3等级")


class SupportUnit(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    group_id: int = Field(title="所属群")
    pcrid: int = Field(title="玩家ID")
    unit_id: int = Field(title="角色ID")
    name: str = Field(title="玩家昵称")
    rarity: int = Field(title="星级")
    battle_rarity: Optional[int] = Field(default=0, title="战斗星级")
    unique_level: Optional[int] = Field(default=0, title="专武等级")
    unique_level2: Optional[int] = Field(default=0, title="专武等级2")
    special_attribute: Optional[str] = Field(default="", title="好感加成")
    level: int = Field(title="等级")
    rank: int = Field(title="品级")
    main_1: Optional[int] = Field(default=0, title="1技能")
    main_2: Optional[int] = Field(default=0, title="2技能")
    ex: Optional[int] = Field(default=0, title="ex技能")
    union_burst: Optional[int] = Field(default=0, title="连结爆发")
    equip_1: str = Field(title="左上")
    equip_2: str = Field(title="右上")
    equip_3: str = Field(title="左中")
    equip_4: str = Field(title="右中")
    equip_5: str = Field(title="左下")
    equip_6: str = Field(title="右下")
    cb_ex_equip_1: Optional[int] = Field(default=0, title="会战ex装备1")
    cb_ex_equip_2: Optional[int] = Field(default=0, title="会战ex装备2")
    cb_ex_equip_3: Optional[int] = Field(default=0, title="会战ex装备3")
    cb_ex_equip_1_level: Optional[int] = Field(default=0, title="会战ex装备1等级")
    cb_ex_equip_2_level: Optional[int] = Field(default=0, title="会战ex装备2等级")
    cb_ex_equip_3_level: Optional[int] = Field(default=0, title="会战ex装备3等级")


class ClanBattleMember(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    group_id: int = Field(primary_key=True, title="所属群")
    user_id: int = Field(primary_key=True, title="玩家QQ")
    group_name: str = Field(title="群名称", default="环奈连结")
    priority: Optional[int] = Field(title="权限等级", default=0)


class BlackUnit(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    user_id: int = Field(title="玩家QQ")
    black_id: str = Field(title="黑名单id")
    black_type: int = Field(title="黑名单类型")


class ArenaSetting(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    user_id: int = Field(primary_key=True, title="玩家QQ")
    jjc_notice: bool = Field(default=True, title="竞技场提醒")
    grand_notice: bool = Field(default=True, title="公主竞技场提醒")


class GrandDefenceCache(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    pcrid: int = Field(primary_key=True, title="玩家ID")
    grand_id: int = Field(title="场次")
    defence: str = Field(title="防守队伍id")
    row: int = Field(primary_key=True, title="防守位置")
    user_id: int = Field(primary_key=True, title="玩家QQ")
    vs_time: int = Field(default=0, title="更新时间")


class CookieCache(DataBase, table=True):
    __table_args__ = {"keep_existing": True}
    token: str = Field(primary_key=True, title="token")
    user_id: str = Field(title="user_id")
    time: int = Field(default_factory=lambda: int(time.time()), title="时间")
