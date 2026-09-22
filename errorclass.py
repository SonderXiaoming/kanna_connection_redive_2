from enum import Enum


class CuteResultCode(Enum):
    API_RESULT_SUCCESS_CODE = 1
    RESULT_CODE_MAINTENANCE_COMMON = 101
    RESULT_CODE_SERVER_ERROR = 102
    API_RESULT_SESSION_ERROR = 201
    RESULT_CODE_ACCOUNT_BLOCK_ERROR = 203
    API_RESULT_VERSION_ERROR = 204
    RESULT_CODE_PROCESSED_ERROR = 213
    RESULT_CODE_DMM_ONETIMETOKEN_EXPIRED = 318
    API_RESULT_APPRES_VERSION_ERROR = 217
    API_RESULT_REQUEST_DECODE_ERROR = 218
    API_RESULT_RESPONSE_DECODE_ERROR = 219
    RESULT_CODE_MAINTENANCE_FROM = 2700
    RESULT_CODE_MAINTENANCE_TO = 2999


class PasswordError(Exception):
    def __init__(self, message: str):
        super().__init__("用户名或密码错误")


class NeedCaptchError(Exception):
    def __init__(self):
        super().__init__("需要过码")


class PassCaptchError(Exception):
    def __init__(self, msg=""):
        super().__init__(f"过码失败{msg}")


class NeedRefreshError(Exception):
    def __init__(self):
        super().__init__("access_key过期，重新登录，可能需要重绑")


class NeedReLoginError(Exception):
    def __init__(self):
        super().__init__("需要重新登录")


class RiskControlError(Exception):
    def __init__(self):
        super().__init__("账号存在风险，请改密码后重新登录")


class TutorialError(Exception):
    def __init__(self):
        super().__init__("该账号没过完教程!")


class MaintenanceError(Exception):
    def __init__(self, message: str):
        super().__init__(f"维护中，预计{message}开服")


class DetailHttpError(Exception):
    def __init__(self, message: str):
        super().__init__(f"网络异常，请稍后再试：{message}")


class UnknowError(Exception):
    def __init__(self, message: str):
        super().__init__(f"未知错误：{message}")


class ApiException(Exception):
    def __init__(self, message: str, header: dict, result_code: int):
        super().__init__(message)
        self.header = header
        try:
            self.result_code = CuteResultCode(result_code)
        except ValueError:
            self.result_code = result_code


class NoneResponseError(Exception):
    def __init__(self, message: dict):
        super().__init__(str(message))


class CancelledError(Exception):
    pass
