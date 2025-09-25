-- DICE 통합 공지사항 시스템 PostgreSQL Database Schema
-- Version: 1.0.0
-- Created: 2025-01-15

-- ===================================
-- 1. DATABASE SETUP
-- ===================================

-- CREATE DATABASE dice_db ... (Railway에서는 주석 유지)
-- \c dice_db;

-- Extensions (권한 허용 시에만 설치)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ===================================
-- 2. ENUM TYPES
-- ===================================

CREATE TYPE user_role AS ENUM ('student', 'admin', 'moderator');

CREATE TYPE notice_category AS ENUM (
    'general','scholarship','internship','competition',
    'recruitment','academic','seminar','event'
);

CREATE TYPE notice_status AS ENUM ('active', 'archived', 'deleted');

-- ===================================
-- 3. TABLES
-- ===================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100),
    student_id VARCHAR(20),
    role user_role DEFAULT 'student',
    is_active BOOLEAN DEFAULT true,
    email_verified BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT email_format CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$')
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_student_id ON users(student_id) WHERE student_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at DESC);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    push_notifications BOOLEAN DEFAULT true,
    email_notifications BOOLEAN DEFAULT true,
    deadline_alerts BOOLEAN DEFAULT true,
    ai_recommendations BOOLEAN DEFAULT true,
    notification_time TIME DEFAULT '09:00:00',
    deadline_alert_days INTEGER DEFAULT 3 CHECK (deadline_alert_days BETWEEN 1 AND 30),
    interested_categories notice_category[] DEFAULT ARRAY['general']::notice_category[],
    excluded_keywords TEXT[],
    notices_per_page INTEGER DEFAULT 20 CHECK (notices_per_page BETWEEN 10 AND 100),
    default_sort_order VARCHAR(20) DEFAULT 'date_desc',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS colleges (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    icon VARCHAR(10),
    color VARCHAR(7),
    url VARCHAR(255),
    apify_task_id VARCHAR(100),
    crawl_enabled BOOLEAN DEFAULT true,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_colleges_display_order ON colleges(display_order, name);

CREATE TABLE IF NOT EXISTS user_college_subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    college_id VARCHAR(50) NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
    notifications_enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, college_id)
);
CREATE INDEX IF NOT EXISTS idx_user_colleges_user ON user_college_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_colleges_college ON user_college_subscriptions(college_id);

CREATE TABLE IF NOT EXISTS notices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    college_id VARCHAR(50) NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    content TEXT,
    department VARCHAR(200),
    writer VARCHAR(100),
    original_id VARCHAR(100),
    original_url VARCHAR(500),
    category notice_category DEFAULT 'general',
    status notice_status DEFAULT 'active',
    published_date DATE,
    deadline_date DATE,
    event_date DATE,
    view_count INTEGER DEFAULT 0,
    click_count INTEGER DEFAULT 0,
    crawled_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    content_hash VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_notice_per_college UNIQUE (college_id, original_id),
    CONSTRAINT valid_dates CHECK (
        (deadline_date IS NULL OR deadline_date >= published_date) AND
        (event_date IS NULL OR event_date >= published_date)
    )
);
CREATE INDEX IF NOT EXISTS idx_notices_college ON notices(college_id);
CREATE INDEX IF NOT EXISTS idx_notices_published ON notices(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_deadline ON notices(deadline_date) WHERE deadline_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notices_category ON notices(category);
CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_notices_content_hash ON notices(content_hash);
CREATE INDEX IF NOT EXISTS idx_notices_search ON notices USING GIN (to_tsvector('korean', title || ' ' || COALESCE(content, '')));

CREATE TABLE IF NOT EXISTS user_notice_interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notice_id UUID NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    viewed BOOLEAN DEFAULT false,
    clicked BOOLEAN DEFAULT false,
    bookmarked BOOLEAN DEFAULT false,
    hidden BOOLEAN DEFAULT false,
    viewed_at TIMESTAMP WITH TIME ZONE,
    clicked_at TIMESTAMP WITH TIME ZONE,
    bookmarked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, notice_id)
);
CREATE INDEX IF NOT EXISTS idx_interactions_user ON user_notice_interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_interactions_notice ON user_notice_interactions(notice_id);
CREATE INDEX IF NOT EXISTS idx_interactions_bookmarked ON user_notice_interactions(user_id, bookmarked) WHERE bookmarked = true;

CREATE TABLE IF NOT EXISTS crawl_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    college_id VARCHAR(50) REFERENCES colleges(id) ON DELETE SET NULL,
    task_id VARCHAR(100),
    run_id VARCHAR(100),
    status VARCHAR(50),
    error_message TEXT,
    notices_fetched INTEGER DEFAULT 0,
    notices_new INTEGER DEFAULT 0,
    notices_updated INTEGER DEFAULT 0,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER GENERATED ALWAYS AS (EXTRACT(EPOCH FROM (completed_at - started_at))) STORED,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_crawl_logs_college ON crawl_logs(college_id);
CREATE INDEX IF NOT EXISTS idx_crawl_logs_status ON crawl_logs(status);
CREATE INDEX IF NOT EXISTS idx_crawl_logs_created ON crawl_logs(created_at DESC);

-- ===================================
-- 4. FUNCTIONS & TRIGGERS
-- ===================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_user_settings_updated_at ON user_settings;
CREATE TRIGGER update_user_settings_updated_at BEFORE UPDATE ON user_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_colleges_updated_at ON colleges;
CREATE TRIGGER update_colleges_updated_at BEFORE UPDATE ON colleges
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_notices_updated_at ON notices;
CREATE TRIGGER update_notices_updated_at BEFORE UPDATE ON notices
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_interactions_updated_at ON user_notice_interactions;
CREATE TRIGGER update_interactions_updated_at BEFORE UPDATE ON user_notice_interactions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE FUNCTION increment_notice_view_count(
    p_notice_id UUID,
    p_user_id UUID DEFAULT NULL
) RETURNS void AS $$
BEGIN
    UPDATE notices SET view_count = view_count + 1 WHERE id = p_notice_id;
    IF p_user_id IS NOT NULL THEN
        INSERT INTO user_notice_interactions (user_id, notice_id, viewed, viewed_at)
        VALUES (p_user_id, p_notice_id, true, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, notice_id)
        DO UPDATE SET viewed = true, viewed_at = COALESCE(user_notice_interactions.viewed_at, CURRENT_TIMESTAMP);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ===================================
-- 5. INITIAL DATA
-- ===================================
INSERT INTO colleges (id, name, icon, color, url, apify_task_id, display_order) VALUES
    ('main','메인 공지사항','🏫','#003876','https://www.yonsei.ac.kr','VsNDqFr5fLLIi2Xh1',0),
    ('liberal','문과대학','📚','#8B4513','https://liberal.yonsei.ac.kr','L5AS9TZWUMorttUJJ',1),
    ('business','상경대학','📊','#FFB700','https://soe.yonsei.ac.kr','yJ8Rp9AhTSVCw7Yt8',2),
    ('management','경영대학','💼','#1E90FF','https://ysb.yonsei.ac.kr','DjsOsls6pCpaQaKq9',3),
    ('engineering','공과대학','⚙️','#DC143C','https://engineering.yonsei.ac.kr','tdcYhb8OaDnBHI8jJr',4),
    ('life','생명시스템대학','🧬','#228B22','https://sys.yonsei.ac.kr','gOKavS1YNKhNUVsNQ',5),
    ('ai','인공지능융합대학','🤖','#9370DB','https://ai.yonsei.ac.kr','qb6M6hbdm2fnhxfeg',6),
    ('theology','신과대학','✝️','#4B0082','https://theology.yonsei.ac.kr','9akDlFeStRHdeps4t',7),
    ('social','사회과학대학','🏛️','#2E8B57','https://yeri.yonsei.ac.kr/socsci','hNSAPYSS35RscOWWm',8),
    ('music','음악대학','🎵','#FF1493','https://music.yonsei.ac.kr','B3xYzP1Jqo1jVH1Me',9),
    ('human','생활과학대학','🏠','#FF6347','https://che.yonsei.ac.kr','K5kXEuXSyZzY5uwpn',10),
    ('education','교육과학대학','🎓','#4169E1','https://educa.yonsei.ac.kr','9XfmKGnPdDQWZkUjW',11),
    ('underwood','언더우드국제대학','🌏','#FF8C00','https://uic.yonsei.ac.kr','Xz2t1SAdshoLSDslB',12),
    ('global','글로벌인재대학','🌐','#008B8B','https://global.yonsei.ac.kr','BwiB4aHdY2uyP4txl',13),
    ('medicine','의과대학','⚕️','#B22222','https://medicine.yonsei.ac.kr','oAgxPnIMOv2IYhZej',14),
    ('dentistry','치과대학','🦷','#5F9EA0','https://dentistry.yonsei.ac.kr','etPqNCyaZNI4A8sEl',15),
    ('nursing','간호대학','💊','#DB7093','https://nursing.yonsei.ac.kr','I04xneYTZMJ8jAn4r',16),
    ('pharmacy','약학대학','💉','#663399','https://pharmacy.yonsei.ac.kr','gjqRcgjHJr4frQhma',17)
ON CONFLICT (id) DO NOTHING;

-- ===================================
-- 6. VIEWS
-- ===================================

CREATE OR REPLACE VIEW v_active_notices AS
SELECT n.*, c.name AS college_name, c.icon AS college_icon, c.color AS college_color
FROM notices n
JOIN colleges c ON n.college_id = c.id
WHERE n.status = 'active'
  AND (n.deadline_date IS NULL OR n.deadline_date >= CURRENT_DATE);

CREATE OR REPLACE VIEW v_user_notices AS
SELECT DISTINCT n.*, c.name AS college_name, c.icon AS college_icon,
       ui.bookmarked, ui.viewed, ui.clicked
FROM notices n
JOIN colleges c ON n.college_id = c.id
JOIN user_college_subscriptions ucs ON ucs.college_id = c.id
LEFT JOIN user_notice_interactions ui ON ui.notice_id = n.id AND ui.user_id = ucs.user_id
WHERE n.status = 'active';

-- ===================================
-- 8. COMMENTS
-- ===================================
COMMENT ON TABLE users IS '사용자 계정 정보';
COMMENT ON TABLE user_settings IS '사용자별 알림 및 개인화 설정';
COMMENT ON TABLE colleges IS '연세대학교 단과대학 정보';
COMMENT ON TABLE notices IS '크롤링된 공지사항 데이터';
COMMENT ON TABLE user_notice_interactions IS '사용자-공지사항 상호작용 기록';
COMMENT ON TABLE crawl_logs IS 'Apify 크롤링 실행 로그';
COMMENT ON COLUMN users.password_hash IS 'bcrypt 또는 argon2로 해시된 비밀번호';
COMMENT ON COLUMN notices.content_hash IS 'SHA-256 해시로 중복 공지 감지용';
COMMENT ON COLUMN notices.original_id IS '원본 시스템(대학 웹사이트)의 공지 ID';
COMMENT ON COLUMN user_settings.deadline_alert_days IS '마감일 며칠 전에 알림을 받을지 설정';
COMMENT ON COLUMN user_settings.interested_categories IS '관심있는 공지사항 카테고리 목록';
