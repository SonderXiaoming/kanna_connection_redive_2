from typing import List, Optional
from pydantic import BaseModel, Field

from ..database.models import NoticeCache


class User(BaseModel):
    account: Optional[str] = None
    password: Optional[str] = None
    ticket: Optional[str] = None


class ClanInfo(BaseModel):
    group_id: int
    group_name: str = "环奈连结"
    priority: int = 0
    role: str = "员工"


class PcrAccountInfo(BaseModel):
    platform: int
    viewer_id: Optional[int] = None
    name: Optional[str] = None
    allow_others: int = 0


class NotificationSettingForm(BaseModel):
    enabled: bool = False
    delivery: int = 0
    group_ids: List[int] = Field(default_factory=list)
    event_types: List[str] = Field(
        default_factory=lambda: ["notice", "report", "monitor", "arena", "role"]
    )
    quiet_start: str = ""
    quiet_end: str = ""


class PcrAccountBindingForm(BaseModel):
    platform: int
    account: Optional[str] = None
    password: Optional[str] = None
    viewer_id: Optional[int] = None
    transfer_code: Optional[str] = None


class PcrAccountAccessForm(BaseModel):
    allow_others: int


class GameAccountForm(BaseModel):
    platform: int = 0


class SupportChangeForm(GameAccountForm):
    unit_id: int
    mode: int


class SupportRemoveForm(GameAccountForm):
    unit_id: int


class ArenaSettingForm(BaseModel):
    jjc_notice: bool = True
    grand_notice: bool = True


class GameUnitInfo(BaseModel):
    unit_id: int
    name: str = ""
    owner_name: str = ""
    owner_id: Optional[int] = None
    rarity: int = 0
    battle_rarity: int = 0
    level: int = 0
    rank: int = 0
    unique_level: int = -1
    unique_level2: int = -1
    love_level: int = 0
    support_position: int = 0
    support_mode: str = ""
    special_attribute: str = ""
    equipment: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)


class GameUnitPage(BaseModel):
    units: List[GameUnitInfo] = Field(default_factory=list)
    page: int = 1
    page_size: int = 60
    total: int = 0
    cache_name: str = ""
    cache_viewer_id: Optional[int] = None


class ArenaSummaryInfo(BaseModel):
    rank: int = 0
    group: int = 0
    highest_rank: int = 0
    season_highest_rank: int = 0
    battle_number: int = 0
    max_battle_number: int = 0
    interval_end_time: int = 0


class ArenaOverviewResponse(BaseModel):
    account: PcrAccountInfo
    arena: ArenaSummaryInfo = Field(default_factory=ArenaSummaryInfo)
    grand_arena: ArenaSummaryInfo = Field(default_factory=ArenaSummaryInfo)
    settings: ArenaSettingForm = Field(default_factory=ArenaSettingForm)
    source_user_id: int = 0
    monitored: bool = False
    can_manage: bool = False


class ArenaSourceInfo(BaseModel):
    source_user_id: int
    platform: int
    viewer_id: Optional[int] = None
    account_name: str = ""
    own: bool = False
    monitored: bool = False
    last_check: int = 0
    arena_group: int = 0
    arena_rank: int = 0
    grand_group: int = 0
    grand_rank: int = 0


class ArenaSourcesResponse(BaseModel):
    sources: List[ArenaSourceInfo] = Field(default_factory=list)


class ArenaMonitorStartForm(GameAccountForm):
    pass


class ArenaRankPlayerInfo(BaseModel):
    viewer_id: int
    rank: int = 0
    user_name: str = ""
    team_level: int = 0
    winning_number: Optional[int] = None
    favorite_unit_id: int = 1000
    favorite_unit_rarity: int = 0
    defence: List[List[int]] = Field(default_factory=list)


class ArenaRankingResponse(BaseModel):
    arena_type: str = "arena"
    page: int = 1
    source_user_id: int = 0
    group: int = 0
    players: List[ArenaRankPlayerInfo] = Field(default_factory=list)


class ArenaPlayerProfileInfo(BaseModel):
    viewer_id: int
    user_name: str = ""
    team_level: int = 0
    clan_name: str = ""
    favorite_unit_id: int = 1000
    favorite_unit_rarity: int = 0
    arena_rank: int = 0
    arena_group: int = 0
    grand_arena_rank: int = 0
    grand_arena_group: int = 0


class UserResponse(BaseModel):
    priority: int = 0
    is_superuser: bool = False
    user_id: int = 0
    accounts: List[PcrAccountInfo] = Field(default_factory=list)
    clan: List[ClanInfo] = Field(default_factory=list)
    notification: NotificationSettingForm = Field(
        default_factory=NotificationSettingForm
    )


class AdminUserUpdateForm(BaseModel):
    priority: int


class AdminUserInfo(BaseModel):
    account: str
    priority: int = 0
    temp: bool = False
    create_time: int = 0
    is_superuser: bool = False
    pcr_accounts: int = 0
    clans: int = 0
    active_sessions: int = 0


class AdminUsersResponse(BaseModel):
    users: List[AdminUserInfo] = Field(default_factory=list)


class GroupSummary(BaseModel):
    group_id: int
    group_name: str = "环奈连结"
    member_count: int = 0
    role: str = "员工"
    role_level: int = 1
    bot_online: bool = False


class ClanMemberInfo(BaseModel):
    user_id: int
    nickname: str = ""
    card: str = ""
    qq_role: str = "member"
    role: str = "员工"
    role_level: int = 1
    delegated: bool = False
    join_time: int = 0
    last_sent_time: int = 0
    game_name: str = ""
    viewer_id: Optional[int] = None
    platform: Optional[int] = None
    dao_count: float = 0
    last_dao_time: int = 0


class GroupRoleUpdateForm(BaseModel):
    role: str


class ClanManagementResponse(BaseModel):
    group: GroupSummary
    members: List[ClanMemberInfo] = Field(default_factory=list)
    can_manage_roles: bool = False
    can_operate: bool = False


class MonitorStartForm(BaseModel):
    account_user_id: Optional[int] = None
    platform: int = 0


class MonitorStateInfo(BaseModel):
    running: bool = False
    operator_id: Optional[int] = None
    operator_name: str = ""
    loop_num: int = 0
    last_check: int = 0
    error_count: int = 0
    rank: int = 0
    stage: str = "暂无信息"


class OperationAccountInfo(PcrAccountInfo):
    user_id: int
    owner_name: str = ""


class KpiUpdateForm(BaseModel):
    pcrid: int
    bonus: int


class KpiInfo(BaseModel):
    pcrid: int
    name: str = ""
    bonus: int = 0
    time: int = 0


class ClanOperationsResponse(BaseModel):
    role: str = "员工"
    role_level: int = 1
    can_operate: bool = False
    monitor: MonitorStateInfo = Field(default_factory=MonitorStateInfo)
    accounts: List[OperationAccountInfo] = Field(default_factory=list)
    kpis: List[KpiInfo] = Field(default_factory=list)
    subscribe_count: int = 0
    apply_count: int = 0
    tree_count: int = 0


class AnalyticsMemberInfo(BaseModel):
    pcrid: int
    name: str = ""
    dao: float = 0
    damage: int = 0
    full_count: int = 0
    tail_count: int = 0
    compensate_count: int = 0
    last_dao_time: int = 0


class AnalyticsBossInfo(BaseModel):
    boss: int
    dao: float = 0
    damage: int = 0


class AnalyticsTrendInfo(BaseModel):
    date: str
    dao: float = 0
    damage: int = 0


class AnalyticsCompositionInfo(BaseModel):
    units: List[int] = Field(default_factory=list)
    uses: int = 0
    damage: int = 0


class ClanAnalyticsResponse(BaseModel):
    total_damage: int = 0
    total_dao: float = 0
    members: List[AnalyticsMemberInfo] = Field(default_factory=list)
    bosses: List[AnalyticsBossInfo] = Field(default_factory=list)
    trends: List[AnalyticsTrendInfo] = Field(default_factory=list)
    compositions: List[AnalyticsCompositionInfo] = Field(default_factory=list)


class NotificationEventInfo(BaseModel):
    id: int
    group_id: Optional[int] = None
    event_type: str
    title: str
    body: str = ""
    url: str = ""
    time: int
    read: bool = False


class NotificationInboxResponse(BaseModel):
    events: List[NotificationEventInfo] = Field(default_factory=list)
    unread: int = 0


class BossInfoCounter(BaseModel):
    name: str = ""
    id: int = 0
    current_hp: int = 0
    max_hp: int = 0
    lap: int = 0
    subscribe: int = 0
    apply: int = 0
    fighter: int = 0
    tree: int = 0


class HomeResponse(BaseModel):
    priority: int = 0
    is_superuser: bool = False
    user_id: int = 0
    name: str = "无？你绑定账号了嘛？"
    status: str = "成员"
    saying: str = (
        "我们不必为他人隐藏本性而感到愤怒，因为你自己也在隐藏本性。——拉罗什富科《箴言集》"
    )
    clan: List[ClanInfo] = Field(default_factory=list)


class DashboardResponse(BaseModel):
    priority: int = 0
    clan_priority: int = 0
    user_id: int = 1791800364
    name: str = "无"
    clan_name: str = "环奈连结"
    stage: str = "暂无信息"
    dao: int = 0
    yesterday_dao: int = 0
    rank: int = 114514
    state: str = "关闭"
    boss: List[BossInfoCounter] = []
    report: list = []
    day_num: int = 0


class NoticeResponse(BaseModel):
    priority: int = 0
    user_id: int = 1791800364
    subscribe: List[NoticeCache] = []
    apply: List[NoticeCache] = []
    tree: List[NoticeCache] = []


class DaoInfo(BaseModel):
    name: str = ""
    damage: int = 0
    score: int = 0
    type: str = ""
    date: int = 0
    boss: int = 0
    lap: int = 0
    dao_id: int = 0
    damage_rate: str = ""
    score_rate: str = ""
    dao: float = 0


class ReportResponse(BaseModel):
    priority: int = 0
    user_id: int = 1791800364
    name: str = ""
    all: List[DaoInfo] = []
    detail: List[DaoInfo] = []
    me: List[DaoInfo] = []


class SpecialNoticeForm(BaseModel):
    group_id: str
    boss: int
    notice_type: int
    lap: int
    user_id: int


class CorrectDaoInfo(BaseModel):
    type: str
    dao_id: int
    group_id: int
