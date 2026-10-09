"""What a browser does with the pages: Back, Refresh, a second click, an expired session, a wrong address.

Each of these was found by using the application, not by reading it. Made-up people and passwords.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Application
from tests.test_backend_access import PASSWORD, SCIENCE, _account, _sign_in, _two_schools, web  # noqa: F401
from tests.test_backend_foundation import _docx, client, engine  # noqa: F401
from tests.test_backend_intake import DOCX, _create_opening_via_page, _data, _storage  # noqa: F401


def _applications(engine) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(Application))


# --- nothing is left behind in the browser -----------------------------------


def test_no_page_may_be_kept_by_the_browser_so_back_after_signing_out_shows_nothing(web, engine, tmp_path):
    opening_id, app_id = _two_schools(engine, tmp_path)["science"]
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    _sign_in(web, admin)
    for address in (f"/hr/applications/{app_id}", f"/hr/applications/{app_id}/resume", f"/hr/openings/{opening_id}", "/hr/dashboard",
                    "/account/password", "/apply", "/login"):
        headers = web.get(address, follow_redirects=False).headers
        assert headers["cache-control"] == "no-store", address
        assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff", address
    assert "no-store" not in web.get("/static/app.css").headers.get("cache-control", "")  # the stylesheet holds no one's data
    # A page shown to someone signed in asks for itself again if the browser brings it back from memory (Back twice
    # reaches pages that are restored without asking the server at all); a public page has no need to.
    assert "e.persisted" in web.get("/hr/dashboard").text and "e.persisted" not in web.get("/apply").text
    out = web.post("/logout", follow_redirects=False)
    assert out.headers["clear-site-data"] == '"cache"'  # and signing out tells the browser to drop what it holds
    # What Back now does: the browser has no copy, asks again, and is sent to sign in.
    again = web.get(f"/hr/applications/{app_id}", follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"].startswith("/login") and again.headers["cache-control"] == "no-store"


def test_someone_already_signed_in_is_not_shown_the_sign_in_form(web, engine):
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    _sign_in(web, admin)
    assert web.get("/login", follow_redirects=False).headers["location"] == "/"
    assert web.get("/login?next=/hr/inbox", follow_redirects=False).headers["location"] == "/hr/inbox"
    assert web.get("/login?next=https://example.org", follow_redirects=False).headers["location"] == "/"


# --- a refused form can be refreshed and gone back to -------------------------


def test_a_wrong_password_is_answered_on_the_sign_in_pages_own_address_and_only_once(web, engine):
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    sent = web.post("/login", data={"email": admin.email, "password": "wrong-password-0", "next": "/hr/inbox"}, follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"] == "/login?next=%2Fhr%2Finbox"  # not a page drawn from the form itself
    assert "The email address" not in web.get("/forgot").text  # another page does not pick up what was left for this one
    shown = web.get(sent.headers["location"]).text
    assert "The email address or the password is not right." in shown and f'value="{admin.email}"' in shown
    assert "wrong-password-0" not in shown and 'name="next" value="/hr/inbox"' in shown  # the password is never sent back
    refreshed = web.get(sent.headers["location"]).text
    assert "not right" not in refreshed and f'value="{admin.email}"' not in refreshed  # shown once; a refresh is a clean form


def test_a_refused_opening_form_keeps_what_was_typed_and_a_refresh_starts_clean(client):
    bad = client.post("/hr/openings", data={"school_id": "SCH-008", "designation": "ASSISTANT_PROFESSOR", "discipline_group": "GENERAL",
                                            "title": "Kept title", "closing_date": "not-a-date"}, follow_redirects=False)
    assert bad.status_code == 303 and bad.headers["location"] == "/hr/openings/new"
    shown = client.get("/hr/openings/new").text
    assert "Enter a valid date." in shown and 'value="Kept title"' in shown
    assert "Enter a valid date." not in client.get("/hr/openings/new").text


def test_refreshing_after_an_upload_does_not_upload_again(client, engine):
    opening_id = _create_opening_via_page(client)
    sent = client.post(f"/hr/openings/{opening_id}/upload", files=[("resumes", ("cv.docx", _docx(), DOCX)), ("resumes", ("notes.txt", b"x", "text/plain"))],
                       follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"] == f"/hr/openings/{opening_id}"
    page = client.get(f"/hr/openings/{opening_id}").text
    assert "1 of 2 file(s) added." in page and "cv.docx:" in page and "added as APP-000001" in page and "not added" in page
    again = client.get(f"/hr/openings/{opening_id}").text  # the refresh
    assert "file(s) added" not in again and _applications(engine) == 1


def test_an_applicant_can_refresh_the_received_page_without_applying_twice(client, engine):
    opening_id = _create_opening_via_page(client)
    sent = client.post(f"/apply/{opening_id}", data=_data(), files={"resume": ("cv.docx", _docx(), DOCX)}, follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"].startswith(f"/apply/{opening_id}/received/")
    for _ in range(2):  # the page, and the page refreshed
        page = client.get(sent.headers["location"])
        assert page.status_code == 200 and "Application received" in page.text and "APP-000001" in page.text
    assert _applications(engine) == 1
    assert "sample.exampleton" not in page.text.lower() and "Sample Exampleton" not in page.text  # the reference, and no more
    key = sent.headers["location"].rsplit("/", 1)[1]
    assert len(key) == 24 and client.get(f"/apply/{opening_id}/received/{key[:-1]}x").status_code == 404
    assert client.get(f"/apply/{opening_id}/received/{key[:5]}").status_code == 404  # a guess at the start of one finds nothing

    refused = client.post(f"/apply/{opening_id}", data=_data(email="nope", full_name="Typed Name"), files={"resume": ("cv2.docx", _docx() + b"2", DOCX)},
                          follow_redirects=False)
    assert refused.status_code == 303 and refused.headers["location"] == f"/apply/{opening_id}"
    form = client.get(f"/apply/{opening_id}").text
    assert 'value="Typed Name"' in form and "Nothing has been submitted yet" in form
    assert 'value="Typed Name"' not in client.get(f"/apply/{opening_id}").text


# --- a session that ended while a form was open --------------------------------


def test_a_form_sent_after_the_session_ended_leads_back_to_its_page_after_signing_in(web, engine, tmp_path):
    opening_id, _ = _two_schools(engine, tmp_path)["science"]
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    page = f"/hr/openings/{opening_id}"
    sent = web.post(f"{page}/close", headers={"referer": f"http://testserver{page}?x=1"}, follow_redirects=False)
    assert sent.headers["location"] == "/login?expired=1&next=" + f"{page}?x=1".replace("/", "%2F").replace("?", "%3F").replace("=", "%3D")
    assert "You had been signed out, so that was not saved." in web.get(sent.headers["location"]).text
    elsewhere = web.post(f"{page}/close", headers={"referer": "https://example.org/hr/openings/1"}, follow_redirects=False)
    assert elsewhere.headers["location"] == "/login?expired=1&next=%2F"  # never sent off to another site's address
    r = web.post("/login", data={"email": admin.email, "password": PASSWORD, "next": f"{page}?x=1"}, follow_redirects=False)
    assert r.headers["location"] == f"{page}?x=1"
    assert "Close this opening" in web.get(page).text  # and nothing had been done by the form that was refused


# --- a wrong address is answered with a page, not with code ---------------------


def test_errors_are_pages_a_person_can_read_and_the_api_still_answers_in_json(client):
    for address, status, words in (("/hr/openings/999999", 404, "Page not found"), ("/no-such-page", 404, "Page not found"),
                                   ("/hr/openings/abc", 422, "That could not be read"), ("/logout", 405, "cannot be opened directly"),
                                   ("/apply/999999", 404, "Page not found")):
        r = client.get(address)
        assert r.status_code == status and words in r.text and r.headers["content-type"].startswith("text/html"), address
        assert "<h1>" in r.text and 'href="/' in r.text and "detail" not in r.text  # a way back, and no raw code
    api = client.get("/applications/999999")
    assert api.status_code == 404 and api.json() == {"detail": "application not found"}
    assert client.post("/applications", data={}).headers["content-type"].startswith("application/json")
    for address in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(address).status_code == 404, address  # the API is not listed to whoever asks


def test_a_view_only_account_is_not_shown_a_form_it_cannot_save(web, engine, tmp_path):
    opening_id, _ = _two_schools(engine, tmp_path)["science"]
    with Session(engine) as s:
        viewer = _account(s, "Vani Viewer", "SCHOOL_HR", [(SCIENCE, None, "VIEW")])
        editor = _account(s, "Esha Editor", "SCHOOL_HR", [(SCIENCE, None, "VIEW_EDIT")])
    _sign_in(web, viewer)
    assert web.get(f"/hr/openings/{opening_id}/edit").status_code == 403 and web.get(f"/hr/openings/{opening_id}").status_code == 200
    _sign_in(web, editor)
    assert web.get(f"/hr/openings/{opening_id}/edit").status_code == 200


def test_every_page_carries_the_send_once_guard_and_drops_a_shown_message_from_its_address(client):
    page = client.get("/login").text
    assert "form.dataset.sent" in page and 'searchParams.delete' in page and 'rel="icon"' in page


def test_the_sign_in_page_says_only_its_own_messages_never_words_from_the_address(web, engine):
    with Session(engine) as s:
        _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    assert "You are signed out." in web.get("/login?said=out").text
    made_up = web.get("/login?msg=Your+password+has+expired.+Call+0000&said=Call+0000").text
    assert "Call 0000" not in made_up and "has expired" not in made_up


def test_each_change_to_an_account_is_its_own_form_so_enter_does_what_the_box_says(web, engine):
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
        _account(s, "Hari HR", "HR_ADMIN")
    _sign_in(web, admin)
    page = web.get("/admin/users").text
    # One hidden "do" per form, and no button that carries the action: nothing depends on which button Enter picks.
    assert page.count('name="do" value="password"') == 2 and 'button type="submit" name="do"' not in page
