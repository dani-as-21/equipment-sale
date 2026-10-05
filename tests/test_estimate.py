import json

from app.estimate import parse_labx_listings


LABX = """
{id:"listing-9",unique_id:9,doc_type:"listing",title:"Mettler Toledo XP205 Analytical Balance",name:"Mettler Toledo XP205 Analytical Balance",content:"The list price was $99999.",model:"XP205",manufacturer:"METTLER TOLEDO",condition:"Used",price:2500,currency:"USD",
{id:"listing-8",unique_id:8,doc_type:"listing",title:"Other device ZZ-9",name:"Other device",content:"",model:"ZZ-9",manufacturer:"Other Maker",condition:"Used",price:999999,currency:"USD",
{id:"listing-7",unique_id:7,doc_type:"listing",title:"Mettler Toledo XP205DR Balance",name:"Mettler Toledo XP205DR Balance",content:"",model:"XP205DR",manufacturer:"METTLER TOLEDO",condition:"Refurbished",price:10,currency:"USD",
"""


def test_labx_parser_keeps_only_priced_matches_for_the_model():
    hits = parse_labx_listings(LABX, "Mettler Toledo", "XP205", "https://example.test/labx")
    assert len(hits) == 1
    assert hits[0]["amount"] == 2500
    assert hits[0]["currency"] == "USD"
    assert hits[0]["url"] == "https://example.test/labx"
    assert hits[0]["kind"] == "asking"
    dumped = json.dumps(hits)
    assert "999999" not in dumped
    assert "99999" not in dumped


def _asset(client, tag):
    found = client.get("/api/assets", params={"q": tag}).json()["assets"]
    return next(asset for asset in found if tag in asset["tags"])


def _quiet_research(monkeypatch, hits=None):
    calls = []

    def research(manufacturer, model):
        calls.append((manufacturer, model))
        return {"hits": hits or [], "note": "בדיקה", "url": "https://example.test/search", "query": f"{manufacturer} {model}"}

    monkeypatch.setattr("app.estimate.research_market", research)
    return calls


def test_insufficient_evidence_does_not_invent_a_number(client, monkeypatch):
    _quiet_research(monkeypatch)
    asset = _asset(client, "V-0514")
    result = client.post(f"/api/assets/{asset['id']}/estimate")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "insufficient"
    assert body["message"] == "Insufficient information to estimate value reliably."
    assert body["headline"].startswith("אין די מידע")
    assert body["figures"] == []
    assert body["required"]
    assert body["helpful"]
    assert "range_low" not in json.dumps(body)
    card = client.get(f"/api/assets/{asset['id']}").json()
    assert card["estimate"]["status"] == "insufficient"
    assert "range_low" not in json.dumps(card)


def test_local_and_matching_market_evidence_produce_three_figures(client, monkeypatch):
    asset = _asset(client, "RDBAL-01")
    card = client.get(f"/api/assets/{asset['id']}").json()
    manufacturer = next(item["value"] for item in card["technical"] if item["label"] == "יצרן")
    model = next(item["value"] for item in card["technical"] if item["label"] == "דגם")
    calls = _quiet_research(
        monkeypatch,
        hits=[
            {
                "kind": "asking",
                "currency": "USD",
                "amount": 4000,
                "title": f"{manufacturer} {model} used listing",
                "detail": "Used",
                "url": "https://example.test/listing",
                "source": "LabX",
                "exact": True,
            },
            {
                "kind": "asking",
                "currency": "USD",
                "amount": 999999,
                "title": "Other Maker OTHER-9",
                "detail": "Used",
                "url": "https://example.test/wrong",
                "source": "LabX",
                "exact": False,
            },
        ],
    )
    for kind, price, source in (
        ("asking", "2000", "מודעה א"),
        ("asking", "5500", "מודעה ב"),
        ("offer", "1500", "הצעה א"),
        ("offer", "1800", "הצעה ב"),
        ("purchase", "9000", "חשבונית"),
    ):
        saved = client.post(
            f"/api/assets/{asset['id']}/evidence",
            json={"value_kind": kind, "price_text": price, "currency": "USD", "source": source, "description": source},
        )
        assert saved.status_code == 200, saved.text
    first = client.post(f"/api/assets/{asset['id']}/estimate")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "estimated"
    assert body["separate_component_values"] is False
    assert "RDBAL-01/01" in body["component_tags"]
    figures = {item["key"]: item["text"] for item in body["figures"]}
    assert figures["fair"] == "2,000–5,500 USD"
    assert figures["expected"] == "1,500–1,800 USD"
    assert figures["quick"] == "1,500 USD"
    dumped = json.dumps(body, ensure_ascii=False)
    assert "999999" not in dumped
    assert "https://example.test/listing" in dumped
    assert "https://example.test/wrong" not in dumped
    assert "9,000" in dumped
    assert calls and calls[0][0] == manufacturer and calls[0][1] == model
    assert "range_low" not in dumped

    client.post(
        f"/api/assets/{asset['id']}/evidence",
        json={"value_kind": "asking", "price_text": "8000", "currency": "USD", "source": "מודעה ג", "description": "מודעה ג"},
    )
    second = client.post(f"/api/assets/{asset['id']}/estimate").json()
    fair = next(item["text"] for item in second["figures"] if item["key"] == "fair")
    assert fair == "2,000–8,000 USD"
    assert len(calls) == 2


def test_linked_components_are_not_valued_separately(client, monkeypatch):
    _quiet_research(monkeypatch)
    machine = _asset(client, "R-4208")
    assert "F-4208" in machine["tags"]
    result = client.post(f"/api/assets/{machine['id']}/estimate")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["asset_id"] == machine["id"]
    assert body["separate_component_values"] is False
    assert "F-4208" in body["component_tags"]
    assert "R-4208" not in body["component_tags"]
    assert body["figures"] == []
    assert "לא חושב שווי נפרד" in body["scope"]
    child = client.get("/api/assets", params={"q": "RDBAL-01/01"}).json()["assets"]
    assert all(asset["tag"] != "RDBAL-01/01" for asset in child)


def test_priced_upload_attaches_and_a_price_without_a_tag_waits(client):
    body = b"Buyer offer for X-9013\nBuyer offer 1500 USD\n"
    uploaded = client.post("/api/uploads", files=[("files", ("x9013-offer.txt", body, "text/plain"))])
    assert uploaded.status_code == 200, uploaded.text
    filed = uploaded.json()["results"][0]
    assert filed["status"] == "filed"
    assert filed["confidence"] == "confirmed"
    assert len(filed["links"]) == 1
    card = client.get(f"/api/assets/{filed['links'][0]['asset_id']}").json()
    assert any(item["value_kind"] == "offer" and item["price_text"] == "1,500" for item in card["evidence"])

    pending = client.post("/api/uploads", data={"note": "Buyer offer 2200 USD for an unnamed pump"})
    assert pending.status_code == 200, pending.text
    waiting = pending.json()["results"][0]
    assert waiting["confidence"] == "uncertain"
    assert waiting["links"] == []
    assert "לא שויך" in waiting["summary"]
    assert "2,200" in waiting["summary"]
    target = _asset(client, "X-9013")
    assigned = client.post(f"/api/uploads/{waiting['id']}", json={"action": "assign", "asset_ids": [target["id"]]})
    assert assigned.status_code == 200, assigned.text
    moved = client.get(f"/api/assets/{target['id']}").json()
    assert any(item["value_kind"] == "offer" and item["price_text"] == "2,200" for item in moved["evidence"])
