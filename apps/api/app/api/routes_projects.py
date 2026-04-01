from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session as DBSession

from app.core.api import AckResponse, PaginationMeta, SortMeta, ok_response
from app.db.models import (
    Project,
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    ProjectSettingsRead,
    ProjectSettingsUpdate,
    ProjectUpdate,
    to_project_detail,
    to_project_read,
    to_project_settings_read,
    utc_now,
)
from app.db.repositories import ProjectRepository, ProjectSettingsRepository
from app.db.session import get_db_session

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _get_existing_project(
    repository: ProjectRepository,
    project_id: str,
    *,
    include_deleted: bool = False,
) -> Project:
    project = repository.get_project(project_id, include_deleted=include_deleted)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get(
    "",
    response_model=list[ProjectRead],
    summary="List projects",
    description="Return projects with pagination, sorting, and fuzzy search.",
)
async def list_projects(
    include_deleted: bool = Query(default=False),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sort_by: Literal["updated_at", "created_at", "name"] = Query(default="updated_at"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    offset = (page - 1) * page_size
    projects = repository.list_projects(
        include_deleted=include_deleted,
        query=q,
        offset=offset,
        limit=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = repository.count_projects(include_deleted=include_deleted, query=q)
    return ok_response(
        [to_project_read(project).model_dump(mode="json") for project in projects],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total),
        sort=SortMeta(by=sort_by, direction=sort_order),
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create project",
    description="Create a new local project container without affecting global user settings.",
)
async def create_project(
    payload: ProjectCreate,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    project = repository.create_project(name=payload.name, description=payload.description)
    return ok_response(to_project_read(project).model_dump(mode="json"), status_code=201)


@router.get(
    "/{project_id}",
    response_model=ProjectDetail,
    summary="Get project",
    description="Return a project and its linked sessions.",
)
async def get_project(
    project_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    project = _get_existing_project(repository, project_id)
    sessions = repository.list_sessions_for_project(project_id)
    return ok_response(to_project_detail(project, sessions).model_dump(mode="json"))


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update project",
    description="Update mutable project metadata.",
)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    project = _get_existing_project(repository, project_id)
    updated_project = repository.update_project(
        project,
        name=payload.name,
        description=payload.description,
    )
    return ok_response(to_project_read(updated_project).model_dump(mode="json"))


@router.delete("/{project_id}", status_code=status.HTTP_200_OK)
async def delete_project(
    project_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    project = _get_existing_project(repository, project_id)
    repository.soft_delete_project(project)
    return ok_response(AckResponse().model_dump(mode="json"))


@router.post("/{project_id}/restore", response_model=ProjectRead)
async def restore_project(
    project_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    project = _get_existing_project(repository, project_id, include_deleted=True)
    restored_project = repository.restore_project(project)
    return ok_response(to_project_read(restored_project).model_dump(mode="json"))


@router.get(
    "/{project_id}/settings",
    response_model=ProjectSettingsRead,
    summary="Get project settings",
    description="Read project-scoped defaults that remain separate from global user API settings.",
)
async def get_project_settings(
    project_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    _get_existing_project(repository, project_id)
    settings_repository = ProjectSettingsRepository(db_session)
    project_settings = settings_repository.get_by_project_id(project_id)
    if project_settings is None:
        now = utc_now()
        return ok_response(
            ProjectSettingsRead(
                project_id=project_id,
                default_workflow_template=None,
                default_runtime_profile_name=None,
                default_queue_backend=None,
                runtime_defaults={},
                notes=None,
                created_at=now,
                updated_at=now,
            ).model_dump(mode="json")
        )
    return ok_response(to_project_settings_read(project_settings).model_dump(mode="json"))


@router.patch(
    "/{project_id}/settings",
    response_model=ProjectSettingsRead,
    summary="Update project settings",
    description=(
        "Update project-scoped workflow/runtime defaults without storing secrets "
        "in the project database."
    ),
)
async def update_project_settings(
    project_id: str,
    payload: ProjectSettingsUpdate,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = ProjectRepository(db_session)
    _get_existing_project(repository, project_id)
    settings_repository = ProjectSettingsRepository(db_session)
    project_settings = settings_repository.upsert(
        project_id=project_id,
        default_workflow_template=payload.default_workflow_template,
        default_runtime_profile_name=payload.default_runtime_profile_name,
        default_queue_backend=payload.default_queue_backend,
        runtime_defaults=payload.runtime_defaults,
        notes=payload.notes,
    )
    return ok_response(to_project_settings_read(project_settings).model_dump(mode="json"))
