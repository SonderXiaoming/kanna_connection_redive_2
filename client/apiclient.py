import asyncio
import binascii
from collections import deque
import random
import re
import threading
import traceback
from base64 import b64decode, b64encode
from hashlib import md5, sha1
from json import loads
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union
import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from loguru import logger
from msgpack import packb
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from msgpack import unpackb
from ..basedata import GamePlatform, Platform
from ..errorclass import (
    ApiException,
    CuteResultCode,
    DetailHttpError,
    MaintenanceError,
    NeedRefreshError,
    NoneResponseError,
    RiskControlError,
    TutorialError,
)
from ..util.tools import load_config
from .base import BaseClient
from .request import (
    CheckAgreementRequest,
    CheckGameStartRequest,
    RequestBase,
    SourceIniGetMaintenanceStatusRequest,
    ToolSdkLoginRequest,
)
from .response import SourceIniGetMaintenanceStatusResponse, ToolSdkLoginResponse
from nonebot import on_startup

config = str(Path(__file__).parent / "version.txt")
pinfo = load_config(str(Path(__file__).parent / "proxy.json"))
version = "99.9.9"

bcr_headers = {
    "Accept-Encoding": "gzip",
    "User-Agent": "Dalvik/2.1.0 (Linux, U, Android 5.1.1, PCRT00 Build/LMY48Z)",
    "X-Unity-Version": "2018.4.30f1",
    "APP-VER": version,
    "BATTLE-LOGIC-VERSION": "4",
    "BUNDLE-VER": "",
    "DEVICE": "2",
    "DEVICE-ID": "7b1703a5d9b394e24051d7a5d4818f17",
    "DEVICE-NAME": "OPPO PCRT00",
    "EXCEL-VER": "1.0.0",
    "GRAPHICS-DEVICE-NAME": "Adreno (TM) 640",
    "IP-ADDRESS": "10.0.2.15",
    "KEYCHAIN": "",
    "LOCALE": "CN",
    "PLATFORM-OS-VERSION": "Android OS 5.1.1 / API-22 (LMY48Z/rel.se.infra.20200612.100533)",
    "REGION-CODE": "",
    "RES-KEY": "ab00a0a6dd915a052a2ef7fd649083e5",
    "RES-VER": "10002200",
    "SHORT-UDID": "0",
    "CHANNEL-ID": "1",
    "PLATFORM": "2",
    "Connection": "Keep-Alive",
}

tw_headers = {
    "Accept-Encoding": "deflate, gzip",
    "User-Agent": "UnityPlayer/2021.3.20f1 (UnityWebRequest/1.0, libcurl/7.84.0-DEV)",
    "Content-Type": "application/octet-stream",
    "Expect": "100-continue",
    "X-Unity-Version": "2021.3.20f1",
    "APP-VER": "4.4.0",
    "BATTLE-LOGIC-VERSION": "4",
    "BUNDLE-VER": "",
    "DEVICE": "2",
    "DEVICE-ID": "7b1703a5d9b394e24051d7a5d4818f17",
    "DEVICE-NAME": "OPPO PCRM00",
    "GRAPHICS-DEVICE-NAME": "Adreno (TM) 640",
    "IP-ADDRESS": "10.0.2.15",
    "KEYCHAIN": "",
    "LOCALE": "Jpn",
    "PLATFORM-OS-VERSION": "Android OS 5.1.1 / API-22 (LMY48Z/rel.se.infra.20200612.100533)",
    "REGION-CODE": "",
    "RES-VER": "00150001",
    "platform": "2",
}


@on_startup
async def init():
    asyncio.create_task(b_apiroot.reset_statistics())
    asyncio.create_task(qu_apiroot.reset_statistics())


class ServerManager:
    def __init__(self, urls: List[str], reset_interval=1800):
        self.urls = urls
        self.reset_interval = reset_interval  # 重置统计数据的时间间隔（秒）
        self.response_times = {
            url: deque(maxlen=5) for url in urls
        }  # 存储最近几次的响应时间

    def record_response_time(self, url: str, response_time: float):
        self.response_times[url].append(response_time)

    def select_best_server(self):
        return (
            min(avg_response_times, key=avg_response_times.get)
            if (
                avg_response_times := {
                    url: sum(times) / len(times)
                    for url, times in self.response_times.items()
                    if times and float("inf") not in times
                }
            )
            else random.choice(list(self.urls))
        )

    async def reset_statistics(self):
        while True:
            await asyncio.sleep(self.reset_interval)
            for url in self.urls:
                self.response_times[url].clear()


b_apiroot = ServerManager(
    [
        "https://le1-prod-all-gs-gzlj.bilibiligame.net/",
        "https://l2-prod-all-gs-gzlj.bilibiligame.net/",
        "https://l3-prod-all-gs-gzlj.bilibiligame.net/",
    ]
)

qu_apiroot = ServerManager(
    [
        "https://l1-prod-uo-gs-gzlj.bilibiligame.net/",
        "https://l2-prod-uo-gs-gzlj.bilibiligame.net/",
        "https://l3-prod-uo-gs-gzlj.bilibiligame.net/",
    ]
)

_http_clients: Dict[Tuple[Any, bool], httpx.AsyncClient] = {}
_http_clients_lock = threading.Lock()


def _current_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.get_event_loop()


def get_async_client(verify: bool = True) -> httpx.AsyncClient:
    """返回绑定在当前事件循环上的 httpx 客户端。

    httpx/httpcore/anyio 的连接池内部对象同样只能绑定一个 loop，跨 loop
    复用连接会抛 "got Future attached to a different loop"。因此按
    (loop, verify) 惰性创建客户端，各 loop 拥有独立的连接池。
    """
    key = (_current_loop(), verify)
    with _http_clients_lock:
        client = _http_clients.get(key)
        if client is None:
            client = httpx.AsyncClient(verify=verify, timeout=20)
            _http_clients[key] = client
        return client


async def close_async_clients() -> None:
    """Close and forget HTTP clients owned by the current event loop."""
    loop = asyncio.get_running_loop()
    with _http_clients_lock:
        clients = [
            (key, client)
            for key, client in _http_clients.items()
            if key[0] is loop
        ]
        for key, _ in clients:
            del _http_clients[key]
    await asyncio.gather(*(client.aclose() for _, client in clients))


class BCRClient(BaseClient):
    def __init__(self, uid: str, access_key: str, platform: int):
        super().__init__()
        self.headers = bcr_headers.copy()
        self.qudao = (
            GamePlatform.b_id.value
            if platform == Platform.b_id.value
            else GamePlatform.qu_id.value
        )
        self.apiroot_manager = (
            b_apiroot if self.qudao == GamePlatform.b_id.value else qu_apiroot
        )
        self.set_platform(self.qudao)
        self.uid = uid
        self.access_key = access_key
        self.platfrom = platform
        if self.qudao == GamePlatform.qu_id.value:
            self.headers["RES-KEY"] = "d145b29050641dac2f8b19df0afe0e59"
        self.device_index = 0
        self.headers["DEVICE-ID"] = self.generate_device_id(
            self.uid, self.access_key, self.device_index
        )

    def _getiv(self) -> bytes:
        return b"7Fk9Lm3Np8Qr4Sv2"

    @staticmethod
    def pack(data: object, key: bytes) -> bytes:
        aes = AES.new(key, AES.MODE_CBC, b"7Fk9Lm3Np8Qr4Sv2")
        return aes.encrypt(pad(packb(data, use_bin_type=False), 16)) + key

    async def callapi(self, request: RequestBase) -> Union[dict, Tuple[dict, dict]]:
        try:
            api_root = self.apiroot_manager.select_best_server()
            async with self._api_lock:
                key = BCRClient.createkey()
                if self.viewer_id is not None:
                    request.viewer_id = (
                        b64encode(self.encrypt(str(self.viewer_id), key))
                        if request.crypted
                        else str(self.viewer_id)
                    )
                resp = await get_async_client().post(
                    api_root + request.url,
                    data=(
                        self.pack(request.dict(by_alias=True), key)
                        if request.crypted
                        else request.json(by_alias=True).encode("utf8")
                    ),
                    headers=self.headers,
                )
                resp.raise_for_status()
                response_time = resp.elapsed.total_seconds()
                self.apiroot_manager.record_response_time(api_root, response_time)
                orginal_response = resp.content
                format_response = BCRClient.no_null_key(
                    self.unpack(orginal_response)[0]
                    if request.crypted
                    else loads(orginal_response)
                )

                data_headers = format_response["data_headers"]

                if "sid" in data_headers and data_headers["sid"] != "":
                    t = md5()
                    t.update((data_headers["sid"] + "c!SID!n").encode("utf8"))
                    self.headers["SID"] = t.hexdigest()

                if "request_id" in data_headers:
                    self.headers["REQUEST-ID"] = data_headers["request_id"]

                data = format_response["data"]

                if not data:
                    raise NoneResponseError(format_response)

                if "server_error" in data:
                    data = data["server_error"]
                    logger.error(f"pcrclient: {request.url} api failed {data}")
                    raise ApiException(
                        data["message"], data_headers, data_headers["result_code"]
                    )

                return (data, data_headers) if request.header else data
        except ApiException:
            raise
        except binascii.Error:
            await asyncio.sleep(1)
            return await self.callapi(request)
        except (NoneResponseError, httpx.HTTPStatusError, httpx.HTTPError) as e:
            self.apiroot_manager.record_response_time(api_root, float("inf"))
            raise DetailHttpError(e.__repr__()) from e
        except Exception:
            logger.error(traceback.format_exc())
            raise

    async def check_gamestart(self):
        try:
            gamestart, data_headers = await self.callapi(CheckGameStartRequest())
        except ApiException as e:
            data_headers = e.header
        if "store_url" in data_headers:
            if version := re.compile(r"\d\d\.\d\.\d").findall(
                data_headers["store_url"]
            ):
                version = version[0]
                print(f"检测到新版本{version}, 请前往应用商店更新")
                with open(config, "w", encoding="utf-8") as fp:
                    print(version, file=fp)
            else:
                version = "9.9.9"
            bcr_headers["APP-VER"] = version
            self.headers["APP-VER"] = version
            gamestart, data_headers = await self.callapi(CheckGameStartRequest())

        if not gamestart.get("now_tutorial", True):
            raise TutorialError

    async def check_dangerous(self):
        try:
            lres, data_headers = await self.callapi(
                ToolSdkLoginRequest(
                    uid=self.uid, access_key=self.access_key, platform=self.qudao
                )
            )
            response = ToolSdkLoginResponse(**lres)
        except ApiException as e:
            if e.result_code == CuteResultCode.RESULT_CODE_ACCOUNT_BLOCK_ERROR:
                self.device_index += 1
                self.headers["DEVICE-ID"] = self.generate_device_id(
                    self.uid, self.access_key, self.device_index
                )
                return await self.check_dangerous()
            raise NeedRefreshError from e

        if response.is_risk:
            raise RiskControlError

        self.viewer_id = str(data_headers["viewer_id"])

    async def check_maintenance(self):
        manifest = await self.callapi(SourceIniGetMaintenanceStatusRequest())
        response = SourceIniGetMaintenanceStatusResponse(**manifest)
        if response.maintenance_message:
            time = re.search(
                "\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d", manifest["maintenance_message"]
            ).group()
            raise MaintenanceError(time)
        self.headers["MANIFEST-VER"] = response.manifest_ver
        self.headers["RES-KEY"] = response.res_key

    async def login(self):
        if "REQUEST-ID" in self.headers:
            self.headers.pop("REQUEST-ID")

        await self.check_maintenance()
        await self.check_dangerous()
        await self.check_gamestart()

        # await self.callapi(CheckAgreementRequest())
        logger.info(f"{self.viewer_id}登录成功")


alphabet = "0123456789"


class TWClient(BaseClient):
    def __init__(self, short_udid: str, udid: str, viewer_id: int):
        super().__init__()
        self.udid = udid
        self.short_udid = short_udid
        self.viewer_id = viewer_id
        self.headers = tw_headers.copy()
        self.headers["SID"] = TWClient._makemd5(
            str(self.viewer_id) + TWClient.format_uuid(udid)
        )
        self.token = TWClient.createkey()
        self.platform = str(self.viewer_id)[0]
        self.apiroot = self.api_root = (
            f'https://api{"" if self.platform == "1" else "5"}-pc.so-net.tw/'
        )
        self.device_index = 0
        self.headers["DEVICE-ID"] = self.generate_device_id(
            self.udid, self.short_udid, self.device_index
        )

    def _getiv(self) -> bytes:
        return self.udid[:16].encode("utf8")

    def pack(self, data: object, key: bytes) -> Tuple[bytes, bytes]:
        aes = AES.new(key, AES.MODE_CBC, self._getiv())
        packed = packb(data, use_bin_type=False)
        return packed, aes.encrypt(pad(packed, 16)) + key

    @staticmethod
    def _makemd5(str: str) -> str:
        return md5(f"{str}r!I@nt8e5i=".encode("utf8")).hexdigest()

    @staticmethod
    def _encode(dat: str) -> str:
        return (
            f"{len(dat):0>4x}"
            + "".join(
                [
                    (
                        chr(ord(dat[int(i / 4)]) + 10)
                        if i % 4 == 2
                        else random.choice(alphabet)
                    )
                    for i in range(len(dat) * 4)
                ]
            )
            + TWClient._ivstring()
        )

    @staticmethod
    def _ivstring() -> str:
        return "".join([random.choice(alphabet) for _ in range(32)])

    @staticmethod
    def format_uuid(uuid_str: str) -> str:
        """把不带 - 的 UUID 转换为标准 UUID 格式"""
        uuid_str = uuid_str.strip().lower()
        if len(uuid_str) != 32:
            raise ValueError("输入必须是32位的十六进制字符串")
        return f"{uuid_str[:8]}-{uuid_str[8:12]}-{uuid_str[12:16]}-{uuid_str[16:20]}-{uuid_str[20:32]}"

    async def callapi(self, request: RequestBase) -> dict:
        try:
            async with self._api_lock:
                key = TWClient.createkey()
                if self.viewer_id:
                    request.viewer_id = b64encode(self.encrypt(str(self.viewer_id), key))
                    request.tw_server_id = self.platform
                packed, crypted = self.pack(request.dict(by_alias=True), key)

                self.headers["PARAM"] = sha1(
                    (
                        TWClient.format_uuid(self.udid)
                        + f"/{request.url}"
                        + b64encode(packed).decode("utf8")
                        + str(self.viewer_id)
                    ).encode("utf8")
                ).hexdigest()
                self.headers["SHORT-UDID"] = TWClient._encode(self.short_udid)

                resp = await get_async_client(verify=False).post(
                    self.apiroot + request.url, data=crypted, headers=self.headers
                )
                response = self.unpack(resp.content)[0]

                data_headers = response["data_headers"]

                if "viewer_id" in data_headers:
                    self.viewer_id = data_headers["viewer_id"]

                if "required_res_ver" in data_headers:
                    self.headers["RES-VER"] = data_headers["required_res_ver"]
                data = response["data"]

                if "server_error" in data:
                    data = data["server_error"]
                    code = data_headers["result_code"]
                    logger.info(
                        f"pcrclient: {request.url} api failed code = {code}, {data}"
                    )
                    raise ApiException(
                        data["message"], data["status"], data_headers["result_code"]
                    )

                return data
        except ApiException:
            raise
        except binascii.Error:
            await asyncio.sleep(1)
            return await self.callapi(request)
        except (NoneResponseError, httpx.HTTPStatusError, httpx.HTTPError) as e:
            raise DetailHttpError(e.__repr__()) from e
        except Exception:
            logger.error(traceback.format_exc())
            raise

    async def login(self):
        # self.headers["APP-VER"] = await self.get_ver()
        self.headers["APP-VER"] = version
        await self.callapi(CheckAgreementRequest())
        await self.callapi(CheckGameStartRequest())
