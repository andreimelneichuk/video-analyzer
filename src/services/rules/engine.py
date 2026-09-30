from src.models.analysis import IntegrationAnalysis
from src.models.enums import BannerDefect, IntegrationClass
from src.schemas.analysis import VlmRawObservation

# Calibrated on banner_review_examples.pdf: "too small" banners measured 2.4-4.6%
# of the frame, fully paid ones 5.4% and up.
TOO_SMALL_AREA_PERCENT = 5.0
# "Slightly small" band gets the reduced 20% from the PDF quick guide: DbpiVrpMa1j
# measured 5.5% and was docked 20%; the smallest fully paid banner is 5.8%.
SLIGHTLY_SMALL_AREA_PERCENT = 5.7

# Banner bbox is in 0-1000 frame coordinates. A box within this margin of the
# left/right/top frame edge means the banner runs off-screen (logo clipped).
EDGE_MARGIN = 10

# Top strip covered by the Reels/Shorts/Facebook app header. A banner flush to
# the top edge (Facebook example #20) sits under it and is not visible.
APP_HEADER_ZONE = 80
HEADER_NOT_VISIBLE_SHARE = 0.6

# Like/comment/share/remix column on the right of Shorts/Reels, measured from
# app screenshots (x from ~87% of the width, y from ~51% to ~98% of the height).
SIDE_BUTTONS_ZONE = (870, 510, 1000, 980)

# A banner only counts as covered when the permanent app UI hides a real part of
# it: Dbghg_3RrsX grazes the header by 2px and is paid in full. Transient overlays
# (YouTube "Auto-dubbed" badge, search suggestion) are ignored on purpose.
UI_OVERLAP_SHARE = 0.25


class SkycoachRuleEngine:
    """
    Deterministic business decision engine implementing Skycoach's internal
    payout guidelines and defect penalty rules.
    """

    @classmethod
    def evaluate(cls, obs: VlmRawObservation) -> IntegrationAnalysis:
        """
        Evaluates raw perceptual VLM observations and computes deterministic verdicts.
        """
        # 1. Determine integration class (0, 1, 2)
        if not obs.has_skycoach_mention:
            integration_class = IntegrationClass.NONE
        elif obs.is_product_advertised:
            integration_class = IntegrationClass.DIRECT_AD
        else:
            integration_class = IntegrationClass.MENTION

        # If no brand mention at all (Class 0)
        if integration_class == IntegrationClass.NONE:
            return IntegrationAnalysis(
                integration_class=IntegrationClass.NONE,
                prominence_score=1,
                has_correct_logo=False,
                banner_duration_seconds=0.0,
                screen_percentage=0.0,
                has_voice_cta=False,
                has_text_cta=False,
                promo_code=None,
                defects=[],
                deduction_percent=100,
                payout_recommendation="Excluded / No mention",
                reasoning=(
                    "Класс 0: В ролике не обнаружено присутствия бренда Skycoach "
                    "(логотип, название, промокод и голосовой призыв отсутствуют). "
                    "Выплата не производится."
                ),
            )

        # 2. Prominence Score calculation (1 to 5, penalized if wrong logo)
        prominence_score = cls._calculate_prominence(obs)

        # 3. Deductions & defect penalties calculation
        deduction_percent, defects, recommendation = cls._calculate_deductions(obs)

        # 4. Detailed justification text for influence manager
        reasoning = cls._build_reasoning(
            obs, integration_class, prominence_score, defects, recommendation
        )

        has_correct = obs.has_correct_logo and (BannerDefect.WRONG_LOGO not in defects)

        return IntegrationAnalysis(
            integration_class=integration_class,
            prominence_score=prominence_score,
            has_correct_logo=has_correct,
            banner_duration_seconds=obs.banner_duration_seconds,
            screen_percentage=obs.screen_percentage,
            has_voice_cta=obs.has_voice_cta,
            has_text_cta=obs.has_text_cta,
            promo_code=obs.promo_code,
            defects=[d.value for d in defects],
            deduction_percent=deduction_percent,
            payout_recommendation=recommendation,
            reasoning=reasoning,
        )

    @staticmethod
    def _calculate_prominence(obs: VlmRawObservation) -> int:
        """
        Evaluates prominence score from 1 (flash of logo) to 5 (very aggressive)
        based on duration, screen percentage, and calls to action.
        Penalizes score if a wrong or foreign logo is detected.
        """
        dur = obs.banner_duration_seconds
        area = obs.screen_percentage
        has_cta = obs.has_voice_cta or obs.has_text_cta

        if dur < 2.0 and area < 4.0:
            score = 1
        elif dur < 4.5 and not has_cta:
            score = 2
        elif dur >= 9.0 and area >= 12.0 and obs.has_voice_cta:
            score = 5
        elif (dur >= 6.5 and area >= 7.0) or (has_cta and area >= 6.0):
            score = 4
        else:
            score = 3

        # If banner has a wrong/competitor logo, severely reduce the prominence rating
        if not obs.has_correct_logo or BannerDefect.WRONG_LOGO in obs.observed_defects:
            score = max(1, score - 2)

        return score

    @staticmethod
    def _calculate_deductions(
        obs: VlmRawObservation,
    ) -> tuple[int, list[BannerDefect], str]:
        """
        Computes exact payout deductions matching Skycoach banner review examples:
        - Banner cut off (edge / logo clipped) -> 20% deduction
        - Banner too small (< 5% of frame) -> 30% deduction
        - Banner too low / overlapped by UI or camera -> 20% deduction
        - Banner not visible / fully covered -> excluded (100% deduction / 0 payout)
        - Wrong logo / competitor logo -> 30% deduction
        """
        defects = list(obs.observed_defects)
        for defect in SkycoachRuleEngine._placement_defects(obs.banner_bbox):
            if defect not in defects:
                defects.append(defect)

        # Severely obscured or not visible
        if BannerDefect.NOT_VISIBLE in defects or obs.screen_percentage < 0.8:
            if BannerDefect.NOT_VISIBLE not in defects:
                defects.append(BannerDefect.NOT_VISIBLE)
            return 100, defects, "Excluded (Banner not visible)"

        # Auto-detect too small banner
        if (
            obs.screen_percentage < SLIGHTLY_SMALL_AREA_PERCENT
            and BannerDefect.TOO_SMALL not in defects
        ):
            defects.append(BannerDefect.TOO_SMALL)
        slightly_small = (
            TOO_SMALL_AREA_PERCENT <= obs.screen_percentage < SLIGHTLY_SMALL_AREA_PERCENT
        )

        # Auto-detect wrong logo defect if has_correct_logo is False
        if not obs.has_correct_logo and BannerDefect.WRONG_LOGO not in defects:
            defects.append(BannerDefect.WRONG_LOGO)

        penalties = [
            (BannerDefect.CUT_OFF_EDGE, 20, "Баннер обрезан по краю (-20%)"),
            (BannerDefect.OVERLAPPED_BY_UI, 20, "Баннер перекрыт интерфейсом или камерой (-20%)"),
            (BannerDefect.TOO_SMALL, 20, "Баннер немного мелковат (-20%)")
            if slightly_small
            else (BannerDefect.TOO_SMALL, 30, "Баннер слишком мелкий (-30%)"),
            (BannerDefect.WRONG_LOGO, 30, "Чужой или некорректный логотип на баннере (-30%)"),
        ]
        applied = [(pct, reason) for defect, pct, reason in penalties if defect in defects]

        # The payout examples apply one deduction per video: the heaviest defect
        # wins, the rest are only reported (cut off + too low -> 20%, not 40%).
        deduction = max((pct for pct, _ in applied), default=0)
        reasons = [reason for _, reason in applied]

        if deduction == 0:
            recommendation = "Full payout (No deduction)"
        else:
            recommendation = f"{deduction}% deduction: {', '.join(reasons)}"

        return deduction, defects, recommendation

    @staticmethod
    def _placement_defects(bbox: list[float] | None) -> list[BannerDefect]:
        """Placement defects that follow from where the banner sits in the frame."""
        if not bbox or len(bbox) != 4:
            return []
        x1, y1, x2, y2 = bbox
        if max(bbox) <= 1.0:  # 0-1 fractions instead of 0-1000
            x1, y1, x2, y2 = (v * 1000 for v in bbox)
        if x2 <= x1 or y2 <= y1:
            return []

        defects: list[BannerDefect] = []
        if x1 <= EDGE_MARGIN or x2 >= 1000 - EDGE_MARGIN or y1 <= EDGE_MARGIN:
            defects.append(BannerDefect.CUT_OFF_EDGE)

        header_overlap = max(0.0, min(y2, APP_HEADER_ZONE) - y1) / (y2 - y1)
        zx1, zy1, zx2, zy2 = SIDE_BUTTONS_ZONE
        side_area = max(0.0, min(x2, zx2) - max(x1, zx1)) * max(0.0, min(y2, zy2) - max(y1, zy1))
        side_overlap = side_area / ((x2 - x1) * (y2 - y1))

        if header_overlap >= HEADER_NOT_VISIBLE_SHARE:
            defects.append(BannerDefect.NOT_VISIBLE)
        elif max(header_overlap, side_overlap) >= UI_OVERLAP_SHARE:
            defects.append(BannerDefect.OVERLAPPED_BY_UI)
        return defects

    @staticmethod
    def _build_reasoning(
        obs: VlmRawObservation,
        integration_class: IntegrationClass,
        score: int,
        defects: list[BannerDefect],
        recommendation: str,
    ) -> str:
        class_desc = (
            "Прямая реклама продукта (услуги, бустинг, валюта)"
            if integration_class == IntegrationClass.DIRECT_AD
            else "Упоминание бренда (логотип/название без прямой продажи)"
        )
        parts = [
            f"Класс {integration_class.value}: {class_desc}.",
            f"Заметность рекламы: {score}/5.",
            f"Хронометраж баннера: ~{obs.banner_duration_seconds:.1f} сек, занимает ~{obs.screen_percentage:.1f}% площади кадра.",
        ]

        if obs.has_voice_cta:
            parts.append("Присутствует голосовой призыв к действию (CTA).")
        if obs.has_text_cta:
            parts.append("Присутствует текстовый оффер / ссылка на экране.")
        if obs.promo_code:
            parts.append(f"Распознан промокод: '{obs.promo_code}'.")

        if not obs.has_correct_logo or BannerDefect.WRONG_LOGO in defects:
            parts.append(
                "Внимание: обнаружен чужой или некорректный логотип (не Skycoach), рейтинг заметности снижен."
            )

        if defects:
            defect_labels = {
                BannerDefect.CUT_OFF_EDGE: "обрезан по краю",
                BannerDefect.TOO_SMALL: "слишком мелкий",
                BannerDefect.OVERLAPPED_BY_UI: "перекрыт интерфейсом Reels / камерой",
                BannerDefect.NOT_VISIBLE: "не виден / скрыт",
                BannerDefect.WRONG_LOGO: "чужой/некорректный логотип на баннере",
            }
            defect_names = [defect_labels.get(d, d.value) for d in defects]
            parts.append(f"Обнаружены дефекты размещения: {', '.join(defect_names)}.")
        else:
            parts.append("Дефектов размещения не обнаружено (баннер расположен корректно).")

        parts.append(f"Вердикт по оплате: {recommendation}.")
        return " ".join(parts)
