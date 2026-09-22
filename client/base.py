from base64 import b64decode
from hashlib import md5
from random import randint
from typing import Tuple, Union
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import httpx
import asyncio
from msgpack import unpackb
from .request import (
    RequestBase,
    LoadIndexRequest,
    HomeIndexRequest,
    ClanInfoRequest,
    ClanBattleTopRequest,
    ClanBattleReloadDetailInfoRequest,
    ClanBattleLogListRequest,
    ClanBattleTimeLineReportRequest,
    ClanBattleSupportUnitList2Request,
    ArenaInfoRequest,
    ArenaRankingRequest,
    GrandArenaRankingRequest,
    GrandArenaInfoRequest,
    GrandArenaHistoryRequest,
    GrandArenaHistoryDetailRequest,
    ProfileGetRequest,
    SupportUnitChangeSettingRequest,
    SupportUnitGetSettingRequest,
)
from .response import (
    LoadIndexResponse,
    HomeIndexResponse,
    ClanInfoResponse,
    ClanBattleTopResponse,
    ClanBattleReloadDetailInfoResponse,
    ClanBattleLogListResponse,
    ClanBattleTimeLineReportResponse,
    ClanBattleSupportUnitList2Response,
    ArenaInfoResponse,
    ArenaRankingResponse,
    GrandArenaRankingResponse,
    GrandArenaInfoResponse,
    GrandArenaHistoryResponse,
    GrandArenaHistoryDetailResponse,
    ProfileGetResponse,
    SupportUnitChangeSettingResponse,
    SupportUnitGetSettingResponse,
)


class BaseClient:
    def __init__(self):
        self.viewer_id: int = 0
        self.call_lock: asyncio.Lock = asyncio.Lock()
        self.headers: dict = None
        self.client: httpx.AsyncClient = None

    def set_platform(self, platform: int):
        self.headers["PLATFORM-ID"] = platform

    def encrypt(self, data: str, key: bytes) -> bytes:
        aes = AES.new(key, AES.MODE_CBC, self._getiv())
        return aes.encrypt(pad(data.encode("utf8"), 16)) + key

    def decrypt(self, data: bytes) -> Tuple[bytes]:
        data = b64decode(data.decode("utf8"))
        aes = AES.new(data[-32:], AES.MODE_CBC, self._getiv())
        return aes.decrypt(data[:-32]), data[-32:]

    def unpack(self, data: bytes) -> Tuple[dict, bytes]:  # 与原版不同，优先改
        data = b64decode(data.decode("utf8"))
        aes = AES.new(data[-32:], AES.MODE_CBC, self._getiv())
        dec = unpad(aes.decrypt(data[:-32]), 16)
        return unpackb(dec, strict_map_key=False), data[-32:]

    def generate_device_id(self, uid: str, access_key: str, index: int = 0) -> str:
        """生成设备ID"""
        return md5(f"{uid}:{access_key}:{index}".encode("utf-8")).hexdigest()

    @staticmethod
    def createkey() -> bytes:
        return bytes(ord("0123456789abcdef"[randint(0, 15)]) for _ in range(32))

    @staticmethod
    def no_null_key(obj):
        if isinstance(obj, dict):
            if None in obj and not [
                1 for k in obj if type(k) is not int and k is not None
            ]:
                return [
                    BaseClient.no_null_key(v1)
                    for k1, v1 in sorted(
                        ((k, v) for k, v in obj.items() if k is not None),
                        key=lambda x: x[0],
                    )
                ]
            return {
                k: BaseClient.no_null_key(v) for k, v in obj.items() if k is not None
            }
        elif isinstance(obj, list):
            return [BaseClient.no_null_key(v) for v in obj]
        else:
            return obj

    def pack(self, data: object, key: bytes) -> Tuple[bytes]:
        raise NotImplementedError

    def _getiv(self) -> bytes:
        raise NotImplementedError

    def get_api_root(self, platfrom: int) -> int:
        raise NotImplementedError

    async def callapi(self, request: RequestBase) -> Union[dict, Tuple[dict, dict]]:
        raise NotImplementedError

    async def login(self):
        raise NotImplementedError

    async def load_index(self) -> LoadIndexResponse:
        response = await self.callapi(LoadIndexRequest())
        return LoadIndexResponse.parse_obj(response)

    async def home_index(self) -> HomeIndexResponse:
        response = await self.callapi(HomeIndexRequest())
        return HomeIndexResponse.parse_obj(response)

    async def clan_info(
        self,
        clan_id: int,
    ) -> ClanInfoResponse:
        response = await self.callapi(ClanInfoRequest(clan_id=clan_id))
        return ClanInfoResponse.parse_obj(response)

    async def clan_battle_top(self, clan_id: int, coin: int) -> ClanBattleTopResponse:
        response = await self.callapi(
            ClanBattleTopRequest(clan_id=clan_id, current_clan_battle_coin=coin)
        )
        return ClanBattleTopResponse.parse_obj(response)

    async def clan_battle_detail_info(
        self, clan_id: int, clan_battle_id: int, lap_num: int, order_num: int
    ) -> ClanBattleReloadDetailInfoResponse:
        response = await self.callapi(
            ClanBattleReloadDetailInfoRequest(
                clan_id=clan_id,
                clan_battle_id=clan_battle_id,
                lap_num=lap_num,
                order_num=order_num,
            )
        )
        return ClanBattleReloadDetailInfoResponse.parse_obj(response)

    async def clan_battle_log(
        self, page: int, clan_battle_id: int
    ) -> ClanBattleLogListResponse:
        response = await self.callapi(
            ClanBattleLogListRequest(page=page, clan_battle_id=clan_battle_id)
        )
        return ClanBattleLogListResponse.parse_obj(response)

    async def time_line_report(
        self, target_viewer_id: int, clan_battle_id: int, battle_log_id: int
    ) -> ClanBattleTimeLineReportResponse:
        response = await self.callapi(
            ClanBattleTimeLineReportRequest(
                target_viewer_id=target_viewer_id,
                battle_log_id=battle_log_id,
                clan_battle_id=clan_battle_id,
            )
        )
        return ClanBattleTimeLineReportResponse.parse_obj(response)

    async def support_unit_list_2(
        self, clan_id: int
    ) -> ClanBattleSupportUnitList2Response:
        response = await self.callapi(
            ClanBattleSupportUnitList2Request(clan_id=clan_id)
        )
        return ClanBattleSupportUnitList2Response.parse_obj(response)

    async def arena_info(self) -> ArenaInfoResponse:
        response = await self.callapi(ArenaInfoRequest())
        return ArenaInfoResponse.parse_obj(response)

    async def arena_rank(self, page: int) -> ArenaRankingResponse:
        response = await self.callapi(ArenaRankingRequest(page=page))
        return ArenaRankingResponse.parse_obj(response)

    async def grand_rank(self, page: int) -> GrandArenaRankingResponse:
        response = await self.callapi(GrandArenaRankingRequest(page=page))
        return GrandArenaRankingResponse.parse_obj(response)

    async def grand_arena_info(self) -> GrandArenaInfoResponse:
        response = await self.callapi(GrandArenaInfoRequest())
        return GrandArenaInfoResponse.parse_obj(response)

    async def grand_arena_history(self) -> GrandArenaHistoryResponse:
        response = await self.callapi(GrandArenaHistoryRequest())
        return GrandArenaHistoryResponse.parse_obj(response)

    async def grand_arena_history_detial(
        self, log_id: int
    ) -> GrandArenaHistoryDetailResponse:
        response = await self.callapi(GrandArenaHistoryDetailRequest(log_id=log_id))
        return GrandArenaHistoryDetailResponse.parse_obj(response)

    async def profile_get(self, viewer_id: int) -> ProfileGetResponse:
        response = await self.callapi(ProfileGetRequest(target_viewer_id=viewer_id))
        return ProfileGetResponse.parse_obj(response)

    async def get_support_unit_setting(self) -> SupportUnitGetSettingResponse:
        response = await self.callapi(SupportUnitGetSettingRequest())
        return SupportUnitGetSettingResponse.parse_obj(response)

    async def change_support_unit(
        self, support_type: int, position: int, action: int, unit_id: int
    ) -> SupportUnitChangeSettingResponse:
        response = await self.callapi(
            SupportUnitChangeSettingRequest(
                support_type=support_type,
                position=position,
                action=action,
                unit_id=unit_id,
            )
        )
        return SupportUnitChangeSettingResponse.parse_obj(response)
