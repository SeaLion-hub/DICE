-- Enhanced Database Schema for DICE System
-- Production-ready with indexes, constraints, and performance optimizations

-- 데이터베이스 설정
SET timezone = 'Asia/Seoul';

-- UUID 확장 활성화
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm"; -- 텍스트 검색 성능 향상

-- ===== 사용자 관리 테이블 =====

-- 사용자 테이블
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100),
    student_id VARCHAR(20),
    major VARCHAR(100),
    gpa DECIMAL(3,2) CHECK (gpa >= 0.0 AND gpa <= 4.5),
    toeic_score INTEGER CHECK (toeic_score >= 10 AND toeic_score <= 990),
    is_active BOOLEAN DEFAULT true,
    is_verified BOOLEAN DEFAULT false,
    verification_token VARCHAR(255),
    reset_password_token VARCHAR(255),
    reset_password_expires TIMESTAMP,
    last_login_at TIMESTAMP,
    login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP,
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 사용자 설정 테이블
CREATE TABLE IF NOT EXISTS user_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    push_notifications BOOLEAN DEFAULT true,
    email_notifications BOOLEAN DEFAULT true,
    deadline_alerts BOOLEAN DEFAULT true,
    ai_recommendations BOOLEAN DEFAULT true,
    notification_frequency VARCHAR(20) DEFAULT 'daily', -- 'real_time', 'daily', 'weekly'
    theme VARCHAR(20) DEFAULT 'light', -- 'light', 'dark', 'auto'
    language VARCHAR(10) DEFAULT 'ko',
    timezone VARCHAR(50) DEFAULT 'Asia/Seoul',
    keywords TEXT[], -- 관심 키워드 배열
    subscribed_colleges TEXT[], -- 구독 단과대학 배열
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 사용자 세션 테이블 (JWT 블랙리스트)
CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    jti VARCHAR(255) UNIQUE NOT NULL, -- JWT ID
    token_hash VARCHAR(255) NOT NULL,
    is_revoked BOOLEAN DEFAULT false,
    user_agent TEXT,
    ip_address INET,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===== 단과대학 및 공지사항 테이블 =====

-- 단과대학 테이블
CREATE TABLE IF NOT EXISTS colleges (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    icon VARCHAR(10) DEFAULT '🏫',
    color VARCHAR(7) DEFAULT '#2563eb',
    url TEXT,
    apify_task_id VARCHAR(100),
    crawl_enabled BOOLEAN DEFAULT true,
    display_order INTEGER DEFAULT 0,
    description TEXT,
    contact_info JSONB DEFAULT '{}',
    last_crawled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 공지사항 테이블
CREATE TABLE IF NOT EXISTS notices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    college_id VARCHAR(50) REFERENCES colleges(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT,
    department VARCHAR(200),
    writer VARCHAR(100),
    original_id VARCHAR(200),
    original_url TEXT,
    published_date DATE,
    deadline_date DATE,
    event_date DATE,
    view_count INTEGER DEFAULT 0,
    category VARCHAR(50) DEFAULT 'general',
    priority VARCHAR(20) DEFAULT 'normal', -- 'low', 'normal', 'high', 'urgent'
    status VARCHAR(20) DEFAULT 'active', -- 'active', 'archived', 'deleted'
    tags TEXT[],
    metadata JSONB DEFAULT '{}',
    content_hash VARCHAR(64),
    search_vector tsvector, -- 전문 검색용
    last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT unique_college_original_id UNIQUE (college_id, original_id)
);

-- 공지사항 첨부파일 테이블
CREATE TABLE IF NOT EXISTS notice_attachments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    notice_id UUID REFERENCES notices(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255),
    file_size BIGINT,
    mime_type VARCHAR(100),
    download_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===== 사용자 상호작용 테이블 =====

-- 사용자-공지사항 상호작용 테이블
CREATE TABLE IF NOT EXISTS user_notice_interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    notice_id UUID REFERENCES notices(id) ON DELETE CASCADE,
    viewed_at TIMESTAMP,
    bookmarked BOOLEAN DEFAULT false,
    bookmarked_at TIMESTAMP,
    hidden BOOLEAN DEFAULT false,
    hidden_at TIMESTAMP,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    feedback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT unique_user_notice UNIQUE (user_id, notice_id)
);

-- 사용자 알림 테이블
CREATE TABLE IF NOT EXISTS user_notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    notice_id UUID REFERENCES notices(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL, -- 'new_notice', 'deadline_reminder', 'keyword_match'
    title VARCHAR(200) NOT NULL,
    message TEXT,
    read_at TIMESTAMP,
    sent_at TIMESTAMP,
    delivery_method VARCHAR(20) DEFAULT 'push', -- 'push', 'email', 'sms'
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===== 시스템 관리 테이블 =====

-- 크롤링 로그 테이블
CREATE TABLE IF NOT EXISTS crawl_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    college_id VARCHAR(50) REFERENCES colleges(id),
    status VARCHAR(20) NOT NULL, -- 'success', 'failed', 'partial'
    notices_fetched INTEGER DEFAULT 0,
    notices_new INTEGER DEFAULT 0,
    notices_updated INTEGER DEFAULT 0,
    error_message TEXT,
    execution_time_ms INTEGER,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- API 사용량 로그 테이블
CREATE TABLE IF NOT EXISTS api_usage_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    endpoint VARCHAR(100) NOT NULL,
    method VARCHAR(10) NOT NULL,
    status_code INTEGER,
    response_time_ms INTEGER,
    ip_address INET,
    user_agent TEXT,
    request_size INTEGER,
    response_size INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 시스템 설정 테이블
CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 피드백 테이블
CREATE TABLE IF NOT EXISTS user_feedback (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) DEFAULT 'general', -- 'bug', 'feature', 'general'
    subject VARCHAR(200),
    message TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'open', -- 'open', 'in_progress', 'closed'
    priority VARCHAR(20) DEFAULT 'normal',
    admin_response TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===== 인덱스 생성 (성능 최적화) =====

-- 사용자 테이블 인덱스
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_student_id ON users(student_id);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = true;

-- 사용자 세션 인덱스
CREATE INDEX IF NOT EXISTS idx_user_sessions_jti ON user_sessions(jti);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON user_sessions(expires_at);

-- 공지사항 테이블 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_college_id ON notices(college_id);
CREATE INDEX IF NOT EXISTS idx_notices_published_date ON notices(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_category ON notices(category);
CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_notices_deadline ON notices(deadline_date) WHERE deadline_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notices_search ON notices USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_notices_title_gin ON notices USING gin(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_notices_content_hash ON notices(content_hash);

-- 복합 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_college_published ON notices(college_id, published_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_status_published ON notices(status, published_date DESC) WHERE status = 'active';

-- 사용자 상호작용 인덱스
CREATE INDEX IF NOT EXISTS idx_user_interactions_user_id ON user_notice_interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_interactions_notice_id ON user_notice_interactions(notice_id);
CREATE INDEX IF NOT EXISTS idx_user_interactions_bookmarked ON user_notice_interactions(user_id, bookmarked) WHERE bookmarked = true;

-- 알림 인덱스
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON user_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON user_notifications(user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON user_notifications(type);

-- 로그 테이블 인덱스
CREATE INDEX IF NOT EXISTS idx_crawl_logs_college_date ON crawl_logs(college_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_logs_user_date ON api_usage_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_logs_endpoint ON api_usage_logs(endpoint, created_at DESC);

-- ===== 트리거 및 함수 =====

-- 업데이트 시간 자동 갱신 함수
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 업데이트 트리거 생성
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN 
        SELECT table_name 
        FROM information_schema.columns 
        WHERE column_name = 'updated_at' 
        AND table_schema = 'public'
    LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS trigger_update_%I_updated_at ON %I;
            CREATE TRIGGER trigger_update_%I_updated_at
                BEFORE UPDATE ON %I
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        ', t, t, t, t);
    END LOOP;
END $$;

-- 공지사항 검색 벡터 업데이트 함수
CREATE OR REPLACE FUNCTION update_notice_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := 
        setweight(to_tsvector('korean', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('korean', COALESCE(NEW.content, '')), 'B') ||
        setweight(to_tsvector('korean', COALESCE(NEW.department, '')), 'C');
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 검색 벡터 업데이트 트리거
DROP TRIGGER IF EXISTS trigger_notice_search_vector ON notices;
CREATE TRIGGER trigger_notice_search_vector
    BEFORE INSERT OR UPDATE ON notices
    FOR EACH ROW EXECUTE FUNCTION update_notice_search_vector();

-- 조회수 증가 함수 (원자성 보장)
CREATE OR REPLACE FUNCTION increment_notice_view_count(
    p_notice_id UUID,
    p_user_id UUID DEFAULT NULL
)
RETURNS void AS $
BEGIN
    -- 공지사항 조회수 증가
    UPDATE notices 
    SET view_count = view_count + 1 
    WHERE id = p_notice_id;
    
    -- 사용자가 있으면 상호작용 기록
    IF p_user_id IS NOT NULL THEN
        INSERT INTO user_notice_interactions (user_id, notice_id, viewed_at)
        VALUES (p_user_id, p_notice_id, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, notice_id) 
        DO UPDATE SET viewed_at = CURRENT_TIMESTAMP;
    END IF;
END;
$ LANGUAGE plpgsql;

-- 사용자 알림 생성 함수
CREATE OR REPLACE FUNCTION create_user_notification(
    p_user_id UUID,
    p_notice_id UUID,
    p_type VARCHAR(50),
    p_title VARCHAR(200),
    p_message TEXT DEFAULT NULL
)
RETURNS UUID AS $
DECLARE
    notification_id UUID;
BEGIN
    INSERT INTO user_notifications (user_id, notice_id, type, title, message)
    VALUES (p_user_id, p_notice_id, p_type, p_title, p_message)
    RETURNING id INTO notification_id;
    
    RETURN notification_id;
END;
$ LANGUAGE plpgsql;

-- 북마크 토글 함수
CREATE OR REPLACE FUNCTION toggle_notice_bookmark(
    p_user_id UUID,
    p_notice_id UUID
)
RETURNS BOOLEAN AS $
DECLARE
    current_bookmark BOOLEAN;
BEGIN
    -- 현재 북마크 상태 확인
    SELECT COALESCE(bookmarked, false) INTO current_bookmark
    FROM user_notice_interactions
    WHERE user_id = p_user_id AND notice_id = p_notice_id;
    
    -- 상호작용 레코드 생성 또는 업데이트
    INSERT INTO user_notice_interactions (user_id, notice_id, bookmarked, bookmarked_at)
    VALUES (p_user_id, p_notice_id, NOT COALESCE(current_bookmark, false), CURRENT_TIMESTAMP)
    ON CONFLICT (user_id, notice_id)
    DO UPDATE SET 
        bookmarked = NOT COALESCE(current_bookmark, false),
        bookmarked_at = CASE 
            WHEN NOT COALESCE(current_bookmark, false) THEN CURRENT_TIMESTAMP 
            ELSE NULL 
        END;
    
    RETURN NOT COALESCE(current_bookmark, false);
END;
$ LANGUAGE plpgsql;

-- ===== 기본 데이터 삽입 =====

-- 시스템 설정 기본값
INSERT INTO system_settings (key, value, description) VALUES
('maintenance_mode', 'false', '시스템 점검 모드'),
('max_notices_per_page', '50', '페이지당 최대 공지사항 수'),
('cache_ttl_minutes', '5', '캐시 만료 시간 (분)'),
('notification_batch_size', '100', '알림 배치 처리 크기'),
('crawl_interval_minutes', '30', '크롤링 간격 (분)')
ON CONFLICT (key) DO NOTHING;

-- 기본 단과대학 데이터
INSERT INTO colleges (id, name, icon, color, display_order, description) VALUES
('main', '메인 공지사항', '🏫', '#003876', 1, '연세대학교 메인 공지사항'),
('liberal', '문과대학', '📚', '#8B4513', 2, '문과대학 공지사항'),
('business', '상경대학', '📊', '#FFB700', 3, '상경대학 공지사항'),
('management', '경영대학', '💼', '#1E90FF', 4, '경영대학 공지사항'),
('engineering', '공과대학', '⚙️', '#DC143C', 5, '공과대학 공지사항'),
('life', '생명시스템대학', '🧬', '#228B22', 6, '생명시스템대학 공지사항'),
('ai', '인공지능융합대학', '🤖', '#9370DB', 7, '인공지능융합대학 공지사항'),
('theology', '신과대학', '✝️', '#4B0082', 8, '신과대학 공지사항'),
('social', '사회과학대학', '🏛️', '#2E8B57', 9, '사회과학대학 공지사항'),
('music', '음악대학', '🎵', '#FF1493', 10, '음악대학 공지사항'),
('human', '생활과학대학', '🏠', '#FF6347', 11, '생활과학대학 공지사항'),
('education', '교육과학대학', '🎓', '#4169E1', 12, '교육과학대학 공지사항'),
('underwood', '언더우드국제대학', '🌍', '#FF8C00', 13, '언더우드국제대학 공지사항'),
('global', '글로벌인재대학', '🌐', '#008B8B', 14, '글로벌인재대학 공지사항'),
('medicine', '의과대학', '⚕️', '#B22222', 15, '의과대학 공지사항'),
('dentistry', '치과대학', '🦷', '#5F9EA0', 16, '치과대학 공지사항'),
('nursing', '간호대학', '👩‍⚕️', '#DB7093', 17, '간호대학 공지사항'),
('pharmacy', '약학대학', '💊', '#663399', 18, '약학대학 공지사항')
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    icon = EXCLUDED.icon,
    color = EXCLUDED.color,
    display_order = EXCLUDED.display_order,
    description = EXCLUDED.description;

-- ===== 데이터 정리 및 유지보수 함수 =====

-- 만료된 세션 정리 함수
CREATE OR REPLACE FUNCTION cleanup_expired_sessions()
RETURNS INTEGER AS $
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM user_sessions 
    WHERE expires_at < CURRENT_TIMESTAMP OR is_revoked = true;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    
    INSERT INTO crawl_logs (college_id, status, notices_fetched, started_at, completed_at)
    VALUES (NULL, 'cleanup', deleted_count, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
    
    RETURN deleted_count;
END;
$ LANGUAGE plpgsql;

-- 오래된 로그 정리 함수
CREATE OR REPLACE FUNCTION cleanup_old_logs(days_to_keep INTEGER DEFAULT 90)
RETURNS INTEGER AS $
DECLARE
    deleted_count INTEGER := 0;
    temp_count INTEGER;
BEGIN
    -- API 로그 정리
    DELETE FROM api_usage_logs 
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '%d days' 
    USING days_to_keep;
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    -- 크롤링 로그 정리 (성공한 것만, 실패한 것은 더 오래 보관)
    DELETE FROM crawl_logs 
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '%d days' 
    AND status = 'success'
    USING days_to_keep;
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    -- 읽은 알림 정리
    DELETE FROM user_notifications 
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '%d days' 
    AND read_at IS NOT NULL
    USING days_to_keep;
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    RETURN deleted_count;
END;
$ LANGUAGE plpgsql;

-- 통계 업데이트 함수
CREATE OR REPLACE FUNCTION update_notice_statistics()
RETURNS void AS $
BEGIN
    -- 공지사항별 북마크 수 업데이트 (필요시 notices 테이블에 컬럼 추가)
    -- UPDATE notices SET bookmark_count = (
    --     SELECT COUNT(*) FROM user_notice_interactions 
    --     WHERE notice_id = notices.id AND bookmarked = true
    -- );
    
    -- 여기에 다른 통계 업데이트 로직 추가 가능
    NULL;
END;
$ LANGUAGE plpgsql;

-- ===== 성능 모니터링 뷰 =====

-- 인기 공지사항 뷰
CREATE OR REPLACE VIEW popular_notices AS
SELECT 
    n.id,
    n.title,
    n.college_id,
    c.name as college_name,
    n.view_count,
    n.published_date,
    COUNT(uni.id) FILTER (WHERE uni.bookmarked = true) as bookmark_count,
    AVG(uni.rating) as avg_rating
FROM notices n
JOIN colleges c ON n.college_id = c.id
LEFT JOIN user_notice_interactions uni ON n.id = uni.notice_id
WHERE n.status = 'active'
GROUP BY n.id, n.title, n.college_id, c.name, n.view_count, n.published_date
ORDER BY n.view_count DESC, bookmark_count DESC;

-- 사용자 활동 통계 뷰
CREATE OR REPLACE VIEW user_activity_stats AS
SELECT 
    u.id,
    u.email,
    u.last_login_at,
    COUNT(DISTINCT uni.notice_id) FILTER (WHERE uni.viewed_at > CURRENT_TIMESTAMP - INTERVAL '7 days') as notices_viewed_week,
    COUNT(DISTINCT uni.notice_id) FILTER (WHERE uni.bookmarked = true) as total_bookmarks,
    COUNT(DISTINCT un.id) FILTER (WHERE un.read_at IS NULL) as unread_notifications
FROM users u
LEFT JOIN user_notice_interactions uni ON u.id = uni.user_id
LEFT JOIN user_notifications un ON u.id = un.user_id
WHERE u.is_active = true
GROUP BY u.id, u.email, u.last_login_at;

-- 단과대학별 공지사항 통계 뷰
CREATE OR REPLACE VIEW college_notice_stats AS
SELECT 
    c.id,
    c.name,
    COUNT(n.id) as total_notices,
    COUNT(n.id) FILTER (WHERE n.published_date >= CURRENT_DATE - INTERVAL '30 days') as notices_last_month,
    AVG(n.view_count) as avg_views,
    MAX(n.published_date) as latest_notice_date,
    c.last_crawled_at
FROM colleges c
LEFT JOIN notices n ON c.id = n.college_id AND n.status = 'active'
WHERE c.crawl_enabled = true
GROUP BY c.id, c.name, c.last_crawled_at
ORDER BY c.display_order;

-- ===== 정기 작업을 위한 함수들 =====

-- 마감 알림 생성 함수
CREATE OR REPLACE FUNCTION create_deadline_notifications()
RETURNS INTEGER AS $
DECLARE
    notification_count INTEGER := 0;
    notice_rec RECORD;
BEGIN
    -- 3일 후 마감인 공지사항에 대한 알림 생성
    FOR notice_rec IN
        SELECT DISTINCT n.id, n.title, n.deadline_date, us.user_id
        FROM notices n
        CROSS JOIN user_settings us
        WHERE n.deadline_date = CURRENT_DATE + INTERVAL '3 days'
        AND n.status = 'active'
        AND us.deadline_alerts = true
        AND NOT EXISTS (
            SELECT 1 FROM user_notifications un
            WHERE un.user_id = us.user_id 
            AND un.notice_id = n.id 
            AND un.type = 'deadline_reminder'
            AND un.created_at > CURRENT_DATE
        )
    LOOP
        INSERT INTO user_notifications (user_id, notice_id, type, title, message)
        VALUES (
            notice_rec.user_id,
            notice_rec.id,
            'deadline_reminder',
            '마감 3일 전: ' || notice_rec.title,
            notice_rec.title || '의 마감일이 3일 남았습니다. (' || notice_rec.deadline_date || ')'
        );
        
        notification_count := notification_count + 1;
    END LOOP;
    
    RETURN notification_count;
END;
$ LANGUAGE plpgsql;

-- ===== 백업 및 복원 스크립트 =====

-- 중요 데이터 백업을 위한 뷰
CREATE OR REPLACE VIEW backup_essential_data AS
SELECT 
    'users' as table_name,
    COUNT(*) as record_count,
    MAX(created_at) as latest_record
FROM users
WHERE is_active = true
UNION ALL
SELECT 
    'notices' as table_name,
    COUNT(*) as record_count,
    MAX(created_at) as latest_record
FROM notices
WHERE status = 'active'
UNION ALL
SELECT 
    'user_notice_interactions' as table_name,
    COUNT(*) as record_count,
    MAX(created_at) as latest_record
FROM user_notice_interactions;

-- ===== 권한 및 보안 설정 =====

-- 애플리케이션용 제한된 권한 역할 생성 (선택사항)
-- CREATE ROLE dice_app_user WITH LOGIN PASSWORD 'secure_password';
-- GRANT CONNECT ON DATABASE your_database TO dice_app_user;
-- GRANT USAGE ON SCHEMA public TO dice_app_user;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dice_app_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dice_app_user;

-- 함수 실행 권한
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO dice_app_user;

-- ===== 최종 성능 최적화 =====

-- 자주 사용되는 쿼리를 위한 추가 인덱스
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notices_user_bookmarks 
ON user_notice_interactions(user_id, bookmarked, bookmarked_at) 
WHERE bookmarked = true;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notices_recent_active 
ON notices(published_date DESC, status) 
WHERE status = 'active' AND published_date >= CURRENT_DATE - INTERVAL '1 year';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_notifications_unread 
ON user_notifications(user_id, created_at DESC) 
WHERE read_at IS NULL;

-- 파티셔닝을 위한 준비 (대용량 데이터시 고려)
-- CREATE TABLE notices_y2024 PARTITION OF notices 
-- FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

-- ===== 스키마 버전 관리 =====
INSERT INTO system_settings (key, value, description) VALUES
('schema_version', '"1.0.0"', '데이터베이스 스키마 버전'),
('last_migration', '"' || CURRENT_TIMESTAMP::text || '"', '마지막 마이그레이션 실행 시간')
ON CONFLICT (key) DO UPDATE SET 
    value = EXCLUDED.value,
    updated_at = CURRENT_TIMESTAMP;

-- 스키마 완료 로그
DO $
BEGIN
    RAISE NOTICE 'DICE Database Schema v1.0.0 설치 완료';
    RAISE NOTICE '총 테이블 수: %', (
        SELECT COUNT(*) FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    );
    RAISE NOTICE '총 인덱스 수: %', (
        SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public'
    );
    RAISE NOTICE '총 함수 수: %', (
        SELECT COUNT(*) FROM information_schema.routines 
        WHERE routine_schema = 'public' AND routine_type = 'FUNCTION'
    );
END $;