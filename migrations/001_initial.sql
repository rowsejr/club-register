CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    membership_type VARCHAR(50) NOT NULL DEFAULT 'standard',
    renewal_status VARCHAR(50) NOT NULL DEFAULT 'active',
    payment_status VARCHAR(50) NOT NULL DEFAULT 'paid',
    join_date DATE,
    expiry_date DATE,
    active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL,
    plan_name VARCHAR(80) NOT NULL,
    starts_on DATE NOT NULL,
    ends_on DATE NOT NULL,
    status VARCHAR(40) NOT NULL,
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    start_at TIMESTAMP NOT NULL,
    location VARCHAR(255) NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    published BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS boats (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    owner_member_id INTEGER NOT NULL,
    boat_class VARCHAR(120) NOT NULL DEFAULT '',
    sail_number VARCHAR(120) NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'active',
    FOREIGN KEY(owner_member_id) REFERENCES members(id),
    UNIQUE(owner_member_id, name, sail_number)
);

CREATE TABLE IF NOT EXISTS storage_resources (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    resource_type VARCHAR(80) NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_allocations (
    id INTEGER PRIMARY KEY,
    boat_id INTEGER NOT NULL,
    resource_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    seasonal BOOLEAN NOT NULL DEFAULT 1,
    billing_flag VARCHAR(80) NOT NULL DEFAULT 'standard',
    FOREIGN KEY(boat_id) REFERENCES boats(id),
    FOREIGN KEY(resource_id) REFERENCES storage_resources(id)
);

CREATE TABLE IF NOT EXISTS duties (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    role VARCHAR(120) NOT NULL,
    member_id INTEGER,
    status VARCHAR(40) NOT NULL DEFAULT 'open',
    start_at TIMESTAMP NOT NULL,
    end_at TIMESTAMP NOT NULL,
    FOREIGN KEY(event_id) REFERENCES events(id),
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS race_series (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    season INTEGER NOT NULL,
    handicap_system VARCHAR(20) NOT NULL DEFAULT 'PY'
);

CREATE TABLE IF NOT EXISTS race_results (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    boat_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    elapsed_seconds INTEGER NOT NULL,
    corrected_seconds INTEGER NOT NULL,
    FOREIGN KEY(series_id) REFERENCES race_series(id),
    FOREIGN KEY(event_id) REFERENCES events(id),
    FOREIGN KEY(boat_id) REFERENCES boats(id),
    UNIQUE(series_id, event_id, boat_id)
);

CREATE TABLE IF NOT EXISTS training_courses (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    starts_at TIMESTAMP NOT NULL,
    ends_at TIMESTAMP NOT NULL,
    capacity INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS training_bookings (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL,
    FOREIGN KEY(course_id) REFERENCES training_courses(id),
    FOREIGN KEY(member_id) REFERENCES members(id),
    UNIQUE(course_id, member_id)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL,
    reference VARCHAR(120) NOT NULL UNIQUE,
    amount_minor INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'GBP',
    status VARCHAR(40) NOT NULL DEFAULT 'draft',
    due_date DATE,
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS communications (
    id INTEGER PRIMARY KEY,
    channel VARCHAR(40) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    provider VARCHAR(80) NOT NULL DEFAULT 'sandbox',
    status VARCHAR(40) NOT NULL DEFAULT 'queued',
    metadata JSON
);

CREATE TABLE IF NOT EXISTS auth_magic_links (
    id INTEGER PRIMARY KEY,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    consumed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY,
    actor_email VARCHAR(255) NOT NULL,
    actor_role VARCHAR(40) NOT NULL,
    action VARCHAR(80) NOT NULL,
    target_type VARCHAR(80) NOT NULL,
    target_id VARCHAR(80) NOT NULL,
    details JSON,
    created_at TIMESTAMP
);
