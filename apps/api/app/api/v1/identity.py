from fastapi import APIRouter, Depends

from app.core.auth import Principal, get_current_principal

router = APIRouter(prefix="/api/v1", tags=["identity"])


@router.get("/me")
async def current_user(principal: Principal = Depends(get_current_principal)) -> dict:
    """Return the authenticated caller's identity and tenant scope."""
    return {
        "subject": principal.subject,
        "tenant_id": principal.tenant_id,
        "roles": sorted(principal.roles),
    }
