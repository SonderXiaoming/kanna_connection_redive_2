from ctypes import CDLL, POINTER, c_ubyte
from random import choices
from time import time
from platform import architecture
from json import dumps
from os.path import join, dirname
import asyncio
from typing import Optional
import httpx
from loguru import logger
from ..setting import JJCSetting
from ..client.base import CrossLoopLock

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
    "Referer": "https://pcrdfans.com/",
    "Origin": "https://pcrdfans.com",
    "Accept": "*/*",
    "Content-Type": "application/json; charset=utf-8",
    "Authorization": "",
    "Host": "api.pcrdfans.com",
}


def _getNonce():
    return "".join(choices("0123456789abcdefghijklmnopqrstuvwxyz", k=16))


def _getTs():
    return int(time())


def _dumps(x):
    return dumps(x, ensure_ascii=False).replace(" ", "")


_dllname = join(
    dirname(__file__),
    "libpcrdwasm.so" if architecture()[1] == "ELF" else "pcrdwasm.dll",
)
_getsign = CDLL(_dllname).getSign
_getsign.restype = POINTER(c_ubyte)
semaphore = CrossLoopLock()


def general_data(_def: list, page: int, region: int, sort: int) -> dict:
    data = {
        "def": _def,
        "language": 0,
        "nonce": _getNonce(),
        "page": page,
        "region": region,
        "sort": sort,
        "language": 0,
        "ts": _getTs(),
    }
    if len(str(_def[0])) != 6:
        return []
    gsign = _getsign(_dumps(data).encode("utf8"), data["nonce"].encode("utf8"))
    list = []
    for n in range(255):
        if gsign[n] == 0:
            break
        list.append(gsign[n])
    data["_sign"] = bytes(list).decode("utf8")
    return data


async def callPcrdLocal(
    _def: list, page: int, region: int, sort: int, proxies: Optional[dict] = None
) -> list:
    data = general_data(_def, page, region, sort)
    async with semaphore:
        async with httpx.AsyncClient(proxies=proxies, timeout=5) as client:
            try:
                res = await client.post(
                    "https://api.pcrdfans.com/x/v1/search",
                    headers=headers,
                    data=_dumps(data).encode("utf8"),
                )
                result = res.json()
                if code := result["code"]:
                    if code == 601:
                        await asyncio.sleep(1)
                        res = await client.post(
                            "https://api.pcrdfans.com/x/v1/search",
                            headers=headers,
                            data=_dumps(data).encode("utf8"),
                        )
                        result = res.json()
                    if result["code"]:
                        raise Exception(f"服务器报错：返回值{res.status_code}")
                return result["data"]["result"]
            except httpx.HTTPError as e:
                logger.warning(f"网络异常或者服务器无响应，{str(e)}")
            except Exception as e:
                logger.warning(str(e))
            finally:
                await asyncio.sleep(1)

    return []


async def callPcrdOnline(
    _def, page: int, region: int, sort: int, proxies: Optional[dict] = None
):
    data = general_data(_def, page, region, sort)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://bot2.hwmoe.com/mapi/multi_pcrdfans_search",
                json=data,
            )
            r.raise_for_status()
            result = r.json()
            return result["data"]["result"]
    except httpx.HTTPError as e:
        logger.warning(f"网络异常或者服务器无响应，{str(e)}")
    except Exception as e:
        logger.warning(str(e))


callPcrd = callPcrdLocal if JJCSetting.query_local.value else callPcrdOnline
