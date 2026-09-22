from enum import Enum
from pathlib import Path
from .basedata import FilePath


class ClientSetting(Enum):
    """
    有关client的设置
    clanbattle_semaphore：会战查询最大并发量
    jjc_semaphore：会战查询最大并发量
    """

    clanbattle_max = 66
    jjc_max = 33


class TimeAxisLimit(Enum):
    """
    分刀限制
    max_calculate：最大计算量
    max_result：最大获取结果数
    max_query：一个boss显示几个作业
    single_limit：一个阶段中一个boss显示几个作业
    """

    max_calculate = 114514
    max_result = 3
    max_query = 8
    single_limit = 3


class JJCSetting(Enum):
    """
    竞技场设置
    buff_path：缓存储存路径
    query_local：作业网查询方法
    """

    buff_path = Path(__file__).parent.parent / "priconne" / "arena" / "buffer"
    query_local = True


class WebSetting(Enum):
    """
    网页端！！！
    """

    api_host = "127.0.0.1"
    api_port = "12139"
    cookie_secure = False
    web_public_url = "http://xxxx:12138/kanna_connection"


class BossData(Enum):
    """
    会战boss数据，主要为网页端服务
    """

    use_online = True
    info_path = FilePath.data.value / "boss_info.json"
