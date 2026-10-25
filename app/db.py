from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./club.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, future=True, connect_args=connect_args)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer; safe for a small club's load
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def run_sql_migrations() -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migrations")}
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            if sql_file.name in applied:
                continue
            sql_text = sql_file.read_text(encoding="utf-8")
            if sql_text.strip():
                if engine.dialect.name == "sqlite":
                    conn.connection.executescript(sql_text)
                else:
                    for statement in [part.strip() for part in sql_text.split(";") if part.strip()]:
                        conn.exec_driver_sql(statement)
            conn.exec_driver_sql(
                "INSERT INTO schema_migrations(version) VALUES (:version)",
                {"version": sql_file.name},
            )


# Columns that were NOT NULL in the original SQL migrations but are nullable in the models.
RELAXED_COLUMNS = [("invoices", "member_id"), ("boats", "owner_member_id"), ("members", "email"), ("training_bookings", "member_id")]


def ensure_schema() -> None:
    """Bring databases created by the older SQL migrations up to the current models.

    Adds missing (nullable) columns and relaxes NOT NULL where the model allows NULL.
    Safe to run repeatedly.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    ddl = col.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                    default = getattr(col.default, "arg", None) if col.default is not None else None
                    if isinstance(default, (int, str, bool)):  # existing rows get the model's scalar default
                        conn.execute(text(f'UPDATE "{table.name}" SET "{col.name}" = :v'), {"v": default})  # noqa: S608 - identifiers come from our own models
        for table_name, column in RELAXED_COLUMNS:
            info = {c["name"]: c for c in inspect(conn).get_columns(table_name)}
            if column in info and not info[column]["nullable"]:
                if engine.dialect.name == "sqlite":
                    _rebuild_sqlite_table(conn, table_name)
                else:
                    conn.execute(text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column}" DROP NOT NULL'))


def _rebuild_sqlite_table(conn, table_name: str) -> None:
    """SQLite cannot drop NOT NULL in place: copy into a table built from the model, then swap."""
    model_table = Base.metadata.tables[table_name]
    temp_name = f"{table_name}__new"
    temp = model_table.to_metadata(Base.metadata, name=temp_name)  # needs the referenced tables in scope
    try:
        temp.create(conn)
    finally:
        Base.metadata.remove(temp)
    old_cols = {c["name"] for c in inspect(conn).get_columns(table_name)}
    shared = ", ".join(f'"{c.name}"' for c in model_table.columns if c.name in old_cols)
    conn.execute(text(f'INSERT INTO "{temp_name}" ({shared}) SELECT {shared} FROM "{table_name}"'))  # noqa: S608 - identifiers come from our own models
    conn.execute(text(f'DROP TABLE "{table_name}"'))
    conn.execute(text(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"'))


INDEXES = [
    ("ix_invoices_contact", "invoices", "contact_id"),
    ("ix_invoices_member", "invoices", "member_id"),
    ("ix_invoices_status_due", "invoices", "status, due_date"),
    ("ix_invoice_lines_invoice", "invoice_lines", "invoice_id"),
    ("ix_payments_invoice", "payment_transactions", "invoice_id"),
    ("ix_payments_bank_entry", "payment_transactions", "bank_entry_id"),
    ("ix_bank_entries_account_status", "bank_entries", "account_id, status, entry_date"),
    ("ix_memberships_member", "memberships", "member_id, starts_on"),
    ("ix_contacts_member", "contacts", "member_id"),
    ("ix_contacts_email", "contacts", "email"),
    ("ix_members_email", "members", "email"),
    ("ix_audit_created", "audit_logs", "created_at"),
    ("ix_boats_owner_contact", "boats", "owner_contact_id"),
    ("ix_outbound_related", "outbound_emails", "related_type, related_id"),
]


def ensure_indexes() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for name, table, columns in INDEXES:
            if insp.has_table(table):
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))  # noqa: S608 - constants above


def init_db() -> None:
    run_sql_migrations()
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    ensure_indexes()
    from app import seed  # imported here: the seed code needs the models and sessions defined above

    with SessionLocal() as db:
        seed.seed(db)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
