from fastapi import APIRouter
from app.common.exceptions import BusinessException
from app.common.response import ApiResponse

router = APIRouter(tags=["System"])


@router.get("/health")
async def health():

    return ApiResponse(
        data={
            "status":"ok"
        }
    )

@router.get("/error")
async def error():

    raise BusinessException(
        code=40001,
        message="Demo Business Error"
    )