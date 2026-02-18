"""Workspace tenancy, files API, and link graph endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Workspace as WorkspaceModel, WorkspaceFile as WorkspaceFileModel, FileLink as FileLinkModel
from app.schemas import (
    Workspace, WorkspaceCreate, WorkspaceUpdate,
    WorkspaceFile, WorkspaceFileCreate,
    FileLink, FileLinkCreate,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
async def list_workspaces(db: AsyncSession = Depends(get_db)) -> list[Workspace]:
    result = await db.execute(select(WorkspaceModel))
    return [Workspace.model_validate(x) for x in result.scalars().all()]


@router.post("")
async def create_workspace(payload: WorkspaceCreate, db: AsyncSession = Depends(get_db)) -> Workspace:
    existing = await db.execute(select(WorkspaceModel).where(WorkspaceModel.id == payload.id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Workspace already exists")
    row = WorkspaceModel(**payload.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return Workspace.model_validate(row)


@router.put("/{workspace_id}")
async def update_workspace(workspace_id: str, payload: WorkspaceUpdate, db: AsyncSession = Depends(get_db)) -> Workspace:
    result = await db.execute(select(WorkspaceModel).where(WorkspaceModel.id == workspace_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Workspace not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.flush()
    await db.refresh(row)
    return Workspace.model_validate(row)


@router.get("/{workspace_id}/files")
async def list_files(workspace_id: str, db: AsyncSession = Depends(get_db)) -> list[WorkspaceFile]:
    result = await db.execute(select(WorkspaceFileModel).where(WorkspaceFileModel.workspace_id == workspace_id))
    return [WorkspaceFile.model_validate(x) for x in result.scalars().all()]


@router.post("/{workspace_id}/files")
async def create_file(workspace_id: str, payload: WorkspaceFileCreate, db: AsyncSession = Depends(get_db)) -> WorkspaceFile:
    data = payload.model_dump()
    data["workspace_id"] = workspace_id
    row = WorkspaceFileModel(**data)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return WorkspaceFile.model_validate(row)


@router.get("/{workspace_id}/links")
async def list_links(workspace_id: str, db: AsyncSession = Depends(get_db)) -> list[FileLink]:
    result = await db.execute(select(FileLinkModel).where(FileLinkModel.workspace_id == workspace_id))
    return [FileLink.model_validate(x) for x in result.scalars().all()]


@router.post("/{workspace_id}/links")
async def create_link(workspace_id: str, payload: FileLinkCreate, db: AsyncSession = Depends(get_db)) -> FileLink:
    data = payload.model_dump()
    data["workspace_id"] = workspace_id
    row = FileLinkModel(**data)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return FileLink.model_validate(row)
