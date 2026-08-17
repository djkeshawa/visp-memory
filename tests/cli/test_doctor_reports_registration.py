"""`visp-memory doctor` says when a store's project scopes are unregistered (LC-86).

An empty `repositories` table is invisible from any count: the store is full, the
API answers as though it were empty, and nothing in the output says why.
"""

import json
import sqlite3

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def _doctor_json():
    result = runner.invoke(app, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["repositories"]


def test_doctor_reports_a_store_whose_scopes_are_all_registered(cli_env):
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])

    report = _doctor_json()

    assert report["status"] == "registered"
    assert report["project_scopes"] == ["alpha"]
    assert report["unregistered_scopes"] == []


def test_doctor_names_the_scopes_that_have_no_repository_row(cli_env):
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])
    _forget_repository_rows(cli_env / ".visp-memory" / "data" / "memories.db")

    report = _doctor_json()

    assert report["status"] == "unregistered_scopes"
    assert report["unregistered_scopes"] == ["alpha"]
    assert "alpha" in report["status_message"]


def test_doctor_reports_a_directory_with_no_store(cli_env):
    report = _doctor_json()

    assert report["status"] == "no_store"


def _forget_repository_rows(db_path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM repositories")
        conn.commit()
    finally:
        conn.close()
