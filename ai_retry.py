"""
ai_retry.py - AI API 호출 재시도 로직 및 Rate Limiting

Exponential backoff를 사용한 재시도 메커니즘과 Rate limiting을 제공합니다.
"""
import time
import logging
import random
from typing import Callable, TypeVar, Optional, Dict, Any
from functools import wraps
from datetime import datetime, timedelta

logger = logging.getLogger("dice-api.ai-retry")

T = TypeVar('T')


class RateLimiter:
    """간단한 Rate Limiter (토큰 버킷 알고리즘)"""
    
    def __init__(self, max_calls: int = 60, time_window: int = 60):
        """
        Args:
            max_calls: 시간 창 내 최대 호출 수
            time_window: 시간 창 (초)
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls: list[float] = []
        self.lock = False
    
    def acquire(self) -> bool:
        """
        Rate limit 체크 및 토큰 획득
        
        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        
        # 오래된 호출 기록 제거
        self.calls = [call_time for call_time in self.calls if now - call_time < self.time_window]
        
        if len(self.calls) >= self.max_calls:
            return False
        
        self.calls.append(now)
        return True
    
    def wait_time(self) -> float:
        """다음 호출까지 대기 시간 계산 (초)"""
        if not self.calls:
            return 0.0
        
        now = time.time()
        oldest_call = min(self.calls)
        elapsed = now - oldest_call
        
        if elapsed >= self.time_window:
            return 0.0
        
        return self.time_window - elapsed


# 전역 Rate Limiter 인스턴스
_default_rate_limiter = RateLimiter(max_calls=60, time_window=60)


def exponential_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,),
    rate_limiter: Optional[RateLimiter] = None
):
    """
    Exponential backoff 데코레이터
    
    Args:
        max_retries: 최대 재시도 횟수
        initial_delay: 초기 지연 시간 (초)
        max_delay: 최대 지연 시간 (초)
        exponential_base: 지수 증가 베이스
        jitter: 랜덤 지터 추가 여부 (True 권장)
        retryable_exceptions: 재시도할 예외 타입
        rate_limiter: Rate limiter 인스턴스 (None이면 기본 사용)
    """
    if rate_limiter is None:
        rate_limiter = _default_rate_limiter
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                # Rate limiting 체크
                if not rate_limiter.acquire():
                    wait_time = rate_limiter.wait_time()
                    if wait_time > 0:
                        logger.warning(
                            f"Rate limit reached for {func.__name__}, "
                            f"waiting {wait_time:.2f}s"
                        )
                        time.sleep(wait_time)
                        # 재시도
                        if not rate_limiter.acquire():
                            raise Exception(f"Rate limit exceeded for {func.__name__}")
                
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    
                    # 429 (Too Many Requests) 에러는 즉시 재시도하지 않고 대기
                    if "429" in str(e) or "quota" in str(e).lower():
                        wait_time = rate_limiter.wait_time() or 60.0
                        logger.warning(
                            f"Rate limit error (429) for {func.__name__}, "
                            f"waiting {wait_time:.2f}s"
                        )
                        time.sleep(wait_time)
                        continue
                    
                    # 마지막 시도면 예외 발생
                    if attempt == max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}: {e}"
                        )
                        raise
                    
                    # Exponential backoff 계산
                    delay = min(
                        initial_delay * (exponential_base ** attempt),
                        max_delay
                    )
                    
                    # Jitter 추가 (동시 재시도 방지)
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)
                    
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay:.2f}s: {e}"
                    )
                    time.sleep(delay)
                except Exception as e:
                    # 재시도 불가능한 예외는 즉시 발생
                    logger.error(f"Non-retryable error in {func.__name__}: {e}")
                    raise
            
            # 모든 재시도 실패
            if last_exception:
                raise last_exception
            raise Exception(f"Unexpected error in {func.__name__}")
        
        return wrapper
    return decorator


def retry_ai_call(
    func: Callable[..., T],
    max_retries: int = 3,
    initial_delay: float = 2.0,
    max_delay: float = 60.0
) -> T:
    """
    AI API 호출을 위한 재시도 헬퍼 함수
    
    Args:
        func: 호출할 함수 (인자 없음)
        max_retries: 최대 재시도 횟수
        initial_delay: 초기 지연 시간
        max_delay: 최대 지연 시간
    
    Returns:
        함수 실행 결과
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            
            # 429 에러는 특별 처리
            if "429" in str(e) or "quota" in str(e).lower():
                wait_time = 60.0 * (attempt + 1)  # 429는 더 긴 대기
                logger.warning(f"Rate limit (429) error, waiting {wait_time}s")
                if attempt < max_retries:
                    time.sleep(wait_time)
                    continue
                else:
                    raise
            
            # 마지막 시도면 예외 발생
            if attempt == max_retries:
                logger.error(f"Max retries exceeded: {e}")
                raise
            
            # Exponential backoff
            delay = min(initial_delay * (2 ** attempt), max_delay)
            delay = delay * (0.5 + random.random() * 0.5)  # Jitter
            
            logger.warning(f"Retry {attempt + 1}/{max_retries} after {delay:.2f}s: {e}")
            time.sleep(delay)
    
    if last_exception:
        raise last_exception
    raise Exception("Unexpected error in retry_ai_call")

