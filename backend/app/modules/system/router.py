from fastapi import APIRouter


from app.common.exception.business_exception import BusinessException
from app.common.response.api_response import ApiResponse
from app.common.exception.error_code import ErrorCode

router = APIRouter(tags=["System"])


@router.get("/health")
async def health():

    return ApiResponse(data={"status": "ok"})


@router.get("/error")
async def error():

    raise BusinessException(
        code=ErrorCode.VALIDATION_ERROR, message="Demo Business Error"
    )
