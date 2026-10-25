"""Accounts month by month and the boats CSV."""

from datetime import date

from helpers import login
from sqlalchemy import select

from app import reports
from app.db import SessionLocal
from app.models import BankAccount, BankEntry, Boat, BoatDesign, Contact, Role


def _account(db, name="Bank account"):
    account = BankAccount(name=name, kind="bank", opening_balance_minor=100000)
    db.add(account)
    db.flush()
    for day, amount, key in ((date(2026, 1, 5), 5000, "a"), (date(2026, 1, 20), -2000, "b"), (date(2026, 3, 2), 7500, "c"), (date(2026, 3, 9), -500, "d")):
        db.add(BankEntry(account_id=account.id, entry_date=day, details=f"line {key}", reference="", amount_minor=amount, status="unmatched", fingerprint=f"f{key}", source="t"))
    db.commit()
    return account


def test_each_month_starts_where_the_last_one_ended() -> None:
    with SessionLocal() as db:
        _account(db)
        (year,) = reports.account_months(db, date(2026, 1, 1), date(2026, 4, 30))
        rows = [(m.month.strftime("%b"), m.opening, m.money_in, m.money_out, m.closing) for m in year.months]
        assert rows == [("Jan", 100000, 5000, 2000, 103000), ("Feb", 103000, 0, 0, 103000), ("Mar", 103000, 7500, 500, 110000), ("Apr", 110000, 0, 0, 110000)]
        # a period that starts later opens with everything before it
        (later,) = reports.account_months(db, date(2026, 3, 1), date(2026, 3, 31))
        assert later.months[0].opening == 103000 and later.months[0].closing == 110000


def test_the_accounts_report_page_and_csv() -> None:
    with SessionLocal() as db:
        _account(db)
    treasurer = login("t@example.org", Role.COMMITTEE, hats="treasurer")
    page = treasurer.get("/app/finance/reports/accounts?start=2026-01-01&end=2026-03-31")
    assert page.status_code == 200 and "Accounts month by month" in page.text and "January 2026" in page.text and "£1,030.00" in page.text and "£1,100.00" in page.text
    csv = treasurer.get("/app/finance/reports/accounts.csv?start=2026-01-01&end=2026-03-31").text.splitlines()
    assert csv[0] == "account,month,opening,in,out,closing" and csv[1] == "Bank account,2026-01,1000.00,50.00,20.00,1030.00" and len(csv) == 4
    assert treasurer.get("/app/finance/reports/accounts?start=garbage&end=2026-01-31").status_code == 200
    assert login("m@example.org", Role.MEMBER).get("/app/finance/reports/accounts").status_code in (403, 404)


def test_boats_export_follows_the_filter() -> None:
    with SessionLocal() as db:
        design = BoatDesign(name="Laser", portsmouth_rating=1100)
        db.add(design)
        owner = Contact(name="Ann Sailor", email="ann@example.org")
        db.add(owner)
        db.flush()
        db.add_all([Boat(name="Zip", sail_number="1001", design_id=design.id, owner_contact_id=owner.id, status="active"), Boat(name="Old", sail_number="9", status="archived")])
        db.commit()
        assert db.scalar(select(Boat.id).where(Boat.name == "Zip"))
    bosun = login("b@example.org", Role.COMMITTEE, hats="bosun")
    lines = bosun.get("/app/boats.csv").text.splitlines()
    assert lines[0].startswith("boat,sail_number,design,class,owner,owner_email") and "Zip,1001,Laser" in lines[1] and "Ann Sailor,ann@example.org" in lines[1] and "1100" in lines[1] and len(lines) == 2
    assert "Old" in bosun.get("/app/boats.csv?filter=archived").text and "Zip" not in bosun.get("/app/boats.csv?filter=archived").text
    assert "Zip" in bosun.get("/app/boats.csv?q=zip").text and "Zip" not in bosun.get("/app/boats.csv?q=nomatch").text
    assert "Export CSV" in bosun.get("/app/boats").text
