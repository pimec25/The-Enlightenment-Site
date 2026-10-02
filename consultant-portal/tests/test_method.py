import os
import re
import pytest
from fastapi.testclient import TestClient
import app
from method_analysis import analyze_case, validate_case


def case(**records):
    return {"version": 1, "records": records, "gates": {}}


def opportunity(id, amount, category="Profit", **extra):
    return dict(id=id, name=id, category=category, unit="JMD", period="2026-09",
                quantity_low=str(amount), quantity_high=str(amount), rate_low="1", rate_high="2",
                mechanism="Avoidable actual expense", assumptions="Sustainable reduction", evidence="source.xlsx", **extra)


def benefit(name, **extra):
    b = dict(initiative=name, name=name, category="Profit", unit="JMD", period="September",
             baseline="100", target="80", actual="80", calculation="100-80", evidence="ledger",
             owner="Owner", reviewer="Reviewer", review_date="2026-10-01", finance="Finance",
             amount="20", annualized="240", status="Validated")
    b.update(extra)
    return b


def test_pareto_observed_distribution_and_separate_categories():
    r = analyze_case(case(opportunities=[opportunity("A", 80), opportunity("B", 20), opportunity("C", 900, "Cash")]))
    profit = next(g for g in r["pareto"] if g["category"] == "Profit")
    assert profit["total"] == 100
    assert profit["items_to_80_percent"] == 1
    assert profit["total_items"] == 2
    assert profit["ranked"][0]["cumulative_percent"] == 80
    assert r["opportunities"][0]["high"] == 160


def test_overlap_duplicate_invalid_range_and_incomplete_excluded():
    r = analyze_case(case(opportunities=[opportunity("A", 80, overlap="same"), opportunity("A", 80),
        opportunity("B", -1), {"id": "C", "quantity_low": "1"}]))
    assert not r["pareto"]
    assert len(r["warnings"]) == 4


def test_validation_totals_require_finance_and_deduplicate_shared_claims():
    r = analyze_case(case(benefits=[benefit("A"), benefit("A"), benefit("B", finance=""),
        benefit("C", status="Realized"), benefit("D", overlap="group", allocation="Allocated once"),
        benefit("E", overlap="group", allocation="Allocated once"), benefit("F", category="Cash")]))
    assert next(b for b in r["benefits"] if b["category"] == "Profit")["achieved_to_date"] == 40
    assert next(b for b in r["benefits"] if b["category"] == "Profit")["estimated_annualized"] == 480
    assert next(b for b in r["benefits"] if b["category"] == "Cash")["achieved_to_date"] == 20
    assert len(r["warnings"]) == 4


def test_zero_baseline_and_followup_dates():
    r = analyze_case(case(measures=[dict(name="Scrap", baseline="0", actual="2", target="1", direction="Lower")],
                          charter=[{"handover": "2026-12-15"}]))
    assert r["performance"][0]["percent_change"] is None
    assert r["performance"][0]["target_met"] is False
    assert r["follow_up"][0]["date"] == "2027-01-14"


@pytest.mark.parametrize("records", [{"measures": [{"baseline": "NaN"}]}, {"initiatives": [{"skills": "6"}]},
    {"actions": [{"date": "not-date"}]}, {"charter": [{"client": []}]}, {"charter": [{}] * 201}])
def test_invalid_case_rejected(records):
    with pytest.raises(ValueError):
        validate_case(case(**records))


def test_gate_does_not_accept_blank_records_or_missing_approval():
    d = case(charter=[{}], owners=[{}], measures=[{}], rhythm=[{}])
    d["gates"] = {"0": {"status": "Accepted", "reviewer": "Sponsor", "date": "2026-10-01", "evidence": "minutes"}}
    assert analyze_case(d)["readiness"][0]["status"] == "Needs review"


def test_method_routes_auth_csrf_and_case_roundtrip():
    client = TestClient(app.app)
    for path in ("/consultant/method", "/consultant/method/schema", "/consultant/method.js"):
        assert client.get(path).status_code == 401
    assert client.post("/consultant/method/review", json=case()).status_code == 401
    os.environ["CONSULTANT_EMAIL"] = "test@example.com"
    os.environ["CONSULTANT_PASSWORD_HASH"] = app.hash_password("test", iterations=10000)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/consultant/login").text).group(1)
    client.post("/consultant/login", data={"email": "test@example.com", "password": "test", "csrf_token": token})
    page = client.get("/consultant/method")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    assert client.post("/consultant/method/review", json=case()).status_code == 403
    headers = {"X-CSRF-Token": token}
    response = client.post("/consultant/method/review", json=case(charter=[{"client": "Example"}]), headers=headers)
    assert response.status_code == 200
    assert response.json()["case"]["records"]["charter"][0]["client"] == "Example"
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/consultant/method/review", content=b"x" * (2 * 1024 * 1024 + 1), headers=headers).status_code == 413
    assert client.post("/consultant/method/review", json=case(charter=[{"client": []}]), headers=headers).status_code == 422
