CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    member_id INTEGER,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(80),
    address TEXT,
    birth_date DATE,
    notes TEXT NOT NULL DEFAULT '',
    suppressed BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS contact_lists (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    list_type VARCHAR(40) NOT NULL DEFAULT 'static'
);

CREATE TABLE IF NOT EXISTS list_memberships (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL,
    list_id INTEGER NOT NULL,
    starts_on DATE,
    ends_on DATE,
    FOREIGN KEY(contact_id) REFERENCES contacts(id),
    FOREIGN KEY(list_id) REFERENCES contact_lists(id),
    UNIQUE(contact_id, list_id)
);

CREATE TABLE IF NOT EXISTS contact_relations (
    id INTEGER PRIMARY KEY,
    from_contact_id INTEGER NOT NULL,
    to_contact_id INTEGER NOT NULL,
    relation_type VARCHAR(80) NOT NULL,
    starts_on DATE,
    ends_on DATE,
    FOREIGN KEY(from_contact_id) REFERENCES contacts(id),
    FOREIGN KEY(to_contact_id) REFERENCES contacts(id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    owner_contact_id INTEGER NOT NULL,
    filename VARCHAR(255) NOT NULL,
    media_type VARCHAR(120) NOT NULL DEFAULT 'application/octet-stream',
    uri TEXT NOT NULL,
    metadata JSON,
    FOREIGN KEY(owner_contact_id) REFERENCES contacts(id)
);

CREATE TABLE IF NOT EXISTS participation_records (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL,
    event_id INTEGER,
    participation_type VARCHAR(80) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(contact_id) REFERENCES contacts(id),
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS membership_periods (
    id INTEGER PRIMARY KEY,
    name VARCHAR(80) NOT NULL UNIQUE,
    starts_on DATE NOT NULL,
    ends_on DATE NOT NULL,
    active BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS membership_renewals (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL,
    period_id INTEGER NOT NULL,
    state VARCHAR(40) NOT NULL DEFAULT 'draft',
    lock_flag BOOLEAN NOT NULL DEFAULT 0,
    suggestion_notes TEXT NOT NULL DEFAULT '',
    confirmed_at TIMESTAMP,
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(period_id) REFERENCES membership_periods(id)
);

CREATE TABLE IF NOT EXISTS membership_charges (
    id INTEGER PRIMARY KEY,
    renewal_id INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL,
    invoice_id INTEGER,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',
    FOREIGN KEY(renewal_id) REFERENCES membership_renewals(id),
    FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);

CREATE TABLE IF NOT EXISTS event_resources (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS event_documents (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    uri TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS event_noticeboard (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP,
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS event_tickets (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'booked',
    FOREIGN KEY(event_id) REFERENCES events(id),
    FOREIGN KEY(contact_id) REFERENCES contacts(id)
);

CREATE TABLE IF NOT EXISTS event_wrapups (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL UNIQUE,
    tally_notes TEXT NOT NULL DEFAULT '',
    result_summary TEXT NOT NULL DEFAULT '',
    actions TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS boat_designs (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    manufacturer VARCHAR(255) NOT NULL DEFAULT '',
    rig_type VARCHAR(120) NOT NULL DEFAULT '',
    portsmouth_rating INTEGER
);

ALTER TABLE boats ADD COLUMN design_id INTEGER REFERENCES boat_designs(id);
ALTER TABLE boats ADD COLUMN variant VARCHAR(120) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS boat_crew_links (
    id INTEGER PRIMARY KEY,
    boat_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    role VARCHAR(80) NOT NULL DEFAULT 'crew',
    starts_on DATE,
    ends_on DATE,
    FOREIGN KEY(boat_id) REFERENCES boats(id),
    FOREIGN KEY(contact_id) REFERENCES contacts(id)
);

ALTER TABLE race_results ADD COLUMN result_status VARCHAR(20) NOT NULL DEFAULT 'FIN';
ALTER TABLE race_results ADD COLUMN points INTEGER;
ALTER TABLE race_results ADD COLUMN rank INTEGER;

CREATE TABLE IF NOT EXISTS invoice_lines (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    source_type VARCHAR(80) NOT NULL,
    source_id VARCHAR(80) NOT NULL,
    description TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);

CREATE TABLE IF NOT EXISTS payment_transactions (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL,
    payment_method VARCHAR(80) NOT NULL DEFAULT 'bank',
    bank_reference VARCHAR(120) NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'received',
    received_at TIMESTAMP,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);

CREATE TABLE IF NOT EXISTS communication_deliveries (
    id INTEGER PRIMARY KEY,
    communication_id INTEGER NOT NULL,
    contact_id INTEGER,
    destination VARCHAR(255) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'queued',
    issue TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(communication_id) REFERENCES communications(id),
    FOREIGN KEY(contact_id) REFERENCES contacts(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'new',
    description TEXT NOT NULL DEFAULT '',
    invoice_id INTEGER,
    created_at TIMESTAMP,
    FOREIGN KEY(contact_id) REFERENCES contacts(id),
    FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);
