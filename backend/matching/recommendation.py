from __future__ import annotations

from typing import Any

WEIGHTS = {
    "category": 0.30,
    "budget": 0.20,
    "audience": 0.20,
    "format": 0.15,
    "objective": 0.10,
    "availability": 0.05,
}

CATEGORY_GROUPS = (
    {"fashion", "beauty", "lifestyle"},
    {"food", "travel", "lifestyle"},
    {"technology", "tech", "gaming", "education"},
    {"fitness", "wellness", "health"},
    {"photography", "travel", "arts"},
)


def _normal(value: Any) -> str:
    return str(value or "").strip().casefold()


def _tokens(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {token for item in value for token in _tokens(item)}
    return {part for part in _normal(value).replace(",", " ").replace("/", " ").split() if part}


def _overlap_score(left: Any, right: Any, missing_score: float = 0) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return missing_score
    if left_tokens == right_tokens or _normal(left) == _normal(right):
        return 100
    return round(100 * len(left_tokens & right_tokens) / len(left_tokens | right_tokens))


def budget_score(creator: dict, campaign: dict) -> float:
    minimum = creator.get("minimum_budget")
    maximum = creator.get("maximum_budget")
    campaign_min = campaign.get("minimum_budget")
    campaign_max = campaign.get("maximum_budget")
    if minimum is None or maximum is None or campaign_min is None or campaign_max is None:
        return 0
    if minimum > campaign_max and not campaign.get("allow_negotiation", False):
        return 0
    if minimum > campaign_max:
        return 25
    if maximum < campaign_min:
        return 35
    intersection = max(0, min(maximum, campaign_max) - max(minimum, campaign_min))
    creator_width = max(1, maximum - minimum)
    campaign_width = max(1, campaign_max - campaign_min)
    if minimum >= campaign_min and maximum <= campaign_max:
        return 100
    if intersection:
        return round(65 + 35 * min(intersection / creator_width, intersection / campaign_width))
    return 55


def category_score(creator: dict, campaign: dict) -> float:
    creator_categories = _tokens(creator.get("categories") or creator.get("category"))
    campaign_categories = _tokens(campaign.get("category"))
    if not creator_categories or not campaign_categories:
        return 0
    if creator_categories & campaign_categories:
        return 100
    for group in CATEGORY_GROUPS:
        if creator_categories & group and campaign_categories & group:
            return 60
    return round(100 * len(creator_categories & campaign_categories) / len(creator_categories | campaign_categories))


def score_match(creator: dict, campaign: dict) -> dict:
    category = category_score(creator, campaign)
    audience = _overlap_score(
        creator.get("audience") or creator.get("target_audience"),
        campaign.get("target_audience"),
    )
    formats = _overlap_score(
        creator.get("formats") or creator.get("content_formats"),
        campaign.get("content_format"),
    )
    objective = _overlap_score(
        creator.get("objectives") or creator.get("skills"),
        campaign.get("objective"),
    )
    availability = 100 if creator.get("available") is True else 0
    components = {
        "category": category,
        "budget": budget_score(creator, campaign),
        "audience": audience,
        "format": formats,
        "objective": objective,
        "availability": availability,
    }
    total = round(sum(components[key] * weight for key, weight in WEIGHTS.items()))
    gaps = [key for key in WEIGHTS if components[key] < 50]
    strengths = [key for key in WEIGHTS if components[key] >= 75]
    explanation = (
        "Strong fit for " + ", ".join(strengths) + "."
        if strengths else "Some campaign details align with this creator's profile."
    )
    if gaps:
        explanation += " Consider reviewing: " + ", ".join(gaps) + "."
    return {"score": max(0, min(100, total)), "components": components, "explanation": explanation, "gaps": gaps}


def rank_creators(creators: list[dict], campaign: dict) -> list[dict]:
    ranked = []
    seen = set()
    for creator in creators:
        creator_id = creator.get("id")
        if creator_id in seen:
            continue
        seen.add(creator_id)
        result = score_match(creator, campaign)
        if result["components"]["budget"] == 0:
            continue
        ranked.append({**creator, "match": result})
    return sorted(ranked, key=lambda row: (-row["match"]["score"], str(row.get("name", "")).casefold()))
