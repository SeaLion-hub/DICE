"""
error_handlers.py - 공통 에러 처리 및 표준화된 에러 응답

모든 API 에러를 일관된 형식으로 처리하고, 개발/프로덕션 환경에 맞게 응답합니다.
"""
import logging
import traceback
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
import psycopg2
from psycopg2 import errors as pg_errors

logger = logging.getLogger("dice-api.errors")


class APIError(Exception):
    """커스텀 API 에러 클래스"""
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


def create_error_response(
    status_code: int,
    error_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    include_traceback: bool = False
) -> Dict[str, Any]:
    """
    표준화된 에러 응답 생성
    
    Args:
        status_code: HTTP 상태 코드
        error_code: 애플리케이션 레벨 에러 코드
        message: 사용자 친화적 에러 메시지
        details: 추가 상세 정보
        include_traceback: 스택 트레이스 포함 여부 (개발 환경용)
    
    Returns:
        표준화된 에러 응답 딕셔너리
    """
    response = {
        "error": {
            "code": error_code,
            "message": message,
            "status_code": status_code
        }
    }
    
    if details:
        response["error"]["details"] = details
    
    if include_traceback:
        response["error"]["traceback"] = traceback.format_exc()
    
    return response


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """커스텀 APIError 핸들러"""
    logger.error(
        f"API Error: {exc.error_code} - {exc.message}",
        extra={"error_code": exc.error_code, "details": exc.details}
    )
    
    include_traceback = request.app.state.env == "dev"
    return JSONResponse(
        status_code=exc.status_code,
        content=create_error_response(
            exc.status_code,
            exc.error_code,
            exc.message,
            exc.details,
            include_traceback
        )
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """HTTPException 핸들러"""
    if isinstance(exc, APIError):
        return await api_error_handler(request, exc)
    
    # FastAPI의 HTTPException 처리
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        error_code = f"HTTP_{exc.status_code}"
        message = exc.detail if isinstance(exc.detail, str) else "An error occurred"
        
        logger.warning(f"HTTP Exception: {exc.status_code} - {message}")
        
        include_traceback = request.app.state.env == "dev"
        return JSONResponse(
            status_code=exc.status_code,
            content=create_error_response(
                exc.status_code,
                error_code,
                message,
                include_traceback=include_traceback
            )
        )
    
    return None


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic 검증 에러 핸들러"""
    errors = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"])
        errors.append({
            "field": field,
            "message": error["msg"],
            "type": error["type"]
        })
    
    logger.warning(f"Validation error: {errors}")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=create_error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "VALIDATION_ERROR",
            "입력 데이터 검증에 실패했습니다.",
            {"validation_errors": errors}
        )
    )


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """데이터베이스 에러 핸들러"""
    error_code = "DATABASE_ERROR"
    message = "데이터베이스 작업 중 오류가 발생했습니다."
    details = {}
    
    if isinstance(exc, pg_errors.UniqueViolation):
        error_code = "DUPLICATE_ENTRY"
        message = "이미 존재하는 데이터입니다."
        details["constraint"] = str(exc).split("DETAIL:")[-1].strip() if "DETAIL:" in str(exc) else None
    elif isinstance(exc, pg_errors.ForeignKeyViolation):
        error_code = "FOREIGN_KEY_VIOLATION"
        message = "참조 무결성 제약 조건을 위반했습니다."
    elif isinstance(exc, pg_errors.CheckViolation):
        error_code = "CHECK_CONSTRAINT_VIOLATION"
        message = "데이터 검증 규칙을 위반했습니다."
    elif isinstance(exc, psycopg2.OperationalError):
        error_code = "DATABASE_CONNECTION_ERROR"
        message = "데이터베이스 연결에 실패했습니다."
    elif isinstance(exc, psycopg2.ProgrammingError):
        error_code = "DATABASE_QUERY_ERROR"
        message = "데이터베이스 쿼리 실행 중 오류가 발생했습니다."
    
    logger.error(f"Database error: {error_code} - {str(exc)}", exc_info=True)
    
    include_traceback = request.app.state.env == "dev"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=create_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code,
            message,
            details,
            include_traceback
        )
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """일반 예외 핸들러 (마지막 방어선)"""
    logger.error(f"Unhandled exception: {type(exc).__name__} - {str(exc)}", exc_info=True)
    
    include_traceback = request.app.state.env == "dev"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=create_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_SERVER_ERROR",
            "서버 내부 오류가 발생했습니다.",
            include_traceback=include_traceback
        )
    )


def setup_error_handlers(app: FastAPI, env: str = "dev") -> None:
    """
    FastAPI 앱에 에러 핸들러 등록
    
    Args:
        app: FastAPI 애플리케이션 인스턴스
        env: 환경 변수 ("dev" 또는 "prod")
    """
    app.state.env = env
    
    # 핸들러 등록 순서가 중요합니다 (특정 -> 일반 순서)
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(psycopg2.Error, database_error_handler)
    app.add_exception_handler(Exception, general_exception_handler)

