from src.models.analysis import IntegrationAnalysis
from src.models.enums import BannerDefect, IntegrationClass
from src.schemas.analysis import VlmRawObservation


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
        - Banner too small -> 30% deduction (20% if only slightly small)
        - Banner too low / overlapped by UI or camera -> 20% deduction
        - Banner not visible / fully covered -> excluded (100% deduction / 0 payout)
        - Wrong logo / competitor logo -> 30% deduction
        """
        defects = list(obs.observed_defects)

        # Severely obscured or not visible
        if BannerDefect.NOT_VISIBLE in defects or obs.screen_percentage < 0.8:
            if BannerDefect.NOT_VISIBLE not in defects:
                defects.append(BannerDefect.NOT_VISIBLE)
            return 100, defects, "Excluded (Banner not visible)"

        # Auto-detect too small banner if below 4.0% threshold
        if obs.screen_percentage < 4.0 and BannerDefect.TOO_SMALL not in defects:
            defects.append(BannerDefect.TOO_SMALL)

        # Auto-detect wrong logo defect if has_correct_logo is False
        if not obs.has_correct_logo and BannerDefect.WRONG_LOGO not in defects:
            defects.append(BannerDefect.WRONG_LOGO)

        deduction = 0
        reasons: list[str] = []

        if BannerDefect.CUT_OFF_EDGE in defects:
            deduction += 20
            reasons.append("Баннер обрезан по краю (-20%)")

        if BannerDefect.OVERLAPPED_BY_UI in defects:
            deduction += 20
            reasons.append("Баннер перекрыт интерфейсом или камерой (-20%)")

        if BannerDefect.TOO_SMALL in defects:
            penalty = 30 if obs.screen_percentage < 3.2 else 20
            deduction += penalty
            reasons.append(f"Баннер слишком мелкий (-{penalty}%)")

        if BannerDefect.WRONG_LOGO in defects:
            deduction += 30
            reasons.append("Чужой или некорректный логотип на баннере (-30%)")

        # Cap deduction at 100%
        deduction = min(deduction, 100)

        if deduction == 0:
            recommendation = "Full payout (No deduction)"
        elif deduction >= 100:
            recommendation = "Excluded (Multiple severe defects)"
        else:
            recommendation = f"{deduction}% deduction: {', '.join(reasons)}"

        return deduction, defects, recommendation

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
