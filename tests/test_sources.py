"""Data sources users add: profiling, safety checks, and a CSV source asked through the API end to end."""

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.agents.orchestrated.plan import Plan
from app.api.main import create_app
from app.api.security import SecurityStore
from app.mcp_server.server import server
from app.sources.profile import (
    Column,
    Table,
    identifier,
    kind_of,
    model_sql,
    semantic_layer,
)
from app.sources.store import SourceStore, check_host, connection_string

PASSWORD = "correct horse battery"
SALES = (b"Order Date,Region,Product,Customer ID,Units,Amount\n2025-01-03,North,Tea,c1,3,7.5\n"
         b"2025-01-09,South,Coffee,c2,2,6\n2025-02-11,North,Coffee,c1,5,15\n2025-02-20,South,Tea,c3,1,2.5\n"
         b"2025-03-02,North,Tea,c2,4,10\n")


def test_columns_become_dates_groups_numbers_or_keys():
    assert identifier("Sales 2025.csv") == "sales_2025" and identifier("2025 sales") == "t_2025_sales"
    used = set()
    assert [identifier("a", used), identifier("a", used)] == ["a", "a_2"]
    kinds = {c.name: kind_of(c) for c in [Column("sold_on", "DATE"), Column("region", "VARCHAR", 3),
                                          Column("email", "VARCHAR", 90000), Column("amount", "DOUBLE"),
                                          Column("customer_id", "BIGINT"), Column("blob", "BLOB")]}
    assert kinds == {"sold_on": "time", "region": "group", "email": "skip", "amount": "number",
                     "customer_id": "key", "blob": "skip"}


def test_generated_metrics_say_how_they_are_computed_and_tables_without_dates_get_one():
    table = Table("offices", "raw.offices", "Offices", [Column("city", "VARCHAR", 2), Column("headcount", "BIGINT")])
    layer, notes = semantic_layer([table])
    assert [m["name"] for m in layer["metrics"]] == ["offices_count", "offices_headcount_total", "offices_headcount_average"]
    assert layer["metrics"][1]["description"] == "Sum of headcount over the rows of Offices."
    assert "no date column" in notes[0]
    assert "as loaded_on" in model_sql(table, "2026-10-14")
    renamed = Table("s", "raw.s", "S", [Column("order_date", "DATE", source="Order Date")])
    assert '"Order Date" as order_date' in model_sql(renamed, "2026-10-14")


def test_connection_strings_carry_no_password_and_private_addresses_are_refused(monkeypatch):
    text = connection_string("postgres", "db.example.com", 5432, "shop", "reader")
    assert text.startswith("host=db.example.com port=5432 dbname=shop user=reader") and "password" not in text
    with pytest.raises(ValueError, match="cannot contain spaces"):
        connection_string("mysql", "db.example.com", 3306, "my shop", "reader")
    monkeypatch.delenv("ALLOW_PRIVATE_SOURCES", raising=False)
    for host in ("127.0.0.1", "localhost", "10.0.0.5", "192.168.1.20"):
        with pytest.raises(ValueError, match="private network"):
            check_host(host)
    monkeypatch.setenv("ALLOW_PRIVATE_SOURCES", "1")
    check_host("127.0.0.1")  # allowed on purpose


def test_passwords_are_encrypted_at_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_SOURCES", "1")
    store = SourceStore(tmp_path / "console.sqlite", tmp_path / "sources")
    source = store.add_connection("ana", "Shop", "postgres", "127.0.0.1", None, "shop", "reader", "s3cret-pass")
    assert b"s3cret-pass" not in (tmp_path / "console.sqlite").read_bytes()
    assert not any(b"s3cret-pass" in p.read_bytes() for p in (tmp_path / "sources").rglob("*") if p.is_file())
    environment = store.environment(source["id"])
    assert environment["PGPASSWORD"] == "s3cret-pass" and "s3cret-pass" not in environment["SOURCE_ATTACH"]
    mysql = store.add_connection("ana", "Shop", "mysql", "127.0.0.1", None, "shop", "reader", "it's a 'secret'")
    assert store.environment(mysql["id"])["MYSQL_PWD"] == "it's a 'secret'"  # any password works, spaces and quotes too
    with pytest.raises(KeyError):
        store.get("bob", source["id"])  # private to its owner


# ---- through the API ------------------------------------------------------------------------

REGION_PLAN = Plan.model_validate({"question_type": "ranking", "steps": [
    {"id": "by_region", "purpose": "Amount by region", "metrics": ["sales_2025_amount_total"],
     "group_by": ["sales_2025__region"]}]})


async def plan_fn(messages):
    system = messages[0].content
    assert "coffee" not in system.lower() and "Brooklyn" not in system  # the sample shop's wording is gone
    return REGION_PLAN.model_copy(deep=True), None, {"input_tokens": 1000, "output_tokens": 100}


async def write_fn(messages):
    return "North sold the most: 32.5, against 8.5 in the South.", {"input_tokens": 500, "output_tokens": 50}


def wait_until_built(client, source_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        source = client.get(f"/api/sources/{source_id}").json()
        if source["status"] != "building":
            return source
        time.sleep(1)
    raise AssertionError("the source did not finish building")


@pytest.fixture
def two_users(tmp_path):
    store = SecurityStore(tmp_path / "console.sqlite")
    store.add_user("ana", PASSWORD)
    store.add_user("bob", PASSWORD)
    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, traces_dir=tmp_path / "traces",
                     checkpoint_db=tmp_path / "c.sqlite", results_dir=tmp_path, auth=True,
                     security_db=tmp_path / "console.sqlite", sources_dir=tmp_path / "sources")
    with TestClient(app) as client:
        yield client


def sign_in(client, name):
    client.cookies.clear()
    client.post("/api/auth/login", json={"name": name, "password": PASSWORD})


@pytest.mark.integration
def test_a_csv_upload_becomes_a_private_source_the_agent_can_answer_from(two_users):
    client = two_users
    sign_in(client, "ana")
    created = client.post("/api/sources/csv", data={"name": "Sales 2025"},
                          files=[("files", ("Sales 2025.csv", SALES, "text/csv"))]).json()
    assert created["status"] == "building"
    built = wait_until_built(client, created["id"])
    assert built["status"] == "ready", built.get("error")
    assert built["tables"][0]["rows"] == 5 and built["metrics"] > 3
    names = {m["name"] for m in client.get("/api/metrics", params={"source": created["id"]}).json()["metrics"]}
    assert {"sales_2025_amount_total", "sales_2025_customer_id_distinct"} <= names

    events = [json.loads(line[6:]) for line in client.post("/api/ask", json={
        "question": "Which region sold the most?", "source": created["id"]}).text.splitlines() if line.startswith("data: ")]
    analyzed = next(e for e in events if e.get("node") == "analyze")
    rows = {r["sales_2025__region"]: r["sales_2025_amount_total"] for r in analyzed["data"]["facts"]["steps"][0]["rows"]}
    assert rows == {"North": 32.5, "South": 8.5}
    assert next(e for e in events if e.get("node") == "review")["data"]["issues"] == []
    assert client.get("/api/history").json()[0]["source"] == created["id"]

    sign_in(client, "bob")  # someone else: the source does not exist for them
    assert [s["id"] for s in client.get("/api/sources").json()] == ["sample"]
    assert client.get(f"/api/sources/{created['id']}").status_code == 404
    assert client.post("/api/ask", json={"question": "Which region sold the most?",
                                         "source": created["id"]}).status_code == 404

    sign_in(client, "ana")
    assert client.delete(f"/api/sources/{created['id']}").status_code == 200
    assert [s["id"] for s in client.get("/api/sources").json()] == ["sample"]


@pytest.mark.integration
def test_a_database_file_is_queried_read_only_and_bad_uploads_are_refused(two_users, tmp_path):
    client = two_users
    sign_in(client, "ana")
    path = tmp_path / "shop.sqlite"
    db = sqlite3.connect(path)
    db.execute("create table orders (id integer, ordered_at date, city text, total real)")
    db.executemany("insert into orders values (?, ?, ?, ?)",
                   [(1, "2025-01-02", "Paris", 10.0), (2, "2025-01-05", "Lyon", 4.5), (3, "2025-02-01", "Paris", 7.25)])
    db.commit()
    db.close()
    created = client.post("/api/sources/file", data={"name": "Shop"},
                          files={"file": ("shop.sqlite", path.read_bytes(), "application/octet-stream")}).json()
    built = wait_until_built(client, created["id"])
    assert built["status"] == "ready", built.get("error")
    assert {"orders_total_total", "orders_id_distinct"} <= {
        m["name"] for m in client.get("/api/metrics", params={"source": created["id"]}).json()["metrics"]}

    wrong = client.post("/api/sources/csv", data={"name": "x"}, files=[("files", ("notes.exe", b"MZ", "application/x"))])
    assert wrong.status_code == 422 and "Only CSV" in wrong.json()["detail"]
    private = client.post("/api/sources/connection", json={"name": "Inside", "kind": "postgres", "host": "127.0.0.1",
                                                           "database": "x", "user": "y", "password": "z"})
    assert private.status_code == 422 and "private network" in private.json()["detail"]


def test_names_metricflow_keeps_for_itself_are_renamed():
    """Reproduces a CSV with a 'day' column failing to build: MetricFlow refuses time-grain names."""
    assert identifier("day") == "day_data" and identifier("Year") == "year_data"
    assert identifier("month.csv") == "month_data" and identifier("daily") == "daily"
    # A schema.table label from a live database keeps both parts (it once became just "rfam").
    assert identifier("Rfam.family") == "rfam_family" and identifier("shop.db") == "shop"


@pytest.mark.integration
def test_a_csv_whose_headers_look_like_numbers_keeps_them(tmp_path):
    """Reproduces a real import: headers 1958, 1959 were taken for data and the columns became column1, column2."""
    import subprocess
    import sys

    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "air.csv").write_text('"Month","1958","1959"\n"JAN",340,360\n"FEB",318,342\n', encoding="utf-8")
    (tmp_path / "source.json").write_text(json.dumps({"id": "air", "name": "Air", "kind": "csv", "files": ["air.csv"]}))
    subprocess.run([sys.executable, "-m", "app.sources.build", str(tmp_path)], check=True, capture_output=True)
    semantic = (tmp_path / "models" / "semantic.yml").read_text(encoding="utf-8")
    assert "air_t_1958_total" in semantic and "column1" not in semantic
