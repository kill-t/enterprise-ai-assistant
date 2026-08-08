from fastapi import APIRouter


from app.common.exception.business_exception import BusinessException
from app.common.response.api_response import ApiResponse
from app.common.exception.error_code import ErrorCode

router = APIRouter(tags=["System"])


@router.get("/health")
async def health():
    """返回进程级健康状态，供本地检查和容器探针使用。"""

    return ApiResponse.success(data={"status": "ok"})


@router.get("/error")
async def error():
    """主动抛出业务异常，用于验证全局异常处理器。"""

    raise BusinessException(
        code=ErrorCode.VALIDATION_ERROR, message="Demo Business Error"
    )
