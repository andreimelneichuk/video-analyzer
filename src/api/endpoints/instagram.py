import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.services.extractor import ig_session, ig_session_store

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


# Session checks do blocking network I/O: sync endpoints run in FastAPI's
# threadpool, the async one moves the check to a thread itself.


@router.get("/session", response_model=SessionStatusResponse)
def get_session_status():
    """Current Instagram session state (without contacting Instagram)."""
    return ig_session.get_status()


@router.post("/session", response_model=SessionStatusResponse)
async def update_session(body: SessionUpdateRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """
    Replaces the Instagram session. The new cookies are checked against Instagram
    first; a rejected session is never saved.
    """
    try:
        cookies_text = ig_session.build_cookies_text(body.cookies_txt, body.sessionid)
    except ig_session.InvalidCookiesError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    result = await asyncio.to_thread(ig_session.save_session, cookies_text)
    if not result["saved"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result["error"]
        )
    # Mirror into the database so the session survives an ephemeral disk
    await ig_session_store.persist(db)
    return result


@router.post("/session/check", response_model=SessionStatusResponse)
def check_session():
    """Re-checks the stored session against Instagram."""
    return ig_session.recheck()
