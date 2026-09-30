from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AppState(Base):
    """Small key-value store for state that must survive an ephemeral disk."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
