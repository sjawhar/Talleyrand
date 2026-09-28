"""
GET /research/{graph_id}/jobs/{job_id}: one job's record, for a caller that
polls over HTTP instead of holding the job stream open (the `tally` CLI). It
answers only for the case's owner, and only for a job of that case.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.features.auth_jwt import router as auth_app
from talleyrand.features.graph.models import get_graph_repo
from talleyrand.features.research.generation.records import GenerationJobRecord, get_job_repo
from talleyrand.features.research.router import router
from talleyrand.models.user import User

OWNER = "owner@example.com"
CASE = str(uuid4())
OTHER_CASE = str(uuid4())


def _record(graph_id: str, **fields) -> GenerationJobRecord:
    return GenerationJobRecord(
        id=str(uuid4()),
        graph_id=graph_id,
        user_id=OWNER,
        kind="answer",
        node_id=str(uuid4()),
        created_at=datetime.now(UTC),
        **fields,
    )


class _Graphs:
    """Each case belongs to OWNER."""

    async def exists(self, user_id: str, graph_id: str) -> bool:
        return user_id == OWNER and graph_id in (CASE, OTHER_CASE)


class _Jobs:
    def __init__(self) -> None:
        self.records: dict[str, GenerationJobRecord] = {}

    async def get(self, job_id: str) -> GenerationJobRecord | None:
        return self.records.get(job_id)


@pytest.fixture
def jobs() -> _Jobs:
    return _Jobs()


@pytest.fixture
def client(jobs: _Jobs) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_graph_repo] = _Graphs
    app.dependency_overrides[get_job_repo] = lambda: jobs
    app.dependency_overrides[auth_app.require_auth] = lambda: User(id="1", email=OWNER)
    return TestClient(app)


def test_a_failed_job_is_returned_with_its_error(client: TestClient, jobs: _Jobs):
    record = _record(CASE, status="error", error="Unknown model 'x'.")
    jobs.records[record.id] = record

    response = client.get(f"/research/{CASE}/jobs/{record.id}")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"], body["error"]) == (record.id, "error", "Unknown model 'x'.")


def test_a_running_job_the_server_no_longer_runs_reads_as_interrupted(
    client: TestClient, jobs: _Jobs
):
    record = _record(CASE, status="running")
    jobs.records[record.id] = record

    body = client.get(f"/research/{CASE}/jobs/{record.id}").json()

    assert body["status"] == "error"
    assert "interrupted" in body["error"]


def test_an_unknown_job_is_not_found(client: TestClient):
    assert client.get(f"/research/{CASE}/jobs/{uuid4()}").status_code == 404


def test_a_job_of_another_case_is_not_found_under_this_one(client: TestClient, jobs: _Jobs):
    record = _record(OTHER_CASE, status="done", answer="elsewhere")
    jobs.records[record.id] = record

    assert client.get(f"/research/{CASE}/jobs/{record.id}").status_code == 404


def test_someone_elses_case_is_not_found(client: TestClient, jobs: _Jobs):
    record = _record(CASE, status="done", answer="private")
    jobs.records[record.id] = record
    client.app.dependency_overrides[auth_app.require_auth] = lambda: User(
        id="2", email="stranger@example.com"
    )

    assert client.get(f"/research/{CASE}/jobs/{record.id}").status_code == 404
