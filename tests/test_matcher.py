import os

os.environ["API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite:///./test_sponcheck.db"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.matcher import code_text, get_codeframe

CF = get_codeframe("parcel_carriers_uk")


def codes(text):
    return [(r.verbatim, r.code) for r in code_text(text, CF)]


@pytest.mark.parametrize("text,expected", [
    ("DPD, Royal Mail, Evri, Yodel", [("DPD", 1), ("Royal Mail", 3), ("Evri", 4), ("Yodel", None)]),
    ("dpd royal mail evri and ups", [("dpd", 1), ("royal mail", 3), ("evri", 4), ("ups", 7)]),
    ("DPD Local", [("DPD Local", 2)]),
    ("dpd lcoal", [("dpd lcoal", 2)]),
    ("dpd royal", [("dpd", 1), ("royal", None)]),
    ("Royal Mial", [("Royal Mial", 3)]),
    ("royalmail", [("royalmail", 3)]),
    ("Parcel Froce", [("Parcel Froce", 9)]),
    ("hermes", [("hermes", None)]),
    ("dhl parcel", [("dhl", 5), ("parcel", None)]),
    ("DLH", [("DLH", 5)]),
    ("DHL Express\nDHL ecommerce", [("DHL Express", 6), ("DHL ecommerce", 5)]),
    ("DHL E-commerce", [("DHL E-commerce", 5)]),
    ("dhl expres", [("dhl expres", 6)]),
    ("dhl", [("dhl", 5)]),
    ("FedEx-TNT", [("FedEx-TNT", 8)]),
    ("fedex", [("fedex", 8)]),
    ("Amazon", [("Amazon", 11)]),
    ("in post", [("in post", 10)]),
    ("Inpsot", [("Inpsot", 10)]),
    ("dont know", [("dont know", None)]),
    ("none", [("none", None)]),
    ("Post Office", [("Post Office", None)]),
    ("every", [("every", None)]),
    ("lived", [("lived", None)]),
    ("dp", [("dp", None)]),
    ("dx apc", [("dx", 14), ("apc", 15)]),
    ("1. DPD 2. Evri", [("DPD", 1), ("Evri", 4)]),
    ("I think dpd and maybe evri", [("dpd", 1), ("evri", 4)]),
    ("", []),
])
def test_matching(text, expected):
    assert codes(text) == expected


client = TestClient(app)
client.__enter__()


def test_api_requires_key():
    r = client.post("/code", json={"project": "P1", "respid": "1", "text": "dpd"})
    assert r.status_code == 401


def test_api_code_and_log():
    r = client.post(
        "/code",
        headers={"X-API-Key": "test-key"},
        json={"project": "P1", "respid": "R1", "text": "Royal Mail, dpd, gillette, DPD"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["codes"] == [3, 1]
    assert body["codes_string"] == "3,1"
    assert body["unmatched_count"] == 1
    u = client.get("/unmatched?project=P1", headers={"X-API-Key": "test-key"}).json()
    assert any(row["verbatim"] == "gillette" for row in u["unmatched"])


def test_api_answers_list():
    r = client.post(
        "/code",
        headers={"X-API-Key": "test-key"},
        json={"project": "P1", "respid": "R2", "answers": ["evri", "parcelforce"]},
    )
    assert r.json()["codes"] == [4, 9]
