from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.compat.skills.service import SkillContentReadError, SkillService, get_skill_service
from app.db.models import SkillContentRead, SkillRecordRead

router = APIRouter(prefix="/api/skills", tags=["skills"])


class ToggleSkillRequest(BaseModel):
    enabled: bool


class SkillContextRead(BaseModel):
    payload: dict[str, object]
    prompt_fragment: str


@router.get("", response_model=list[SkillRecordRead])
async def list_skills(
    skill_service: SkillService = Depends(get_skill_service),
) -> list[SkillRecordRead]:
    return skill_service.list_skills()


@router.get("/skill-context", response_model=SkillContextRead)
async def get_skill_context(
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillContextRead:
    return SkillContextRead(
        payload=skill_service.build_skill_context_payload(),
        prompt_fragment=skill_service.build_skill_context_prompt_fragment(),
    )


@router.get("/{skill_id}", response_model=SkillRecordRead)
async def get_skill(
    skill_id: str,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillRecordRead:
    skill_record = skill_service.get_skill(skill_id)
    if skill_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return skill_record


@router.get("/{skill_id}/content", response_model=SkillContentRead)
async def get_skill_content(
    skill_id: str,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillContentRead:
    try:
        skill_content = skill_service.get_skill_content(skill_id)
    except SkillContentReadError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    if skill_content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return skill_content


@router.post("/rescan", response_model=list[SkillRecordRead])
async def rescan_skills(
    skill_service: SkillService = Depends(get_skill_service),
) -> list[SkillRecordRead]:
    return skill_service.rescan_skills()


@router.post("/scan", response_model=list[SkillRecordRead])
async def scan_skills(
    skill_service: SkillService = Depends(get_skill_service),
) -> list[SkillRecordRead]:
    return skill_service.rescan_skills()


@router.post("/refresh", response_model=list[SkillRecordRead])
async def refresh_skills(
    skill_service: SkillService = Depends(get_skill_service),
) -> list[SkillRecordRead]:
    return skill_service.rescan_skills()


@router.post("/{skill_id}/toggle", response_model=SkillRecordRead)
async def toggle_skill(
    skill_id: str,
    payload: ToggleSkillRequest,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillRecordRead:
    updated = skill_service.set_skill_enabled(skill_id, payload.enabled)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return updated


@router.post("/{skill_id}/enable", response_model=SkillRecordRead)
async def enable_skill(
    skill_id: str,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillRecordRead:
    updated = skill_service.set_skill_enabled(skill_id, True)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return updated


@router.post("/{skill_id}/disable", response_model=SkillRecordRead)
async def disable_skill(
    skill_id: str,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillRecordRead:
    updated = skill_service.set_skill_enabled(skill_id, False)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return updated
