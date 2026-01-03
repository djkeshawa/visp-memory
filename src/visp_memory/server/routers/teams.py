from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.core.clock import utc_now
from visp_memory.core.team import Team, TeamManager, User
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.schemas import (
    MemberAdd,
    TeamCreate,
    TeamResponse,
    UserCreate,
    UserResponse,
)

router = APIRouter(prefix="/teams", tags=["teams"])


def _require_admin(current_user: UserContext):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team management requires an admin user",
        )


def _can_access_user(user_id: str, current_user: UserContext) -> bool:
    return current_user.is_admin or current_user.user_id == user_id


def _can_access_team(team_id: str, current_user: UserContext) -> bool:
    return current_user.is_admin or (
        bool(current_user.team_id) and current_user.team_id == team_id
    )


# User Endpoints
@router.post("/users", response_model=UserResponse)
async def create_user(
    request: Request,
    user: UserCreate,
    current_user: UserContext = Depends(get_current_user),
):
    """Create a new user."""
    _require_admin(current_user)
    team_mgr = TeamManager(request.app.state.storage)

    user_obj = User(
        id=user.id or user.username.lower(),
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        metadata=user.metadata or {},
    )

    try:
        user_id = team_mgr.create_user(user_obj)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        **user_obj.__dict__,
        "id": user_id,
        "created_at": utc_now(),
        "last_active": utc_now(),
    }


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    request: Request,
    user_id: str,
    current_user: UserContext = Depends(get_current_user),
):
    """Get user details."""
    if not _can_access_user(user_id, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    team_mgr = TeamManager(request.app.state.storage)
    user = team_mgr.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {
        **user.__dict__,
        "created_at": user.created_at or utc_now(),
        "last_active": user.last_active or utc_now(),
    }


# Team Endpoints
@router.post("", response_model=TeamResponse)
async def create_team(
    request: Request,
    team: TeamCreate,
    current_user: UserContext = Depends(get_current_user),
):
    """Create a new team."""
    _require_admin(current_user)
    team_mgr = TeamManager(request.app.state.storage)

    team_obj = Team(
        id=team.id or team.name.lower().replace(" ", "-"),
        name=team.name,
        description=team.description,
        metadata=team.metadata or {},
    )

    try:
        team_id = team_mgr.create_team(team_obj)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        **team_obj.__dict__,
        "id": team_id,
        "created_at": utc_now(),
    }


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    request: Request,
    team_id: str,
    current_user: UserContext = Depends(get_current_user),
):
    """Get team details."""
    if not _can_access_team(team_id, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    team_mgr = TeamManager(request.app.state.storage)
    team = team_mgr.get_team(team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    return {
        **team.__dict__,
        "created_at": team.created_at or utc_now(),
    }


@router.post("/{team_id}/members")
async def add_member(
    request: Request,
    team_id: str,
    member: MemberAdd,
    current_user: UserContext = Depends(get_current_user),
):
    """Add a member to a team."""
    _require_admin(current_user)
    team_mgr = TeamManager(request.app.state.storage)
    try:
        success = team_mgr.add_member(team_id, member.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if not success:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support team membership",
        )

    return {"status": "success"}


@router.get("/users/{user_id}/teams", response_model=List[TeamResponse])
async def get_user_teams(
    request: Request,
    user_id: str,
    current_user: UserContext = Depends(get_current_user),
):
    """Get all teams for a user."""
    if not _can_access_user(user_id, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot list teams")

    team_mgr = TeamManager(request.app.state.storage)
    teams = team_mgr.get_user_teams(user_id)
    if not current_user.is_admin:
        teams = [team for team in teams if team.id == current_user.team_id]

    return [
        {
            **t.__dict__,
            "created_at": t.created_at or utc_now(),
        }
        for t in teams
    ]
