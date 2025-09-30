# yonsei_app.py
"""
연세대학교 전체 단과대학 공지사항 통합 시스템 (DICE)
Production-Ready Enhanced Version with Security, Performance & Scalability
"""

from flask import Flask, render_template_string, jsonify, send_from_directory, request, redirect, url_for, make_response
from datetime import datetime, timedelta, timezone
import re
import requests
import os
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
import bcrypt
import jwt
from flask_cors import CORS 
from dotenv import load_dotenv
import json
import logging
import traceback
from functools import wraps
import time
from typing import Optional, Dict, List, Any, Tuple
import redis
from contextlib import contextmanager
import threading
from dataclasses import dataclass
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# 로깅 설정 개선
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper())
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('dice_app.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

# CORS 설정 개선 (보안 강화)
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
CORS(app, resources={
    r"/api/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "PUT", "DELETE"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Rate Limiting 설정
rate_limits = [
    f"{os.getenv('RATE_LIMIT_PER_DAY', '200')} per day",
    f"{os.getenv('RATE_LIMIT_PER_HOUR', '50')} per hour"
]

limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=rate_limits,
    storage_uri=os.getenv("REDIS_URL", "memory://")
)

# ===== 환경 변수 및 설정 =====
@dataclass
class Config:
    """애플리케이션 설정 클래스"""
    APIFY_TOKEN: str = os.getenv("APIFY_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    
    # 보안 설정
    JWT_EXPIRATION_HOURS: int = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
    JWT_ALGORITHM: str = "HS256"
    BCRYPT_ROUNDS: int = int(os.getenv("BCRYPT_ROUNDS", "12"))
    
    # 성능 설정
    DB_POOL_MIN: int = int(os.getenv("DB_POOL_MIN", "2"))
    DB_POOL_MAX: int = int(os.getenv("DB_POOL_MAX", "20"))
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))  # 5분
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
    
    # 크롤링 설정
    MAX_NOTICES_PER_COLLEGE: int = int(os.getenv("MAX_NOTICES_PER_COLLEGE", "50"))
    CRAWL_RATE_LIMIT: int = int(os.getenv("CRAWL_RATE_LIMIT", "10"))  # 초당 요청 수
    
    def validate(self) -> List[str]:
        """필수 설정 검증"""
        errors = []
        if not self.JWT_SECRET_KEY:
            errors.append("JWT_SECRET_KEY is required")
        if not self.DATABASE_URL:
            errors.append("DATABASE_URL is required")
        if len(self.JWT_SECRET_KEY) < 32:
            errors.append("JWT_SECRET_KEY must be at least 32 characters")
        return errors

config = Config()

# 설정 검증
validation_errors = config.validate()
if validation_errors:
    for error in validation_errors:
        logger.error(f"Configuration error: {error}")
    raise ValueError(f"Configuration errors: {', '.join(validation_errors)}")

# Railway PostgreSQL URL 변환
if config.DATABASE_URL.startswith("postgres://"):
    config.DATABASE_URL = config.DATABASE_URL.replace("postgres://", "postgresql://", 1)
    logger.info("DATABASE_URL protocol updated to postgresql://")

# ===== Redis 캐싱 설정 =====
redis_client = None
if config.REDIS_URL and config.REDIS_URL != "memory://":
    try:
        redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        redis_client.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Using in-memory fallback.")
        redis_client = None

# 메모리 기반 캐시 폴백
memory_cache = {}
cache_lock = threading.Lock()

class CacheManager:
    """캐시 관리 클래스"""
    
    @staticmethod
    def get(key: str) -> Optional[Any]:
        """캐시에서 값 조회"""
        try:
            if redis_client:
                value = redis_client.get(key)
                return json.loads(value) if value else None
            else:
                with cache_lock:
                    item = memory_cache.get(key)
                    if item and item['expires'] > time.time():
                        return item['value']
                    elif item:
                        del memory_cache[key]
                    return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    @staticmethod
    def set(key: str, value: Any, ttl: int = config.CACHE_TTL) -> bool:
        """캐시에 값 저장"""
        try:
            if redis_client:
                return redis_client.setex(key, ttl, json.dumps(value))
            else:
                with cache_lock:
                    memory_cache[key] = {
                        'value': value,
                        'expires': time.time() + ttl
                    }
                return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
    
    @staticmethod
    def delete(key: str) -> bool:
        """캐시에서 값 삭제"""
        try:
            if redis_client:
                return bool(redis_client.delete(key))
            else:
                with cache_lock:
                    return memory_cache.pop(key, None) is not None
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False

cache = CacheManager()

# ===== 데이터베이스 연결 풀 개선 =====
connection_pool = None

def init_db_pool():
    """데이터베이스 연결 풀 초기화"""
    global connection_pool
    try:
        # psycopg2 연결 풀 import 추가
        import psycopg2.pool
        
        connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=config.DB_POOL_MIN,
            maxconn=config.DB_POOL_MAX,
            dsn=config.DATABASE_URL,
            options='-c statement_timeout=30000'
        )
        logger.info(f"Database connection pool created (min={config.DB_POOL_MIN}, max={config.DB_POOL_MAX})")
    except Exception as e:
        logger.error(f"Failed to create connection pool: {e}")
        connection_pool = None

init_db_pool()

@contextmanager
def get_db_connection():
    """컨텍스트 매니저를 사용한 안전한 DB 연결"""
    conn = None
    try:
        if connection_pool:
            conn = connection_pool.getconn()
        else:
            conn = psycopg2.connect(
                config.DATABASE_URL,
                connect_timeout=10,
                options='-c statement_timeout=30000'
            )
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"DB connection error: {e}")
        raise
    finally:
        if conn:
            if connection_pool:
                connection_pool.putconn(conn)
            else:
                conn.close()

# ===== 보안 개선된 인증 시스템 =====
class AuthManager:
    """인증 관리 클래스"""
    
    @staticmethod
    def create_jwt_token(user_data: Dict[str, Any]) -> str:
        """JWT 토큰 생성"""
        exp_time = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRATION_HOURS)
        payload = {
            'user_id': str(user_data['id']),
            'email': user_data['email'],
            'exp': exp_time,
            'iat': datetime.now(timezone.utc),
            'jti': str(uuid.uuid4())  # JWT ID for token revocation
        }
        return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
    
    @staticmethod
    def verify_jwt_token(token: str) -> Optional[Dict[str, Any]]:
        """JWT 토큰 검증"""
        try:
            payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
            
            # 토큰 블랙리스트 확인
            jti = payload.get('jti')
            if jti and cache.get(f"blacklist:{jti}"):
                logger.warning(f"Blacklisted token used: {jti}")
                return None
                
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
    
    @staticmethod
    def revoke_token(token: str) -> bool:
        """토큰 무효화 (블랙리스트 추가)"""
        try:
            payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
            jti = payload.get('jti')
            if jti:
                exp = payload.get('exp', 0)
                ttl = max(0, int(exp - time.time()))
                cache.set(f"blacklist:{jti}", True, ttl)
                return True
        except Exception as e:
            logger.error(f"Token revocation error: {e}")
        return False
    
    @staticmethod
    def get_user_from_request() -> Optional[str]:
        """요청에서 사용자 ID 추출"""
        token = None
        
        # HttpOnly 쿠키에서 토큰 확인
        token = request.cookies.get('dice_token')
        
        # 헤더에서 확인 (API 호출용)
        if not token:
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]
        
        if not token:
            return None
        
        payload = AuthManager.verify_jwt_token(token)
        return payload.get('user_id') if payload else None
    
    @staticmethod
    def hash_password(password: str) -> str:
        """비밀번호 해시 생성"""
        return bcrypt.hashpw(
            password.encode('utf-8'), 
            bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)
        ).decode('utf-8')
    
    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """비밀번호 검증"""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False

auth = AuthManager()

def require_auth(f):
    """인증 필수 데코레이터"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = auth.get_user_from_request()
        if not user_id:
            return jsonify({
                'success': False, 
                'message': '로그인이 필요합니다',
                'error_code': 'AUTHENTICATION_REQUIRED'
            }), 401
        return f(user_id, *args, **kwargs)
    return decorated_function

# ===== 입력 검증 클래스 =====
class Validator:
    """입력 검증 유틸리티"""
    
    @staticmethod
    def validate_email(email: str) -> Tuple[bool, str]:
        """이메일 형식 검증"""
        if not email or not isinstance(email, str):
            return False, "이메일이 필요합니다"
        
        if len(email) > 254:
            return False, "이메일이 너무 깁니다"
        
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            return False, "올바른 이메일 형식이 아닙니다"
        
        return True, "valid"
    
    @staticmethod
    def validate_password(password: str) -> Tuple[bool, str]:
        """비밀번호 강도 검증"""
        if not password or not isinstance(password, str):
            return False, "비밀번호가 필요합니다"
        
        if len(password) < 8:
            return False, "비밀번호는 최소 8자 이상이어야 합니다"
        
        if len(password) > 128:
            return False, "비밀번호가 너무 깁니다"
        
        if not re.search(r'[A-Za-z]', password):
            return False, "비밀번호에 영문자가 포함되어야 합니다"
        
        if not re.search(r'[0-9]', password):
            return False, "비밀번호에 숫자가 포함되어야 합니다"
        
        # 추가 보안 검증
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return False, "비밀번호에 특수문자가 포함되어야 합니다"
        
        return True, "valid"
    
    @staticmethod
    def sanitize_string(value: str, max_length: int = 255) -> str:
        """문자열 정제"""
        if not value:
            return ""
        
        # HTML 태그 제거
        value = re.sub(r'<[^>]+>', '', str(value))
        
        # 길이 제한
        value = value[:max_length]
        
        # 공백 정리
        value = re.sub(r'\s+', ' ', value).strip()
        
        return value
    
    @staticmethod
    def validate_uuid(value: str) -> bool:
        """UUID 형식 검증"""
        try:
            uuid.UUID(value)
            return True
        except (ValueError, TypeError):
            return False

validator = Validator()

# ===== 데이터 접근 계층 =====
class DatabaseManager:
    """데이터베이스 관리 클래스"""
    
    @staticmethod
    def create_user(user_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """사용자 생성"""
        try:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        INSERT INTO users (
                            email, password_hash, name, student_id,
                            major, gpa, toeic_score
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, email, name, student_id, major, gpa, toeic_score, created_at
                    """, (
                        user_data['email'], user_data['password_hash'],
                        user_data.get('name'), user_data.get('student_id'),
                        user_data.get('major'), user_data.get('gpa'),
                        user_data.get('toeic_score')
                    ))
                    
                    user = cur.fetchone()
                    
                    # 기본 설정 생성
                    cur.execute("""
                        INSERT INTO user_settings (user_id)
                        VALUES (%s)
                    """, (user['id'],))
                    
                    conn.commit()
                    return dict(user)
        except psycopg2.IntegrityError:
            raise ValueError("이미 등록된 이메일입니다")
        except Exception as e:
            logger.error(f"User creation error: {e}")
            raise
    
    @staticmethod
    def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
        """이메일로 사용자 조회"""
        try:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, email, password_hash, name, student_id,
                               major, gpa, toeic_score, is_active, last_login_at
                        FROM users
                        WHERE email = %s AND is_active = true
                    """, (email,))
                    
                    result = cur.fetchone()
                    return dict(result) if result else None
        except Exception as e:
            logger.error(f"Get user by email error: {e}")
            return None
    
    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """ID로 사용자 조회"""
        cache_key = f"user:{user_id}"
        cached_user = cache.get(cache_key)
        if cached_user:
            return cached_user
        
        try:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, email, name, student_id, major, gpa, toeic_score,
                               is_active, created_at, last_login_at
                        FROM users
                        WHERE id = %s AND is_active = true
                    """, (user_id,))
                    
                    result = cur.fetchone()
                    if result:
                        user_data = dict(result)
                        cache.set(cache_key, user_data, 300)  # 5분 캐시
                        return user_data
                    return None
        except Exception as e:
            logger.error(f"Get user by ID error: {e}")
            return None
    
    @staticmethod
    def update_last_login(user_id: str) -> bool:
        """마지막 로그인 시간 업데이트"""
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE users 
                        SET last_login_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (user_id,))
                    conn.commit()
                    
                    # 캐시 무효화
                    cache.delete(f"user:{user_id}")
                    return True
        except Exception as e:
            logger.error(f"Update last login error: {e}")
            return False

db = DatabaseManager()

# ===== 공지사항 관리 클래스 =====
class NoticeManager:
    """공지사항 관리 클래스"""
    
    @staticmethod
    def format_content(content: str) -> str:
        """공지사항 내용 포맷팅"""
        if not content:
            return ""
        
        # HTML 태그 제거 및 텍스트 정리
        content = re.sub(r'<[^>]+>', '', content)
        content = re.sub(r'(?<!\n)(\d+\.)', r'\n\n\1', content)
        content = re.sub(r'(?<!\n)([가-힣]\.)', r'\n\1', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        return content.strip()
    
    @staticmethod
    def detect_category(notice: Dict[str, Any]) -> str:
        """공지사항 카테고리 자동 감지"""
        title = (notice.get('title', '') or '').lower()
        content = (notice.get('content', '') or '').lower()
        text = f"{title} {content}"
        
        category_keywords = {
            'scholarship': ['장학', 'scholarship'],
            'internship': ['인턴', 'intern'],
            'competition': ['공모', 'competition', '대회'],
            'recruitment': ['채용', 'recruit', '모집'],
            'academic': ['수강', '강의', '학사', 'academic'],
            'seminar': ['세미나', 'seminar', '강연'],
            'event': ['행사', 'event', '축제']
        }
        
        for category, keywords in category_keywords.items():
            if any(keyword in text for keyword in keywords):
                return category
        
        return 'general'
    
    @staticmethod
    def get_notices_from_db(college_key: str, limit: int = 50, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """DB에서 공지사항 조회"""
        cache_key = f"notices:{college_key}:{limit}:{user_id or 'anonymous'}"
        cached_notices = cache.get(cache_key)
        if cached_notices:
            return cached_notices
        
        try:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    if college_key == 'all':
                        query = """
                            SELECT n.id, n.title, n.content, n.department as writer,
                                   n.published_date as date, n.original_url as url,
                                   n.view_count as views, n.category,
                                   c.name as college_name, c.icon as college_icon, 
                                   c.color as college_color, n.college_id
                            FROM notices n
                            JOIN colleges c ON n.college_id = c.id
                            LEFT JOIN user_notice_interactions uni ON n.id = uni.notice_id AND uni.user_id = %s
                            WHERE n.status = 'active'
                        """
                        params = [user_id]
                        
                        if user_id:
                            query += " AND (uni.hidden IS NULL OR uni.hidden = false)"
                        
                        query += " ORDER BY n.published_date DESC, n.created_at DESC LIMIT %s"
                        params.append(limit)
                    else:
                        query = """
                            SELECT n.id, n.title, n.content, n.department as writer,
                                   n.published_date as date, n.original_url as url,
                                   n.view_count as views, n.category,
                                   c.name as college_name, c.icon as college_icon, 
                                   c.color as college_color, n.college_id
                            FROM notices n
                            JOIN colleges c ON n.college_id = c.id
                            LEFT JOIN user_notice_interactions uni ON n.id = uni.notice_id AND uni.user_id = %s
                            WHERE n.college_id = %s AND n.status = 'active'
                        """
                        params = [user_id, college_key]
                        
                        if user_id:
                            query += " AND (uni.hidden IS NULL OR uni.hidden = false)"
                        
                        query += " ORDER BY n.published_date DESC, n.created_at DESC LIMIT %s"
                        params.append(limit)
                    
                    cur.execute(query, params)
                    notices = []
                    
                    for row in cur.fetchall():
                        notice = dict(row)
                        notice['date'] = notice['date'].strftime('%Y-%m-%d') if notice['date'] else None
                        notice['views'] = f"{notice['views']:,}"
                        notice['id'] = str(notice['id'])
                        notice['content'] = NoticeManager.format_content(notice['content'])
                        notice['college'] = {
                            'key': notice['college_id'],
                            'name': notice['college_name'],
                            'icon': notice['college_icon'],
                            'color': notice['college_color']
                        }
                        # 민감한 정보 제거
                        del notice['college_name']
                        del notice['college_icon']
                        del notice['college_color']
                        del notice['college_id']
                        notices.append(notice)
                    
                    # 짧은 시간 캐시 (1분)
                    cache.set(cache_key, notices, 60)
                    return notices
                    
        except Exception as e:
            logger.error(f"Get notices error: {e}")
            return []

notice_manager = NoticeManager()

# ===== 사용자 설정 관리 클래스 =====
class UserSettingsManager:
    """사용자 설정 관리 클래스"""
    
    @staticmethod
    def get_user_settings(user_id: str) -> Dict[str, Any]:
        """사용자 설정 조회"""
        try:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT push_notifications, email_notifications, 
                               deadline_alerts, ai_recommendations,
                               notification_frequency, theme, language, timezone,
                               keywords, subscribed_colleges
                        FROM user_settings
                        WHERE user_id = %s
                    """, (user_id,))
                    
                    result = cur.fetchone()
                    return dict(result) if result else {}
        except Exception as e:
            logger.error(f"Get user settings error: {e}")
            return {}
    
    @staticmethod
    def update_user_settings(user_id: str, settings: Dict[str, Any]) -> bool:
        """사용자 설정 업데이트"""
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # 허용된 설정 필드만 업데이트
                    allowed_fields = {
                        'push_notifications', 'email_notifications', 
                        'deadline_alerts', 'ai_recommendations',
                        'notification_frequency', 'theme', 'language', 
                        'timezone', 'keywords', 'subscribed_colleges'
                    }
                    
                    update_fields = []
                    values = []
                    
                    for field, value in settings.items():
                        if field in allowed_fields:
                            update_fields.append(f"{field} = %s")
                            values.append(value)
                    
                    if not update_fields:
                        return False
                    
                    values.append(user_id)
                    
                    query = f"""
                        UPDATE user_settings 
                        SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    """
                    
                    cur.execute(query, values)
                    conn.commit()
                    
                    # 캐시 무효화
                    cache.delete(f"user_settings:{user_id}")
                    return True
        except Exception as e:
            logger.error(f"Update user settings error: {e}")
            return False
    
    @staticmethod
    def get_user_keywords(user_id: str) -> List[str]:
        """사용자 키워드 조회"""
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT keywords FROM user_settings WHERE user_id = %s
                    """, (user_id,))
                    
                    result = cur.fetchone()
                    return result[0] if result and result[0] else []
        except Exception as e:
            logger.error(f"Get user keywords error: {e}")
            return []
    
    @staticmethod
    def update_user_keywords(user_id: str, keywords: List[str]) -> bool:
        """사용자 키워드 업데이트"""
        try:
            # 키워드 검증 및 정리
            clean_keywords = []
            for keyword in keywords:
                clean_keyword = validator.sanitize_string(keyword, 50).strip()
                if clean_keyword and len(clean_keyword) >= 2:
                    clean_keywords.append(clean_keyword)
            
            # 최대 20개 키워드 제한
            clean_keywords = clean_keywords[:20]
            
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE user_settings 
                        SET keywords = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    """, (clean_keywords, user_id))
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Update user keywords error: {e}")
            return False

user_settings_manager = UserSettingsManager()

# ===== 피드백 관리 클래스 =====
class FeedbackManager:
    """피드백 관리 클래스"""
    
    @staticmethod
    def create_feedback(user_id: str, feedback_data: Dict[str, Any]) -> bool:
        """피드백 생성"""
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_feedback (
                            user_id, type, subject, message, priority
                        ) VALUES (%s, %s, %s, %s, %s)
                    """, (
                        user_id,
                        feedback_data.get('type', 'general'),
                        validator.sanitize_string(feedback_data.get('subject', ''), 200),
                        validator.sanitize_string(feedback_data.get('message', ''), 2000),
                        feedback_data.get('priority', 'normal')
                    ))
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Create feedback error: {e}")
            return False

feedback_manager = FeedbackManager()

# ===== 크롤링 유틸리티 함수들 =====
def get_apify_data(task_id: str) -> Optional[List[Dict[str, Any]]]:
    """Apify에서 데이터 가져오기"""
    try:
        if not config.APIFY_TOKEN:
            logger.error("APIFY_TOKEN not configured")
            return None
        
        url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs/last/dataset/items"
        headers = {
            'Authorization': f'Bearer {config.APIFY_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Retrieved {len(data)} items from Apify task {task_id}")
        return data
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Apify API request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Get Apify data error: {e}")
        return None

def save_notices_to_db(college_key: str, notices_data: List[Dict[str, Any]]) -> bool:
    """공지사항을 DB에 저장"""
    try:
        if not notices_data:
            logger.warning(f"No notices data to save for {college_key}")
            return False
        
        saved_count = 0
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for notice_data in notices_data:
                    try:
                        # 기본 필드 검증 및 정리
                        title = validator.sanitize_string(notice_data.get('title', ''), 500)
                        content = validator.sanitize_string(notice_data.get('content', ''), 10000)
                        department = validator.sanitize_string(notice_data.get('department', ''), 200)
                        original_url = notice_data.get('url', '')
                        
                        if not title or not original_url:
                            continue
                        
                        # 날짜 처리
                        published_date = None
                        date_str = notice_data.get('date', '')
                        if date_str:
                            try:
                                # 다양한 날짜 형식 지원
                                for date_format in ['%Y-%m-%d', '%Y.%m.%d', '%Y/%m/%d']:
                                    try:
                                        published_date = datetime.strptime(date_str, date_format).date()
                                        break
                                    except ValueError:
                                        continue
                            except Exception as e:
                                logger.warning(f"Date parsing error for {date_str}: {e}")
                        
                        # 중복 체크 (URL 기준)
                        cur.execute("""
                            SELECT id FROM notices 
                            WHERE original_url = %s AND college_id = %s
                        """, (original_url, college_key))
                        
                        if cur.fetchone():
                            continue  # 이미 존재하는 공지사항
                        
                        # 카테고리 자동 감지
                        category = notice_manager.detect_category(notice_data)
                        
                        # 공지사항 저장
                        cur.execute("""
                            INSERT INTO notices (
                                college_id, title, content, department, 
                                published_date, original_url, category,
                                view_count, status
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            college_key, title, content, department,
                            published_date, original_url, category,
                            0, 'active'
                        ))
                        
                        saved_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error saving individual notice: {e}")
                        continue
                
                conn.commit()
                logger.info(f"Saved {saved_count} new notices for {college_key}")
                
                # 캐시 무효화
                cache_keys = [
                    f"notices:{college_key}:*",
                    "notices:all:*"
                ]
                for pattern in cache_keys:
                    if redis_client:
                        for key in redis_client.scan_iter(match=pattern):
                            cache.delete(key)
                    else:
                        # 메모리 캐시에서 해당 패턴의 키들 삭제
                        with cache_lock:
                            keys_to_delete = [k for k in memory_cache.keys() if pattern.replace('*', '') in k]
                            for key in keys_to_delete:
                                memory_cache.pop(key, None)
                
                return saved_count > 0
                
    except Exception as e:
        logger.error(f"Save notices to DB error: {e}")
        return False

# ===== API 엔드포인트 =====

@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    """회원가입 API"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False, 
                'message': '요청 데이터가 필요합니다',
                'error_code': 'INVALID_REQUEST'
            }), 400
        
        # 입력 검증
        email = validator.sanitize_string(data.get('email', '').strip().lower())
        password = data.get('password', '')
        
        is_valid_email, email_error = validator.validate_email(email)
        if not is_valid_email:
            return jsonify({
                'success': False, 
                'message': email_error,
                'error_code': 'INVALID_EMAIL'
            }), 400
        
        is_valid_password, password_error = validator.validate_password(password)
        if not is_valid_password:
            return jsonify({
                'success': False, 
                'message': password_error,
                'error_code': 'INVALID_PASSWORD'
            }), 400
        
        # 사용자 데이터 준비
        user_data = {
            'email': email,
            'password_hash': auth.hash_password(password),
            'name': validator.sanitize_string(data.get('name', ''), 100),
            'student_id': validator.sanitize_string(data.get('student_id', ''), 20),
            'major': validator.sanitize_string(data.get('major', ''), 100),
            'gpa': data.get('gpa'),
            'toeic_score': data.get('toeic_score')
        }
        
        # GPA 검증
        if user_data['gpa'] is not None:
            try:
                gpa_val = float(user_data['gpa'])
                if not (0.0 <= gpa_val <= 4.5):
                    return jsonify({
                        'success': False,
                        'message': 'GPA는 0.0에서 4.5 사이여야 합니다',
                        'error_code': 'INVALID_GPA'
                    }), 400
                user_data['gpa'] = gpa_val
            except (ValueError, TypeError):
                user_data['gpa'] = None
        
        # TOEIC 점수 검증
        if user_data['toeic_score'] is not None:
            try:
                toeic_val = int(user_data['toeic_score'])
                if not (10 <= toeic_val <= 990):
                    return jsonify({
                        'success': False,
                        'message': 'TOEIC 점수는 10에서 990 사이여야 합니다',
                        'error_code': 'INVALID_TOEIC'
                    }), 400
                user_data['toeic_score'] = toeic_val
            except (ValueError, TypeError):
                user_data['toeic_score'] = None
        
        # 사용자 생성
        try:
            user = db.create_user(user_data)
        except ValueError as e:
            return jsonify({
                'success': False,
                'message': str(e),
                'error_code': 'USER_EXISTS'
            }), 400
        
        # JWT 토큰 생성
        token = auth.create_jwt_token(user)
        
        logger.info(f"User registered successfully: {email}")
        
        # 응답 생성
        response = make_response(jsonify({
            'success': True,
            'user': {
                'id': str(user['id']),
                'email': user['email'],
                'name': user['name'],
                'major': user['major'],
                'gpa': float(user['gpa']) if user['gpa'] else None,
                'toeic_score': user['toeic_score']
            }
        }))
        
        # 보안 쿠키 설정
        exp_time = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRATION_HOURS)
        response.set_cookie(
            'dice_token', 
            token, 
            httponly=True, 
            secure=request.is_secure,  # HTTPS에서만 secure=True
            samesite='Lax',
            expires=exp_time
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Register error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False, 
            'message': '서버 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    """로그인 API"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '요청 데이터가 필요합니다',
                'error_code': 'INVALID_REQUEST'
            }), 400
        
        email = validator.sanitize_string(data.get('email', '').strip().lower())
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({
                'success': False,
                'message': '이메일과 비밀번호를 입력해주세요',
                'error_code': 'MISSING_CREDENTIALS'
            }), 400
        
        # 사용자 조회
        user = db.get_user_by_email(email)
        if not user:
            # 타이밍 공격 방지를 위한 더미 해시 연산
            auth.hash_password("dummy_password")
            return jsonify({
                'success': False,
                'message': '이메일 또는 비밀번호가 일치하지 않습니다',
                'error_code': 'INVALID_CREDENTIALS'
            }), 401
        
        # 비밀번호 검증
        if not auth.verify_password(password, user['password_hash']):
            return jsonify({
                'success': False,
                'message': '이메일 또는 비밀번호가 일치하지 않습니다',
                'error_code': 'INVALID_CREDENTIALS'
            }), 401
        
        # 마지막 로그인 시간 업데이트
        db.update_last_login(str(user['id']))
        
        # JWT 토큰 생성
        token = auth.create_jwt_token(user)
        
        logger.info(f"User logged in successfully: {email}")
        
        # 응답 생성
        response = make_response(jsonify({
            'success': True,
            'user': {
                'id': str(user['id']),
                'email': user['email'],
                'name': user['name'],
                'major': user['major'],
                'gpa': float(user['gpa']) if user['gpa'] else None,
                'toeic_score': user['toeic_score']
            }
        }))
        
        # 보안 쿠키 설정
        exp_time = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRATION_HOURS)
        response.set_cookie(
            'dice_token', 
            token, 
            httponly=True, 
            secure=request.is_secure,
            samesite='Lax',
            expires=exp_time
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': '서버 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """로그아웃 API"""
    try:
        # 토큰 무효화
        token = request.cookies.get('dice_token')
        if token:
            auth.revoke_token(token)
        
        response = make_response(jsonify({
            'success': True,
            'message': '로그아웃되었습니다'
        }))
        
        # 쿠키 삭제
        response.set_cookie(
            'dice_token', 
            '', 
            expires=0, 
            httponly=True, 
            secure=request.is_secure, 
            samesite='Lax'
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return jsonify({
            'success': False,
            'message': '로그아웃 처리 중 오류가 발생했습니다'
        }), 500

@app.route('/api/auth/me')
def get_current_user():
    """현재 사용자 정보 조회"""
    try:
        user_id = auth.get_user_from_request()
        if not user_id:
            return jsonify({
                'authenticated': False,
                'message': '인증되지 않은 사용자입니다'
            })
        
        user = db.get_user_by_id(user_id)
        if not user:
            return jsonify({
                'authenticated': False,
                'message': '사용자를 찾을 수 없습니다'
            })
        
        return jsonify({
            'authenticated': True,
            'user': {
                'id': str(user['id']),
                'email': user['email'],
                'name': user['name'],
                'major': user['major'],
                'gpa': float(user['gpa']) if user['gpa'] else None,
                'toeic_score': user['toeic_score']
            }
        })
        
    except Exception as e:
        logger.error(f"Get current user error: {e}")
        return jsonify({
            'authenticated': False,
            'message': '사용자 정보 조회 실패'
        }), 500

@app.route('/api/colleges')
def get_colleges():
    """단과대학 목록 조회"""
    cache_key = "colleges:list"
    cached_colleges = cache.get(cache_key)
    if cached_colleges:
        return jsonify({
            'success': True,
            'colleges': cached_colleges
        })
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, icon, color, url
                    FROM colleges
                    WHERE crawl_enabled = true
                    ORDER BY display_order, name
                """)
                
                colleges = {}
                for row in cur.fetchall():
                    colleges[row['id']] = {
                        'name': row['name'],
                        'icon': row['icon'],
                        'color': row['color'],
                        'url': row['url']
                    }
                
                # 10분간 캐시
                cache.set(cache_key, colleges, 600)
                
                return jsonify({
                    'success': True,
                    'colleges': colleges
                })
                
    except Exception as e:
        logger.error(f"Get colleges error: {e}")
        return jsonify({
            'success': False,
            'message': '단과대학 목록 조회에 실패했습니다'
        }), 500

@app.route('/api/notices/all')
@limiter.limit("30 per minute")
def get_all_notices():
    """전체 공지사항 조회"""
    try:
        user_id = auth.get_user_from_request()
        limit = min(int(request.args.get('limit', 100)), 200)  # 최대 200개 제한
        
        notices = notice_manager.get_notices_from_db('all', limit, user_id)
        
        return jsonify({
            'success': True,
            'notices': notices,
            'total': len(notices)
        })
        
    except Exception as e:
        logger.error(f"Get all notices error: {e}")
        return jsonify({
            'success': False,
            'message': '공지사항 조회에 실패했습니다'
        }), 500

@app.route('/api/notices/<college_key>')
@limiter.limit("30 per minute")
def get_notices(college_key):
    """단과대학별 공지사항 조회"""
    try:
        # 입력 검증
        if not re.match(r'^[a-zA-Z0-9_-]+$', college_key):
            return jsonify({
                'success': False,
                'message': '잘못된 단과대학 키입니다'
            }), 400
        
        user_id = auth.get_user_from_request()
        limit = min(int(request.args.get('limit', 50)), 100)
        
        notices = notice_manager.get_notices_from_db(college_key, limit, user_id)
        
        # 단과대학 정보 조회
        college_info = None
        if college_key == 'all':
            college_info = {
                'key': 'all',
                'name': '전체 공지사항',
                'icon': '📋',
                'color': '#2563eb'
            }
        else:
            # 캐시에서 단과대학 정보 조회
            colleges = cache.get("colleges:list")
            if not colleges:
                # 캐시 미스시 DB에서 조회
                with get_db_connection() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT id, name, icon, color
                            FROM colleges
                            WHERE id = %s AND crawl_enabled = true
                        """, (college_key,))
                        
                        college_row = cur.fetchone()
                        if college_row:
                            college_info = {
                                'key': college_key,
                                'name': college_row['name'],
                                'icon': college_row['icon'],
                                'color': college_row['color']
                            }
            else:
                college = colleges.get(college_key)
                if college:
                    college_info = {
                        'key': college_key,
                        'name': college['name'],
                        'icon': college['icon'],
                        'color': college['color']
                    }
        
        if not college_info and college_key != 'all':
            return jsonify({
                'success': False,
                'message': '존재하지 않는 단과대학입니다'
            }), 404
        
        return jsonify({
            'success': True,
            'college': college_info,
            'notices': notices,
            'total': len(notices)
        })
        
    except Exception as e:
        logger.error(f"Get notices error: {e}")
        return jsonify({
            'success': False,
            'message': '공지사항 조회에 실패했습니다'
        }), 500

@app.route('/api/health')
def health_check():
    """시스템 상태 확인"""
    db_connected = False
    stats = {
        'colleges': 0,
        'users': 0,
        'notices': 0,
        'cache_status': 'unknown'
    }
    
    try:
        with get_db_connection() as conn:
            db_connected = True
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        (SELECT COUNT(*) FROM colleges WHERE crawl_enabled = true) as colleges,
                        (SELECT COUNT(*) FROM users WHERE is_active = true) as users,
                        (SELECT COUNT(*) FROM notices WHERE status = 'active') as notices
                """)
                
                result = cur.fetchone()
                stats['colleges'], stats['users'], stats['notices'] = result
                
                # 캐시 상태 확인
                try:
                    cache.set("health_check", "ok", 10)
                    test_value = cache.get("health_check")
                    stats['cache_status'] = 'healthy' if test_value == "ok" else 'degraded'
                except:
                    stats['cache_status'] = 'error'
    
    except Exception as e:
        logger.error(f"Health check error: {e}")
    
    status = 'healthy' if db_connected else 'degraded'
    
    return jsonify({
        'status': status,
        'message': f'DICE 서버가 {"정상" if status == "healthy" else "부분적으로"} 작동 중입니다',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'version': '1.0.0',
        'environment': os.getenv('ENVIRONMENT', 'development'),
        'db_connected': db_connected,
        'stats': stats
    }), 200 if status == 'healthy' else 503

# ===== 크롤링 관리 API =====
@app.route('/api/crawl/trigger/<college_key>', methods=['POST'])
@require_auth
@limiter.limit("5 per hour")
def trigger_crawl(user_id, college_key):
    """수동 크롤링 트리거 API"""
    try:
        # 관리자 권한 체크 (향후 구현)
        # if not is_admin(user_id):
        #     return jsonify({'success': False, 'message': '권한이 없습니다'}), 403
        
        if not re.match(r'^[a-zA-Z0-9_-]+$', college_key):
            return jsonify({
                'success': False,
                'message': '잘못된 단과대학 키입니다',
                'error_code': 'INVALID_COLLEGE_KEY'
            }), 400
        
        # 단과대학 존재 확인
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, apify_task_id 
                    FROM colleges 
                    WHERE id = %s AND crawl_enabled = true
                """, (college_key,))
                
                college = cur.fetchone()
                
                if not college:
                    return jsonify({
                        'success': False,
                        'message': '존재하지 않거나 비활성화된 단과대학입니다',
                        'error_code': 'COLLEGE_NOT_FOUND'
                    }), 404
                
                if not college['apify_task_id']:
                    return jsonify({
                        'success': False,
                        'message': '크롤링 작업이 설정되지 않은 단과대학입니다',
                        'error_code': 'NO_CRAWL_TASK'
                    }), 400
        
        # Apify에서 데이터 가져오기 시도
        if config.APIFY_TOKEN:
            apify_data = get_apify_data(college['apify_task_id'])
            
            if apify_data:
                success = save_notices_to_db(college_key, apify_data)
                
                if success:
                    logger.info(f"Manual crawl triggered by user {user_id} for {college_key}")
                    return jsonify({
                        'success': True,
                        'message': f'{college["name"]} 크롤링이 완료되었습니다',
                        'notices_count': len(apify_data)
                    })
                else:
                    return jsonify({
                        'success': False,
                        'message': '크롤링 데이터 저장에 실패했습니다',
                        'error_code': 'SAVE_FAILED'
                    }), 500
            else:
                return jsonify({
                    'success': False,
                    'message': '크롤링 데이터를 가져올 수 없습니다',
                    'error_code': 'CRAWL_FAILED'
                }), 500
        else:
            return jsonify({
                'success': False,
                'message': 'APIFY 토큰이 설정되지 않았습니다',
                'error_code': 'APIFY_NOT_CONFIGURED'
            }), 503
            
    except Exception as e:
        logger.error(f"Trigger crawl error: {e}")
        return jsonify({
            'success': False,
            'message': '크롤링 실행 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

# ===== 사용자 프로필 관리 API =====
@app.route('/api/user/profile', methods=['GET'])
@require_auth
def get_user_profile(user_id):
    """사용자 프로필 조회 API"""
    try:
        user = db.get_user_by_id(user_id)
        
        if not user:
            return jsonify({
                'success': False,
                'message': '사용자를 찾을 수 없습니다',
                'error_code': 'USER_NOT_FOUND'
            }), 404
        
        return jsonify({
            'success': True,
            'profile': {
                'id': str(user['id']),
                'email': user['email'],
                'name': user['name'],
                'student_id': user['student_id'],
                'major': user['major'],
                'gpa': float(user['gpa']) if user['gpa'] else None,
                'toeic_score': user['toeic_score'],
                'created_at': user['created_at'].isoformat() if user['created_at'] else None,
                'last_login_at': user['last_login_at'].isoformat() if user['last_login_at'] else None
            }
        })
        
    except Exception as e:
        logger.error(f"Get user profile error: {e}")
        return jsonify({
            'success': False,
            'message': '프로필 조회에 실패했습니다',
            'error_code': 'PROFILE_GET_FAILED'
        }), 500

@app.route('/api/user/profile', methods=['PUT'])
@require_auth 
@limiter.limit("10 per hour")
def update_user_profile(user_id):
    """사용자 프로필 업데이트 API"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '업데이트할 데이터가 필요합니다',
                'error_code': 'INVALID_REQUEST'
            }), 400
        
        # 업데이트 가능한 필드 정의
        allowed_fields = ['name', 'student_id', 'major', 'gpa', 'toeic_score']
        update_fields = []
        values = []
        
        for field in allowed_fields:
            if field in data:
                value = data[field]
                
                # 필드별 검증
                if field == 'name':
                    value = validator.sanitize_string(value, 100)
                elif field == 'student_id':
                    value = validator.sanitize_string(value, 20)
                elif field == 'major':
                    value = validator.sanitize_string(value, 100)
                elif field == 'gpa':
                    if value is not None:
                        try:
                            value = float(value)
                            if not (0.0 <= value <= 4.5):
                                return jsonify({
                                    'success': False,
                                    'message': 'GPA는 0.0에서 4.5 사이여야 합니다',
                                    'error_code': 'INVALID_GPA'
                                }), 400
                        except (ValueError, TypeError):
                            value = None
                elif field == 'toeic_score':
                    if value is not None:
                        try:
                            value = int(value)
                            if not (10 <= value <= 990):
                                return jsonify({
                                    'success': False,
                                    'message': 'TOEIC 점수는 10에서 990 사이여야 합니다',
                                    'error_code': 'INVALID_TOEIC'
                                }), 400
                        except (ValueError, TypeError):
                            value = None
                
                update_fields.append(f"{field} = %s")
                values.append(value)
        
        if not update_fields:
            return jsonify({
                'success': False,
                'message': '업데이트할 유효한 필드가 없습니다',
                'error_code': 'NO_VALID_FIELDS'
            }), 400
        
        values.append(user_id)
        
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = f"""
                    UPDATE users 
                    SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, email, name, student_id, major, gpa, toeic_score
                """
                
                cur.execute(query, values)
                updated_user = cur.fetchone()
                conn.commit()
                
                if not updated_user:
                    return jsonify({
                        'success': False,
                        'message': '사용자를 찾을 수 없습니다',
                        'error_code': 'USER_NOT_FOUND'
                    }), 404
                
                # 캐시 무효화
                cache.delete(f"user:{user_id}")
                
                return jsonify({
                    'success': True,
                    'message': '프로필이 업데이트되었습니다',
                    'profile': {
                        'id': str(updated_user['id']),
                        'email': updated_user['email'],
                        'name': updated_user['name'],
                        'student_id': updated_user['student_id'],
                        'major': updated_user['major'],
                        'gpa': float(updated_user['gpa']) if updated_user['gpa'] else None,
                        'toeic_score': updated_user['toeic_score']
                    }
                })
                
    except Exception as e:
        logger.error(f"Update user profile error: {e}")
        return jsonify({
            'success': False,
            'message': '프로필 업데이트 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

# ===== 알림 관리 API =====
@app.route('/api/user/notifications')
@require_auth
def get_user_notifications(user_id):
    """사용자 알림 조회 API"""
    try:
        page = max(1, int(request.args.get('page', 1)))
        limit = min(50, int(request.args.get('limit', 20)))
        unread_only = request.args.get('unread_only', '').lower() == 'true'
        offset = (page - 1) * limit
        
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                base_query = """
                    SELECT id, notice_id, type, title, message, 
                           read_at, sent_at, created_at
                    FROM user_notifications
                    WHERE user_id = %s
                """
                
                params = [user_id]
                
                if unread_only:
                    base_query += " AND read_at IS NULL"
                
                base_query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])
                
                cur.execute(base_query, params)
                
                notifications = []
                for row in cur.fetchall():
                    notification = dict(row)
                    notification['id'] = str(notification['id'])
                    notification['notice_id'] = str(notification['notice_id']) if notification['notice_id'] else None
                    notification['read_at'] = notification['read_at'].isoformat() if notification['read_at'] else None
                    notification['sent_at'] = notification['sent_at'].isoformat() if notification['sent_at'] else None
                    notification['created_at'] = notification['created_at'].isoformat()
                    notifications.append(notification)
                
                # 총 개수 조회
                count_query = "SELECT COUNT(*) FROM user_notifications WHERE user_id = %s"
                count_params = [user_id]
                
                if unread_only:
                    count_query += " AND read_at IS NULL"
                
                cur.execute(count_query, count_params)
                total = cur.fetchone()[0]
                
                return jsonify({
                    'success': True,
                    'notifications': notifications,
                    'pagination': {
                        'page': page,
                        'limit': limit,
                        'total': total,
                        'pages': (total + limit - 1) // limit
                    }
                })
                
    except Exception as e:
        logger.error(f"Get user notifications error: {e}")
        return jsonify({
            'success': False,
            'message': '알림 조회 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/user/notifications/<notification_id>/read', methods=['POST'])
@require_auth
def mark_notification_read(user_id, notification_id):
    """알림 읽음 처리 API"""
    try:
        if not validator.validate_uuid(notification_id):
            return jsonify({
                'success': False,
                'message': '올바른 알림 ID가 아닙니다',
                'error_code': 'INVALID_NOTIFICATION_ID'
            }), 400
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE user_notifications 
                    SET read_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND user_id = %s AND read_at IS NULL
                """, (notification_id, user_id))
                
                if cur.rowcount == 0:
                    return jsonify({
                        'success': False,
                        'message': '알림을 찾을 수 없거나 이미 읽음 처리되었습니다',
                        'error_code': 'NOTIFICATION_NOT_FOUND'
                    }), 404
                
                conn.commit()
                
                return jsonify({
                    'success': True,
                    'message': '알림이 읽음 처리되었습니다'
                })
                
    except Exception as e:
        logger.error(f"Mark notification read error: {e}")
        return jsonify({
            'success': False,
            'message': '알림 처리 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

# ===== 사용자 설정 API =====
@app.route('/api/user/settings', methods=['GET'])
@require_auth
def get_user_settings(user_id):
    """사용자 설정 조회 API"""
    try:
        settings = user_settings_manager.get_user_settings(user_id)
        
        return jsonify({
            'success': True,
            'settings': settings
        })
        
    except Exception as e:
        logger.error(f"Get user settings error: {e}")
        return jsonify({
            'success': False,
            'message': '설정 조회 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/user/settings', methods=['PUT'])
@require_auth
@limiter.limit("20 per hour")
def update_user_settings(user_id):
    """사용자 설정 업데이트 API"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '업데이트할 설정 데이터가 필요합니다',
                'error_code': 'INVALID_REQUEST'
            }), 400
        
        success = user_settings_manager.update_user_settings(user_id, data)
        
        if success:
            return jsonify({
                'success': True,
                'message': '설정이 업데이트되었습니다'
            })
        else:
            return jsonify({
                'success': False,
                'message': '설정 업데이트에 실패했습니다',
                'error_code': 'UPDATE_FAILED'
            }), 500
            
    except Exception as e:
        logger.error(f"Update user settings error: {e}")
        return jsonify({
            'success': False,
            'message': '설정 업데이트 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/user/keywords', methods=['GET'])
@require_auth
def get_user_keywords(user_id):
    """사용자 키워드 조회 API"""
    try:
        keywords = user_settings_manager.get_user_keywords(user_id)
        
        return jsonify({
            'success': True,
            'keywords': keywords
        })
        
    except Exception as e:
        logger.error(f"Get user keywords error: {e}")
        return jsonify({
            'success': False,
            'message': '키워드 조회 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/user/keywords', methods=['PUT'])
@require_auth
@limiter.limit("30 per hour")
def update_user_keywords(user_id):
    """사용자 키워드 업데이트 API"""
    try:
        data = request.get_json()
        
        if not data or 'keywords' not in data:
            return jsonify({
                'success': False,
                'message': '키워드 데이터가 필요합니다',
                'error_code': 'INVALID_REQUEST'
            }), 400
        
        keywords = data['keywords']
        if not isinstance(keywords, list):
            return jsonify({
                'success': False,
                'message': '키워드는 배열 형태여야 합니다',
                'error_code': 'INVALID_FORMAT'
            }), 400
        
        success = user_settings_manager.update_user_keywords(user_id, keywords)
        
        if success:
            return jsonify({
                'success': True,
                'message': '키워드가 업데이트되었습니다'
            })
        else:
            return jsonify({
                'success': False,
                'message': '키워드 업데이트에 실패했습니다',
                'error_code': 'UPDATE_FAILED'
            }), 500
            
    except Exception as e:
        logger.error(f"Update user keywords error: {e}")
        return jsonify({
            'success': False,
            'message': '키워드 업데이트 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

# ===== 피드백 API =====
@app.route('/api/feedback', methods=['POST'])
@require_auth
@limiter.limit("10 per hour")
def submit_feedback(user_id):
    """피드백 제출 API"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '피드백 데이터가 필요합니다',
                'error_code': 'INVALID_REQUEST'
            }), 400
        
        # 필수 필드 검증
        if not data.get('message'):
            return jsonify({
                'success': False,
                'message': '피드백 메시지가 필요합니다',
                'error_code': 'MISSING_MESSAGE'
            }), 400
        
        feedback_data = {
            'type': data.get('type', 'general'),
            'subject': data.get('subject', ''),
            'message': data.get('message', ''),
            'priority': data.get('priority', 'normal')
        }
        
        # 입력값 검증
        valid_types = ['bug', 'feature', 'general', 'complaint', 'compliment']
        if feedback_data['type'] not in valid_types:
            feedback_data['type'] = 'general'
        
        valid_priorities = ['low', 'normal', 'high', 'urgent']
        if feedback_data['priority'] not in valid_priorities:
            feedback_data['priority'] = 'normal'
        
        success = feedback_manager.create_feedback(user_id, feedback_data)
        
        if success:
            return jsonify({
                'success': True,
                'message': '피드백이 성공적으로 제출되었습니다'
            })
        else:
            return jsonify({
                'success': False,
                'message': '피드백 제출에 실패했습니다',
                'error_code': 'SUBMIT_FAILED'
            }), 500
            
    except Exception as e:
        logger.error(f"Submit feedback error: {e}")
        return jsonify({
            'success': False,
            'message': '피드백 제출 중 오류가 발생했습니다',
            'error_code': 'INTERNAL_ERROR'
        }), 500

# ===== 관리자 API (향후 확장용) =====
@app.route('/api/admin/stats')
@require_auth
def get_admin_stats(user_id):
    """관리자 통계 API (향후 구현)"""
    # 관리자 권한 체크 로직 필요
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 기본 통계 조회
                cur.execute("""
                    SELECT 
                        (SELECT COUNT(*) FROM users WHERE is_active = true) as active_users,
                        (SELECT COUNT(*) FROM notices WHERE status = 'active') as active_notices,
                        (SELECT COUNT(*) FROM colleges WHERE crawl_enabled = true) as active_colleges,
                        (SELECT COUNT(*) FROM user_feedback WHERE status = 'open') as open_feedback
                """)
                
                stats = dict(cur.fetchone())
                
                return jsonify({
                    'success': True,
                    'stats': stats
                })
                
    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        return jsonify({
            'success': False,
            'message': '통계 조회에 실패했습니다',
            'error_code': 'STATS_FAILED'
        }), 500

# ===== 정적 파일 및 SPA 라우팅 =====
@app.route('/')
def index():
    """메인 페이지"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/auth')
def auth_page():
    """인증 페이지"""
    return send_from_directory(app.static_folder, 'auth.html')

@app.route('/dashboard')
def dashboard_page():
    """대시보드 페이지"""
    return send_from_directory(app.static_folder, 'dashboard.html')

@app.route('/settings')
def settings_page():
    """설정 페이지"""
    return send_from_directory(app.static_folder, 'settings.html')

@app.route('/calendar')
def calendar_page():
    """캘린더 페이지 (향후 구현)"""
    return send_from_directory(app.static_folder, 'calendar.html')

@app.route('/ai-chat')
def ai_chat_page():
    """AI 채팅 페이지 (향후 구현)"""
    return send_from_directory(app.static_folder, 'ai-chat.html')

@app.route('/notice/<college_key>/<notice_id>')
def notice_detail_page(college_key, notice_id):
    """공지사항 상세 페이지"""
    return send_from_directory(app.static_folder, 'notice-detail.html')

# 기존 HTML 파일들과의 호환성을 위한 라우트
@app.route('/auth.html')
def serve_auth_html():
    return send_from_directory(app.static_folder, 'auth.html')

@app.route('/dashboard.html')
def serve_dashboard_html():
    return send_from_directory(app.static_folder, 'dashboard.html')

@app.route('/settings.html')
def serve_settings_html():
    return send_from_directory(app.static_folder, 'settings.html')

@app.route('/<path:filename>')
def serve_static_files(filename):
    """정적 파일 서빙"""
    try:
        return send_from_directory(app.static_folder, filename)
    except:
        # SPA 라우팅 지원 - 존재하지 않는 경로는 index.html로
        if not filename.startswith('api/') and '.' not in filename:
            return send_from_directory(app.static_folder, 'index.html')
        return jsonify({
            'success': False,
            'message': '요청한 파일을 찾을 수 없습니다',
            'error_code': 'FILE_NOT_FOUND'
        }), 404

# ===== 에러 핸들러 =====
@app.errorhandler(400)
def bad_request(error):
    return jsonify({
        'success': False,
        'message': '잘못된 요청입니다',
        'error_code': 'BAD_REQUEST'
    }), 400

@app.errorhandler(401)
def unauthorized(error):
    return jsonify({
        'success': False,
        'message': '인증이 필요합니다',
        'error_code': 'UNAUTHORIZED'
    }), 401

@app.errorhandler(403)
def forbidden(error):
    return jsonify({
        'success': False,
        'message': '접근이 거부되었습니다',
        'error_code': 'FORBIDDEN'
    }), 403

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'message': '요청한 리소스를 찾을 수 없습니다',
            'error_code': 'NOT_FOUND'
        }), 404
    else:
        # SPA 라우팅 지원
        return send_from_directory(app.static_folder, 'index.html')

@app.errorhandler(429)
def rate_limit_handler(error):
    return jsonify({
        'success': False,
        'message': '요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요',
        'error_code': 'RATE_LIMIT_EXCEEDED'
    }), 429

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({
        'success': False,
        'message': '서버 내부 오류가 발생했습니다',
        'error_code': 'INTERNAL_ERROR'
    }), 500

# ===== 애플리케이션 시작 =====
if __name__ == '__main__':
    logger.info("=" * 80)
    logger.info("🎓 연세대학교 통합 공지사항 시스템 (DICE) - Production Enhanced")
    logger.info("=" * 80)
    logger.info(f"💾 Database: {'Connected' if connection_pool else 'Failed'}")
    logger.info(f"🔐 JWT Security: Enabled")
    logger.info(f"📦 Cache: {'Redis' if redis_client else 'Memory'}")
    logger.info(f"🕷️ APIFY Integration: {'Enabled' if config.APIFY_TOKEN else 'Disabled'}")
    logger.info(f"🌐 Environment: {os.getenv('ENVIRONMENT', 'development')}")
    
    PORT = int(os.getenv('PORT', 8080))
    DEBUG = os.getenv('ENVIRONMENT', 'development') == 'development'
    
    logger.info(f"🚀 Starting server on port {PORT} (debug={DEBUG})")
    logger.info("=" * 80)
    
    app.run(debug=DEBUG, host='0.0.0.0', port=PORT)