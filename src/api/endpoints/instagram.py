from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.services.extractor import ig_session

router = APIRouter(prefix="/instagram", tags=["instagram"])


class SessionStatusResponse(BaseModel):
    configured: bool
    source: str | None = Field(
        None, description="'ui' — saved from the dashboard, 'file' — mounted cookies.txt"
    )
    valid: bool | None = Field(None, description="None until the session has been checked")
    username: str | None = None
    message: str
    checked_at: str | None = None
    expires_at: str | None = None


class SessionUpdateRequest(BaseModel):
    cookies_txt: str | None = Field(None, description="Full Netscape cookies.txt export")
    sessionid: str | None = Field(
        None, description="Bare value of the instagram.com sessionid cookie"
    )


# Endpoints are sync on purpose: session checks do blocking network I/O,
# FastAPI runs them in its threadpool.


@router.get("/session", response_model=SessionStatusResponse)
def get_session_status():
    """Current Instagram session state (without contacting Instagram)."""
    return ig_session.get_status()


@router.post("/session", response_model=SessionStatusResponse)
def update_session(body: SessionUpdateRequest):
    """
    Replaces the Instagram session. The new cookies are checked against Instagram
    first; a rejected session is never saved.
    """
    try:
        cookies_text = ig_session.build_cookies_text(body.cookies_txt, body.sessionid)
    except ig_session.InvalidCookiesError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    result = ig_session.save_session(cookies_text)
    if not result["saved"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result["error"]
        )
    return result


@router.post("/session/check", response_model=SessionStatusResponse)
def check_session():
    """Re-checks the stored session against Instagram."""
    return ig_session.recheck()
