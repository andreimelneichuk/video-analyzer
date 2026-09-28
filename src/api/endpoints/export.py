import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import Task, TaskStatus

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/csv")
async def export_csv(db: Annotated[AsyncSession, Depends(get_db)]):
    """
    Exports all analyzed video integrations to a CSV spreadsheet.
    Includes UTF-8 BOM so Excel on Windows and Mac opens Cyrillic text cleanly.
    """
    stmt = (
        select(Task)
        .where(Task.status == TaskStatus.COMPLETED.value)
        .order_by(Task.created_at.desc())
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    output = io.StringIO()
    # Write UTF-8 BOM for Microsoft Excel compatibility
    output.write("\ufeff")

    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "ID задачи",
            "Ссылка на ролик",
            "Автор",
            "Дата публикации",
            "Просмотры",
            "Лайки",
            "Комментарии",
            "Класс интеграции",
            "Заметность (1-5)",
            "Официальный логотип",
            "Длительность баннера (сек)",
            "Площадь баннера (%)",
            "Голосовой CTA",
            "Текстовый CTA",
            "Промокод",
            "Дефекты",
            "Штраф (%)",
            "Вердикт по оплате",
            "Обоснование",
        ]
    )

    for t in tasks:
        m = t.metrics
        a = t.analysis
        writer.writerow(
            [
                t.id,
                t.canonical_url,
                m.author if m and m.author else "N/A",
                m.upload_date if m and m.upload_date else "N/A",
                m.views if m and m.views is not None else "N/A",
                m.likes if m and m.likes is not None else "N/A",
                m.comments if m and m.comments is not None else "N/A",
                a.integration_class if a else "N/A",
                a.prominence_score if a else "N/A",
                "Да" if a and a.has_correct_logo else "Нет",
                f"{a.banner_duration_seconds:.1f}" if a else "N/A",
                f"{a.screen_percentage:.1f}" if a else "N/A",
                "Да" if a and a.has_voice_cta else "Нет",
                "Да" if a and a.has_text_cta else "Нет",
                a.promo_code if a and a.promo_code else "N/A",
                ", ".join(a.defects) if a and a.defects else "Нет",
                f"{a.deduction_percent}%" if a else "0%",
                a.payout_recommendation if a else "N/A",
                a.reasoning if a else "N/A",
            ]
        )

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="skycoach_integrations_report.csv"'},
    )
