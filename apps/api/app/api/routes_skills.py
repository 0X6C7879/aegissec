from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.compat.skills.service import SkillService, get_skill_service
from app.db.models import SkillRecordRead

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("", response_model=list[SkillRecordRead])
async def list_skills(
    skill_service: SkillService = Depends(get_skill_service),
) -> list[SkillRecordRead]:
    return skill_service.list_skills()


@router.get("/{skill_id}", response_model=SkillRecordRead)
async def get_skill(
    skill_id: str,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillRecordRead:
    skill_record = skill_service.get_skill(skill_id)
    if skill_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return skill_record


@router.post("/rescan", response_model=list[SkillRecordRead])
async def rescan_skills(
    skill_service: SkillService = Depends(get_skill_service),
) -> list[SkillRecordRead]:
    return skill_service.rescan_skills()
