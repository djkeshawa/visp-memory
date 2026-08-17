"""The local owner reads its own store; tenancy is unchanged for everyone else.

`visp-memory serve` on a loopback bind with no credentials starts open local mode,
whose principal is anonymous and teamless by design. Measuring that principal
against the tenancy rule is why the dashboard read zero: every row failed a
comparison neither side was ever going to satisfy.

The rule itself is not the thing that changed. These tests pin both halves — that
the local owner sees its store, and that no tenant boundary moved to let it.
"""

import pytest
from fastapi import HTTPException

from visp_memory.server.auth import UserContext
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_admin,
    require_repo_scope_access,
)


class StubStorage:
    """Answers repository lookups the way LocalStorage does, without a store."""

    def __init__(self, repositories=None):
        self._repositories = repositories or {}

    def get_repository(self, repo_id):
        return self._repositories.get(repo_id)


LOCAL_OWNER = UserContext(user_id="anonymous", username="anonymous", is_local_owner=True)
ANONYMOUS = UserContext(user_id="anonymous", username="anonymous")
TEAM_MEMBER = UserContext(user_id="u1", username="u1", team_id="team-1")
OTHER_TEAM = UserContext(user_id="u2", username="u2", team_id="team-2")
ADMIN = UserContext(user_id="local", username="local", is_admin=True)

NO_REPOS = StubStorage()
UNTENANTED_REPO = StubStorage({"alpha": {"id": "alpha", "team_id": None}})
OWNED_REPO = StubStorage({"alpha": {"id": "alpha", "team_id": "team-1"}})
IMPLICIT_REPO = StubStorage(
    {"alpha": {"id": "alpha", "team_id": None, "metadata": {"registration": "implicit"}}}
)


def _memory(**overrides):
    return {"id": "m1", "repo_id": "alpha", "metadata": {}, **overrides}


# --- what the local owner gains ------------------------------------------------


def test_the_local_owner_reads_its_own_store():
    assert can_access_scoped_record(NO_REPOS, _memory(), LOCAL_OWNER, scope_field="metadata")


def test_the_local_owner_reads_a_record_a_team_once_scoped():
    # Parity with `visp-memory recall`, which reads the same file with no tenancy
    # filter at all. Serving its own dashboard less was never a boundary.
    record = _memory(metadata={"team_id": "team-1"})

    assert can_access_scoped_record(OWNED_REPO, record, LOCAL_OWNER, scope_field="metadata")


def test_the_local_owner_is_not_an_admin():
    # The only one of these claims with nothing behind it until now. Read access to
    # the local store must not carry an administrative surface with it.
    assert LOCAL_OWNER.is_admin is False

    with pytest.raises(HTTPException) as refusal:
        require_admin(LOCAL_OWNER)

    assert refusal.value.status_code == 403


# --- what did not move ---------------------------------------------------------


def test_a_teamless_principal_that_is_not_the_local_owner_still_sees_nothing():
    # The scenario that reaches the network: allow_anonymous is settable from the
    # config file and the environment, so an anonymous principal can exist on a
    # public bind. Without the loopback grant it reads exactly what it read before.
    assert not can_access_scoped_record(NO_REPOS, _memory(), ANONYMOUS, scope_field="metadata")


def test_an_untenanted_record_stays_hidden_from_a_teamless_principal():
    assert not can_access_scoped_record(
        UNTENANTED_REPO, _memory(), ANONYMOUS, scope_field="metadata"
    )


def test_a_registered_user_with_no_team_still_sees_nothing():
    # get_current_user builds session and PAT contexts with team_id=account.get(
    # "team_id"), which is None for an account nobody put in a team.
    teamless_session = UserContext(user_id="u9", username="u9", auth_type="session")

    assert not can_access_scoped_record(
        UNTENANTED_REPO, _memory(), teamless_session, scope_field="metadata"
    )


def test_a_record_owned_by_a_team_stays_hidden_from_another_team():
    assert not can_access_scoped_record(OWNED_REPO, _memory(), OTHER_TEAM, scope_field="metadata")


def test_a_record_scoped_to_another_team_stays_hidden():
    record = _memory(metadata={"team_id": "team-2"})

    assert not can_access_scoped_record(
        UNTENANTED_REPO, record, TEAM_MEMBER, scope_field="metadata"
    )


def test_a_record_scoped_to_the_users_team_is_visible():
    record = _memory(metadata={"team_id": "team-1"})

    assert can_access_scoped_record(UNTENANTED_REPO, record, TEAM_MEMBER, scope_field="metadata")


def test_a_token_restricted_to_other_projects_sees_nothing_here():
    scoped_token = UserContext(
        user_id="t1", username="t1", auth_type="pat", is_admin=True, repo_ids=["beta"]
    )

    assert not can_access_scoped_record(
        UNTENANTED_REPO, _memory(), scoped_token, scope_field="metadata"
    )


def test_an_admin_sees_every_record():
    assert can_access_scoped_record(OWNED_REPO, _memory(), ADMIN, scope_field="metadata")


def test_a_repository_owned_by_another_team_is_still_not_found():
    with pytest.raises(HTTPException) as refusal:
        require_repo_scope_access(OWNED_REPO, "alpha", OTHER_TEAM)

    assert refusal.value.status_code == 404


def test_an_explicitly_registered_untenanted_repository_behaves_as_before():
    # Registering without a team hid the project from every non-admin before this
    # branch, and still does. Only rows the store wrote for itself are exempt.
    with pytest.raises(HTTPException) as refusal:
        require_repo_scope_access(UNTENANTED_REPO, "alpha", TEAM_MEMBER)

    assert refusal.value.status_code == 404


# --- the rows LC-86 creates ----------------------------------------------------


def test_a_row_the_store_wrote_for_itself_is_invisible_to_authorization():
    # It has to behave exactly like the absent row it replaced, or giving
    # `repositories` its missing rows would silently change who can see what.
    require_repo_scope_access(IMPLICIT_REPO, "alpha", TEAM_MEMBER)


def test_a_row_the_store_wrote_for_itself_grants_no_visibility():
    assert not can_access_scoped_record(
        IMPLICIT_REPO, _memory(), ANONYMOUS, scope_field="metadata"
    )
