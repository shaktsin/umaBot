"""Skills management API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from umabot.controlpanel.deps import get_config, get_skill_registry

router = APIRouter(prefix="/skills", tags=["skills"])


class InstallRequest(BaseModel):
    source: str
    name: Optional[str] = None


@router.get("")
async def list_skills(
    skill_registry=Depends(get_skill_registry),
) -> List[Dict[str, Any]]:
    """List all loaded skills."""
    skills = skill_registry.list()
    result = []
    for skill in skills:
        meta = getattr(skill, "metadata", skill)
        result.append(
            {
                "name": getattr(meta, "name", str(skill)),
                "description": getattr(meta, "description", ""),
                "license": getattr(meta, "license", None),
                "source_dir": str(getattr(skill, "path", "") or ""),
                "metadata": {},
            }
        )
    return result


@router.post("/install")
async def install_skill(
    req: InstallRequest,
    config=Depends(get_config),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    """Install a skill from a Git URL or local path."""
    from umabot.skills.installer import SkillInstaller

    install_dir = Path.home() / ".umabot" / "skills"
    installer = SkillInstaller(install_dir)
    try:
        result = await _run_install(installer, req.source, req.name)
        skill_registry.refresh()
        return {"status": "installed", "name": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{name}")
async def remove_skill(
    name: str,
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    """Remove an installed skill."""
    from umabot.skills.installer import SkillInstaller

    install_dir = Path.home() / ".umabot" / "skills"
    installer = SkillInstaller(install_dir)
    try:
        installer.uninstall(name)
        skill_registry.refresh()
        return {"status": "removed", "name": name}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


async def _run_install(installer, source: str, name: Optional[str]) -> str:
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: installer.install(source, name=name))
