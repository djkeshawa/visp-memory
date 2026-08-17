"""Tenant equality, not tenant presence (LC-84).

A record with no team, in a repository with no team, belongs to nobody in
particular — and so does the anonymous principal `visp-memory serve` starts a
loopback server with. Requiring a team on both sides made `allow_anonymous` a
switch that granted 200s with empty bodies.
"""

import pytest
from fastapi import HTTPException

from visp_memory.server.auth import UserContext
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
)


class StubStorage:
    """Answers repository lookups the way LocalStorage does, without a store."""

    def __init__(self, repositories=None):
        self._repositories = repositories or {}

    def get_repository(self, repo_id):
        return self._repositories.get(repo_id)


ANONYMOUS = UserContext(user_id="anonymous", username="anonymous")
TEAM_MEMBER = UserContext(user_id="u1", username="u1", team_id="team-1")
ADMIN = UserContext(user_id="local", username="local", is_admin=True)

UNTENANTED_REPO = StubStorage({"alpha": {"id": "alpha", "team_id": None}})
OWNED_REPO = StubStorage({"alpha": {"id": "alpha", "team_id": "team-1"}})


def _memory(**overrides):
    return {"id": "m1", "repo_id": "alpha", "metadata": {}, **overrides}


def test_an_untenanted_record_is_visible_to_the_local_anonymous_principal():
    assert can_access_scoped_record(
        UNTENANTED_REPO, _memory(), ANONYMOUS, scope_field="metadata"
    )


def test_an_unregistered_repository_reads_the_same_as_an_untenanted_one():
    assert can_access_scoped_record(
        StubStorage(), _memory(), ANONYMOUS, scope_field="metadata"
    )


def test_a_record_owned_by_a_team_stays_hidden_from_a_teamless_principal():
    assert not can_access_scoped_record(
        OWNED_REPO, _memory(), ANONYMOUS, scope_field="metadata"
    )


def test_a_record_scoped_to_another_team_stays_hidden():
    record = _memory(metadata={"team_id": "team-2"})

    assert not can_access_scoped_record(
        UNTENANTED_REPO, record, TEAM_MEMBER, scope_field="metadata"
    )


def test_a_record_scoped_to_the_users_team_is_visible():
    record = _memory(metadata={"team_id": "team-1"})

    assert can_access_scoped_record(
        UNTENANTED_REPO, record, TEAM_MEMBER, scope_field="metadata"
    )


def test_a_token_restricted_to_other_projects_sees_nothing_here():
    scoped_token = UserContext(
        user_id="t1", username="t1", auth_type="pat", is_admin=True, repo_ids=["beta"]
    )

    assert not can_access_scoped_record(
        UNTENANTED_REPO, _memory(), scoped_token, scope_field="metadata"
    )


def test_an_admin_sees_every_record():
    assert can_access_scoped_record(OWNED_REPO, _memory(), ADMIN, scope_field="metadata")


def test_an_untenanted_repository_does_not_hide_itself_from_a_team_member():
    # The store now creates a row for every project scope a write names, and that
    # row has to stay indistinguishable from the absent row it replaced.
    require_repo_scope_access(UNTENANTED_REPO, "alpha", TEAM_MEMBER)


def test_a_repository_owned_by_another_team_is_still_not_found():
    other_team = UserContext(user_id="u2", username="u2", team_id="team-2")

    with pytest.raises(HTTPException) as refusal:
        require_repo_scope_access(OWNED_REPO, "alpha", other_team)

    assert refusal.value.status_code == 404
