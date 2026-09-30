import csv
from pathlib import Path

import pytest

from src.models.enums import BannerDefect, IntegrationClass
from src.schemas.analysis import VlmRawObservation
from src.services.ai.vlm_client import VlmClient
from src.services.rules.engine import SkycoachRuleEngine


def test_full_payout_good_video():
    """Verifies that an approved video with no defects receives full payout."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=10.5,
        screen_percentage=14.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code="VALFUN",
        observed_defects=[],
        visual_observations="Баннер Skycoach расположен сверху по центру, виден четко.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.integration_class == IntegrationClass.DIRECT_AD
    assert result.prominence_score >= 4
    assert result.deduction_percent == 0
    assert "Full payout" in result.payout_recommendation
    assert result.promo_code == "VALFUN"
    assert "Дефектов размещения не обнаружено" in result.reasoning


def test_mention_only_class_1():
    """Verifies that brand mention without product promotion is classified as Class 1."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=False,
        banner_duration_seconds=3.5,
        screen_percentage=6.0,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[],
        visual_observations="Мелькнул логотип Skycoach в углу без призыва к покупке.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.integration_class == IntegrationClass.MENTION
    assert result.prominence_score == 2
    assert result.deduction_percent == 0


def test_no_mention_class_0():
    """Verifies that video with no Skycoach presence is classified as Class 0."""
    obs = VlmRawObservation(
        has_skycoach_mention=False,
        is_product_advertised=False,
        banner_duration_seconds=0.0,
        screen_percentage=0.0,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[],
        visual_observations="Обычный геймплей без рекламы.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.integration_class == IntegrationClass.NONE
    assert result.prominence_score == 1
    assert result.deduction_percent == 100
    assert "Excluded" in result.payout_recommendation


def test_game_hud_without_skycoach_is_class_0():
    """
    Verifies that gameplay of Valorant or other games without Skycoach brand
    is strictly Class 0 (no payout).
    """
    obs = VlmRawObservation(
        has_skycoach_mention=False,  # Skycoach is NOT present
        is_product_advertised=False,
        banner_duration_seconds=0.0,
        screen_percentage=0.0,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[],
        visual_observations="В кадре чистый геймплей Valorant, виден логотип игры и оружие, но рекламы Skycoach нет.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.integration_class == IntegrationClass.NONE
    assert result.deduction_percent == 100
    assert "Excluded / No mention" in result.payout_recommendation


def test_cut_off_edge_deduction():
    """Verifies 20% deduction for cut off edge (from banner review examples)."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=8.0,
        screen_percentage=9.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code=None,
        observed_defects=[BannerDefect.CUT_OFF_EDGE],
        visual_observations="Баннер срезан левым краем кадра.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.deduction_percent == 20
    assert "Баннер обрезан по краю (-20%)" in result.payout_recommendation
    assert "обрезан по краю" in result.reasoning


def test_overlapped_by_ui_deduction():
    """Verifies 20% deduction for banner overlapped by UI / camera notch."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=7.0,
        screen_percentage=8.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code=None,
        observed_defects=[BannerDefect.OVERLAPPED_BY_UI],
        visual_observations="Баннер расположен слишком низко, перекрыт кнопками Reels.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.deduction_percent == 20
    assert "Баннер перекрыт интерфейсом или камерой (-20%)" in result.payout_recommendation


def test_too_small_deduction():
    """Verifies 30% deduction for tiny banner."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=6.0,
        screen_percentage=2.5,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[BannerDefect.TOO_SMALL],
        visual_observations="Баннер крайне мелкий в углу.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.deduction_percent == 30
    assert "Баннер слишком мелкий (-30%)" in result.payout_recommendation


@pytest.mark.parametrize(
    ("defects", "expected"),
    [
        # zdlghervyTA: cut off + too low -> one 20% deduction in the examples, not 40%
        ([BannerDefect.CUT_OFF_EDGE, BannerDefect.OVERLAPPED_BY_UI], 20),
        # DbpiVrpMa1j: small AND partly covered -> the heaviest single deduction applies
        ([BannerDefect.TOO_SMALL, BannerDefect.OVERLAPPED_BY_UI], 30),
    ],
)
def test_multiple_defects_apply_single_heaviest_deduction(defects, expected):
    """The payout examples apply one deduction per video, never a sum."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=7.0,
        screen_percentage=8.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code=None,
        observed_defects=defects,
        visual_observations="Несколько дефектов размещения.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.deduction_percent == expected
    assert result.payout_recommendation.startswith(f"{expected}% deduction")
    # Every defect is still reported to the manager
    assert len(result.defects) == len(defects)


def test_banner_not_visible_excluded():
    """Verifies exclusion / 0 payout for invisible or covered banner."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=1.0,
        screen_percentage=0.4,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[BannerDefect.NOT_VISIBLE],
        visual_observations="Баннер полностью закрыт интерфейсом.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.deduction_percent == 100
    assert "Excluded (Banner not visible)" in result.payout_recommendation


@pytest.mark.asyncio
async def test_vlm_client_heuristic_mode():
    """Verifies that VlmClient gracefully falls back to heuristic observation when no API keys are set."""
    client = VlmClient()
    obs = await client.analyze_video(video_path="non_existent_dummy.mp4", has_audio=True)

    assert isinstance(obs, VlmRawObservation)
    assert obs.has_skycoach_mention is True
    assert obs.is_product_advertised is True
    assert obs.banner_duration_seconds > 0
    assert obs.screen_percentage > 0


def test_vlm_client_reference_logo_loaded():
    """Verifies that the official reference logo is successfully loaded as base64."""
    b64_logo = VlmClient._get_reference_logo_b64()
    assert b64_logo is not None
    assert len(b64_logo) > 1000  # Should be a valid base64 image data string


def test_wrong_logo_reduces_prominence_rating_and_applies_penalty():
    """
    Verifies that if a different/foreign logo is displayed instead of the official
    Skycoach logo, the prominence score rating is reduced by 2 points and a 30% deduction applies.
    """
    # Normal banner without defect would have prominence score = 4 (duration 7.0s, area 8.0%)
    obs_good = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        has_correct_logo=True,
        banner_duration_seconds=7.0,
        screen_percentage=8.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code="BOOST",
        observed_defects=[],
        visual_observations="Баннер с официальным логотипом Skycoach.",
    )
    result_good = SkycoachRuleEngine.evaluate(obs_good)
    assert result_good.prominence_score == 4
    assert result_good.deduction_percent == 0
    assert result_good.has_correct_logo is True

    # Same banner but with a foreign/competitor logo
    obs_wrong_logo = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        has_correct_logo=False,  # Wrong logo detected!
        banner_duration_seconds=7.0,
        screen_percentage=8.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code="BOOST",
        observed_defects=[BannerDefect.WRONG_LOGO],
        visual_observations="На баннере изображен логотип стороннего сервиса вместо Skycoach.",
    )
    result_wrong = SkycoachRuleEngine.evaluate(obs_wrong_logo)

    # Prominence score MUST be reduced (from 4 down to 2)
    assert result_wrong.prominence_score == 2
    assert result_wrong.has_correct_logo is False
    assert BannerDefect.WRONG_LOGO.value in result_wrong.defects
    assert result_wrong.deduction_percent == 30
    assert "Чужой или некорректный логотип на баннере (-30%)" in result_wrong.payout_recommendation
    assert "рейтинг заметности снижен" in result_wrong.reasoning


def test_has_correct_logo_false_auto_triggers_wrong_logo_defect():
    """Verifies that has_correct_logo=False automatically injects WRONG_LOGO defect."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        has_correct_logo=False,
        banner_duration_seconds=5.0,
        screen_percentage=5.0,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[],  # empty, should auto-detect WRONG_LOGO
        visual_observations="Баннер с логотипом конкурента.",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.has_correct_logo is False
    assert BannerDefect.WRONG_LOGO.value in result.defects
    assert result.deduction_percent == 30
    assert result.prominence_score == 1  # Base score 3 - 2 = 1


def test_screen_percentage_derived_from_bbox():
    """Banner area is computed from the bbox, overriding the VLM's own estimate."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=9.0,
        screen_percentage=4.5,
        banner_bbox=[217, 122, 756, 169],
        has_voice_cta=False,
        has_text_cta=True,
        visual_observations="Small banner at the top",
    )
    assert obs.screen_percentage == pytest.approx(2.53, abs=0.01)

    analysis = SkycoachRuleEngine.evaluate(obs)
    assert "too_small" in analysis.defects
    assert analysis.deduction_percent == 30


def test_invalid_bbox_keeps_estimate():
    """A degenerate bbox falls back to the VLM's screen_percentage."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=9.0,
        screen_percentage=12.0,
        banner_bbox=[500, 500, 400, 600],
        has_voice_cta=False,
        has_text_cta=True,
        visual_observations="",
    )
    assert obs.screen_percentage == 12.0


@pytest.mark.asyncio
async def test_banner_measurement_overrides_vlm_estimates(monkeypatch):
    """Measured bbox/duration/promo replace the VLM's guesses and drop its too_small call."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=5.0,
        screen_percentage=12.0,
        promo_code="MERC100",
        observed_defects=[BannerDefect.TOO_SMALL],
        has_voice_cta=False,
        has_text_cta=True,
        visual_observations="",
    )

    async def fake_measure(self, video_path, num_frames=12):
        return [217.0, 122.0, 756.0, 169.0], 8.7, "MGBLOOD"

    monkeypatch.setattr(VlmClient, "_measure_banner", fake_measure)
    refined = await VlmClient()._refine_with_banner_measurement(obs, "video.mp4")

    assert refined.screen_percentage == pytest.approx(2.53, abs=0.01)
    assert refined.banner_duration_seconds == 8.7
    assert refined.promo_code == "MGBLOOD"
    assert BannerDefect.TOO_SMALL not in refined.observed_defects
    assert SkycoachRuleEngine.evaluate(refined).deduction_percent == 30


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [
        # Facebook example #20: flush to the top edge, under the app header -> excluded
        ([0, 13, 998, 105], {BannerDefect.CUT_OFF_EDGE, BannerDefect.NOT_VISIBLE}),
        # Paid-in-full Valorant banners, top and bottom placement
        ([161, 83, 843, 194], set()),
        ([163, 783, 837, 868], set()),
        # Runs off the left edge -> logo clipped
        ([0, 400, 600, 480], {BannerDefect.CUT_OFF_EDGE}),
        # Partly under the header
        ([200, 50, 800, 150], {BannerDefect.OVERLAPPED_BY_UI}),
        # Dbghg_3RrsX: grazes the header by 2 of 107 px -> not a defect
        ([163, 78, 838, 185], set()),
        # Reaches into the Shorts/Reels like/comment/share column on the right
        ([600, 600, 980, 700], {BannerDefect.OVERLAPPED_BY_UI}),
        # AVC3rnBrU-g-like: right edge only touches the button column
        ([161, 775, 875, 882], set()),
    ],
)
def test_placement_defects_from_bbox(bbox, expected):
    assert set(SkycoachRuleEngine._placement_defects(bbox)) == expected


def test_parse_observation_rejects_empty_answer():
    """An empty VLM answer raises instead of producing a half-filled observation."""
    with pytest.raises(ValueError):
        VlmClient._parse_observation("{}")


def test_slightly_small_banner_gets_reduced_deduction():
    """DbpiVrpMa1j: 5.5% of the frame is only slightly small -> 20%, not 30%."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=9.0,
        screen_percentage=5.5,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code=None,
        visual_observations="",
    )
    result = SkycoachRuleEngine.evaluate(obs)

    assert result.deduction_percent == 20
    assert BannerDefect.TOO_SMALL.value in result.defects


LABELS_CSV = Path(__file__).resolve().parents[1] / "docs" / "banner_labels.csv"


def _labeled_banners() -> list:
    with LABELS_CSV.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["banner_bbox"] and r["label_deduction"]]
    return [
        pytest.param(
            [float(v) for v in r["banner_bbox"].split()],
            int(r["label_deduction"]),
            id=r["url"].rstrip("/").rsplit("/", 1)[-1],
        )
        for r in rows
    ]


@pytest.mark.parametrize(("bbox", "expected"), _labeled_banners())
def test_rules_match_manual_labels(bbox, expected):
    """Measured banner boxes from real videos must yield the manually labeled deduction."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=8.0,
        screen_percentage=0.0,
        banner_bbox=bbox,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code=None,
        visual_observations="",
    )

    assert SkycoachRuleEngine.evaluate(obs).deduction_percent == expected
