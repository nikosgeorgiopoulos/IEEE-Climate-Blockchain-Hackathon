"""Sensor.Community parsing, tested on saved fixtures. No real network access."""

import json
from datetime import date
from pathlib import Path

import httpx

from proofofair import db
from proofofair.ingest import sensor_community as sc
from proofofair.verify import verify_reading

FIX = Path(__file__).parent / "fixtures"


def live_records():
    return json.loads((FIX / "sc_live_athens.json").read_text())


def test_parse_merges_sensors_of_one_location_and_minute():
    out = {(p.location_id, p.ts): p for p in sc.parse_records(
        [r for r in live_records() if isinstance(r, dict)])}
    p = out[("18302", "2026-10-19T11:01:00Z")]
    assert p.device_id == "sc-18302"
    assert p.values == {"pm10": 4.47, "pm25": 2.78, "temperature": 21.3, "humidity": 58.1}
    assert p.sensors == {"36001": "SDS011", "36002": "BME280"}
    assert (p.lat, p.lon) == (37.976, 23.736)


def test_parse_is_defensive():
    out = sc.parse_records(live_records())  # includes junk entries and a bare string
    by_loc = {p.location_id: p for p in out}
    assert by_loc["7711"].values == {"pm10": 31.9}  # unparseable P2 dropped
    assert "5" not in by_loc  # bad timestamp
    assert "99" not in by_loc  # only out-of-range values -> nothing left


def test_store_signs_with_gateway_and_verifies():
    db.init_db()
    n = sc.store(sc.parse_records(live_records()))
    assert n == 2
    assert sc.store(sc.parse_records(live_records())) == 0  # dedup on (device_id, ts)
    with db.session() as conn:
        dev = db.get_device(conn, "sc-18302")
        assert dev["source"] == "sensor_community" and dev["signer"] == "gateway"
        assert "SDS011" in dev["name"]
        rid = conn.execute("SELECT id FROM readings WHERE device_id='sc-18302'").fetchone()[0]
    rep = verify_reading(rid)
    assert [c.status for c in rep.checks][:2] == ["pass", "pass"]
    assert "gateway" in rep.checks[1].detail
    assert sc.load_registry()["18302"]["sensors"] == {"36001": "SDS011", "36002": "BME280"}


def test_iter_json_array_streams_small_chunks():
    text = json.dumps(live_records()[:3] + [{"x": "a]b}"}])
    chunks = [text[i:i + 7] for i in range(0, len(text), 7)]
    items = list(sc.iter_json_array(chunks))
    assert len(items) == 4 and items[-1] == {"x": "a]b}"}


def test_area_filter():
    rec = live_records()[0]
    assert sc.in_area(rec, 37.98, 23.73, 30)
    assert not sc.in_area(rec, 40.64, 22.94, 30)  # Thessaloniki
    assert not sc.in_area({"location": {}}, 37.98, 23.73, 30)


def test_archive_csv_parse_and_merge():
    pm = sc.parse_archive_csv((FIX / "sc_archive_sds011.csv").read_text(), "18302")
    th = sc.parse_archive_csv((FIX / "sc_archive_bme280.csv").read_text(), "18302")
    assert [p.ts for p in pm] == ["2026-10-17T00:00:00Z", "2026-10-17T00:05:00Z"]
    assert pm[0].values == {"pm10": 10.5, "pm25": 6.2}  # first sample in the 5-min bucket
    merged = {p.ts: p for p in sc.merge_readings(pm + th)}
    assert merged["2026-10-17T00:05:00Z"].values == {
        "pm10": 12.25, "pm25": 7.1, "temperature": 19.1, "humidity": 64.9}


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_live_uses_area_url():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=live_records())

    db.init_db()
    assert sc.ingest_live(mock_client(handler)) == 2
    assert seen == ["https://data.sensor.community/airrohr/v1/filter/area=37.98,23.73,30"]


def test_network_errors_never_raise():
    def down(req):
        raise httpx.ConnectError("no route", request=req)

    db.init_db()
    assert sc.ingest_live(mock_client(down)) == 0
    assert sc.ingest_hourly_snapshot(mock_client(down)) == 0
    assert sc.ingest_live(mock_client(lambda r: httpx.Response(500))) == 0
    assert sc.ingest_live(mock_client(lambda r: httpx.Response(200, text="<html>"))) == 0


def test_hourly_snapshot_filters_to_athens():
    far = dict(live_records()[0], location={"id": 1, "latitude": "52.5", "longitude": "13.4"})
    payload = json.dumps([far] + live_records()[:3])
    db.init_db()
    n = sc.ingest_hourly_snapshot(mock_client(lambda r: httpx.Response(200, text=payload)))
    assert n == 2
    with db.session() as conn:
        assert db.get_device(conn, "sc-1") is None


def test_archive_backfill_skips_404():
    db.init_db()
    sc.store(sc.parse_records(live_records()))
    csvs = {"sds011_sensor_36001": (FIX / "sc_archive_sds011.csv").read_text(),
            "bme280_sensor_36002": (FIX / "sc_archive_bme280.csv").read_text()}
    urls = []

    def handler(req):
        url = str(req.url)
        urls.append(url)
        for t, body in csvs.items():
            if f"2026-10-17_{t}.csv" in url:
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    n = sc.backfill_archive(days=3, client=mock_client(handler), today=date(2026, 10, 19))
    assert n == 2  # two merged 5-minute readings from 2026-10-17
    assert any("2026-10-17/2026-10-17_sds011_sensor_36001.csv" in u for u in urls)
    with db.session() as conn:
        r = conn.execute("SELECT * FROM readings WHERE ts='2026-10-17T00:05:00Z'").fetchone()
        assert (r["pm25"], r["temperature"]) == (7.1, 19.1)
