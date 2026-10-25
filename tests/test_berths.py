"""The boat park through the year: keeping a space, rolling forward, how full it is, and the waiting list."""

from datetime import date

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import berths, boats
from app.db import SessionLocal
from app.models import Boat, Contact, Member, Role, SpaceRequest, StorageAllocation


@pytest.fixture
def park():
    year = date.today().year
    with SessionLocal() as db:
        people = {}
        for name in ("Ann Sailor", "Bob Rower", "Cara Waiting"):
            member = Member(name=name, email=f"{name.split()[0].lower()}@example.org")  # members may sign in; a bare contact may not
            db.add(member)
            db.flush()
            contact = Contact(name=name, email=member.email, member_id=member.id)
            db.add(contact)
            db.flush()
            people[name.split()[0].lower()] = contact.id
        spaces = [boats.create_resource(db, f"Row A {n}", group="Hard standing") for n in range(1, 5)]
        ann_boat, bob_boat, cara_boat = (Boat(name=n, owner_contact_id=people[o]) for n, o in (("Wavelet", "ann"), ("Gust", "bob"), ("Tern", "cara")))
        db.add_all([ann_boat, bob_boat, cara_boat])
        db.flush()
        a = boats.allocate(db, ann_boat, spaces[0], date(year - 1, 4, 1), date(year, 12, 31))
        b = boats.allocate(db, bob_boat, spaces[1], date(year - 1, 4, 1), date(year, 12, 31))
        db.commit()
        return {"year": year, "people": people, "alloc": {"ann": a.id, "bob": b.id}, "boats": {"ann": ann_boat.id, "bob": bob_boat.id, "cara": cara_boat.id}, "spaces": [s.id for s in spaces]}


def test_season_rows_show_who_has_replied(park) -> None:
    year = park["year"]
    with SessionLocal() as db:
        assert {r.boat.name: r.state for r in berths.season_rows(db, year)} == {"Wavelet": "no reply", "Gust": "no reply"}
        berths.keep(db, db.get(StorageAllocation, park["alloc"]["ann"]), year + 1)
        berths.leave(db, db.get(StorageAllocation, park["alloc"]["bob"]), year + 1)
        db.commit()
        assert {r.boat.name: r.state for r in berths.season_rows(db, year)} == {"Wavelet": "keeping", "Gust": "leaving"}
        berths.keep(db, db.get(StorageAllocation, park["alloc"]["bob"]), year + 1)  # changing their mind clears "leaving"
        assert {r.boat.name: r.state for r in berths.season_rows(db, year)}["Gust"] == "keeping"


def test_rolling_over_extends_everyone_but_those_leaving_and_is_safe_to_repeat(park) -> None:
    year = park["year"]
    with SessionLocal() as db:
        berths.leave(db, db.get(StorageAllocation, park["alloc"]["bob"]), year + 1)
        assert berths.roll_over(db, year) == {"extended": 1, "leaving": 1}
        assert db.get(StorageAllocation, park["alloc"]["ann"]).end_date == date(year + 1, 12, 31)
        assert db.get(StorageAllocation, park["alloc"]["bob"]).end_date == date(year, 12, 31)
        assert berths.roll_over(db, year) == {"extended": 0, "leaving": 1}  # Ann is already carried; Bob is still leaving
        assert db.get(StorageAllocation, park["alloc"]["ann"]).confirmed_year is None  # she is asked again next autumn


def test_occupancy_by_group_counts_free_spaces(park) -> None:
    with SessionLocal() as db:
        [group] = berths.occupancy_by_group(db, date.today())
        assert (group.group, group.capacity, group.used, group.free) == ("Hard standing", 4, 2, 2) and group.free_names == ["Row A 3", "Row A 4"]


def test_the_waiting_list_is_first_come_first_served_and_one_place_each(park) -> None:
    with SessionLocal() as db:
        cara, ann = db.get(Contact, park["people"]["cara"]), db.get(Contact, park["people"]["ann"])
        first = berths.join_waiting_list(db, cara, boat=db.get(Boat, park["boats"]["cara"]))
        berths.join_waiting_list(db, ann)
        with pytest.raises(boats.BoatError, match="already on the waiting list"):
            berths.join_waiting_list(db, cara)
        db.commit()
        assert [(c.name, place) for _, c, _, place in berths.waiting_list(db)] == [("Cara Waiting", 1), ("Ann Sailor", 2)]
        assert berths.next_in_queue(db).id == first.id


def test_offering_a_space_allocates_the_boat_and_closes_the_request(park) -> None:
    year = park["year"]
    with SessionLocal() as db:
        request = berths.join_waiting_list(db, db.get(Contact, park["people"]["cara"]), boat=db.get(Boat, park["boats"]["cara"]))
        with pytest.raises(boats.BoatError, match="does not belong"):
            berths.offer(db, request, db.get(boats.StorageResource, park["spaces"][2]), db.get(Boat, park["boats"]["bob"]), date.today(), date(year, 12, 31))
        allocation = berths.offer(db, request, db.get(boats.StorageResource, park["spaces"][2]), db.get(Boat, park["boats"]["cara"]), date.today(), date(year, 12, 31))
        assert request.status == "allocated" and allocation.resource_id == park["spaces"][2]
        with pytest.raises(boats.BoatError, match="already closed"):
            berths.withdraw(db, request)


def test_staff_screens_roll_the_park_forward_and_run_the_waiting_list(park) -> None:
    year = park["year"]
    bosun = login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    page = bosun.get(f"/app/storage/season?year={year}")
    assert page.status_code == 200 and "No reply yet" in page.text and "Wavelet" in page.text and "Row A 3" in page.text
    token = csrf(bosun, f"/app/storage/season?year={year}")
    assert "Tick the box to confirm" in bosun.post("/app/storage/season/roll", data={"csrf": token, "year": year}, follow_redirects=True).text
    bosun.post(f"/app/storage/season/{park['alloc']['bob']}/leave", data={"csrf": token, "year": year}, follow_redirects=True)
    rolled = bosun.post("/app/storage/season/roll", data={"csrf": token, "year": year, "confirm": "1"}, follow_redirects=True)
    assert "1 space(s) carried" in rolled.text
    added = bosun.post("/app/storage/waiting", data={"csrf": token, "contact_id": park["people"]["cara"], "resource_type": "berth"}, follow_redirects=True)
    assert "Cara Waiting is on the waiting list" in added.text
    with SessionLocal() as db:
        request_id = db.scalar(select(SpaceRequest.id))
    assert "needs a boat" in bosun.get("/app/storage/waiting").text
    with SessionLocal() as db:
        db.get(SpaceRequest, request_id).boat_id = park["boats"]["cara"]
        db.commit()
    offered = bosun.post(f"/app/storage/waiting/{request_id}/offer", data={"csrf": token, "boat_id": park["boats"]["cara"], "resource_id": park["spaces"][2]}, follow_redirects=True)
    assert "Tern now has Row A 3" in offered.text and "Nobody is waiting" in offered.text


def test_a_member_keeps_or_gives_up_their_space_and_can_join_the_waiting_list(park) -> None:
    ann = login("ann@example.org")
    page = ann.get("/app/me/boats")
    assert "Will you keep" in page.text and "Row A 1" in page.text
    token = csrf(ann, "/app/me/boats")
    kept = ann.post(f"/app/me/boats/{park['boats']['ann']}/space/keep", data={"csrf": token}, follow_redirects=True)
    assert "we have noted you are keeping the space for Wavelet" in kept.text
    with SessionLocal() as db:
        assert db.get(StorageAllocation, park["alloc"]["ann"]).confirmed_year == date.today().year + 1
    cara = login("cara@example.org")
    assert "Join the waiting list" in cara.get("/app/me/boats").text
    joined = cara.post("/app/me/waiting", data={"csrf": csrf(cara, "/app/me/boats"), "boat_id": park["boats"]["cara"]}, follow_redirects=True)
    assert "You are on the waiting list" in joined.text
    again = cara.post("/app/me/waiting", data={"csrf": csrf(cara, "/app/me/boats")}, follow_redirects=True)
    assert "already on the waiting list" in again.text
    assert ann.post(f"/app/me/boats/{park['boats']['bob']}/space/keep", data={"csrf": token}).status_code == 404  # someone else's boat
