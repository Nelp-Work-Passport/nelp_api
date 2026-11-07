"""Plain-language score explanation.

The score is computed elsewhere and is never changed here. Claude only rewords
facts it is given. Any failure, or any figure in its reply that is not in those
facts, falls back to the fixed template.
"""

import logging
import re
from functools import lru_cache

import anthropic

from app.config import get_settings
from app.services.riders import RiderStats

logger = logging.getLogger(__name__)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = (
    "You explain a delivery rider's trust score to customers in Kenya, in two plain "
    "sentences. Use only the facts supplied. Do not add numbers that are not in the "
    "facts. Do not claim the score guarantees safety, identity or a successful delivery."
)


def template(stats: RiderStats) -> str:
    trust = stats.trust
    if trust.is_new:
        return (
            f"This rider is new, with {trust.rated_bookings} rated and "
            f"{stats.completed_deliveries} completed deliveries, so no score is shown yet. "
            "A score appears after 3 rated deliveries."
        )
    return (
        f"This rider scores {trust.trust_score} out of 100 from {trust.rated_bookings} rated "
        f"deliveries averaging {trust.average_stars} stars, with "
        f"{stats.ratings_last_30_days} ratings in the last 30 days. The score summarises "
        "past feedback and does not guarantee a future delivery."
    )


def _facts(stats: RiderStats) -> dict[str, str]:
    trust = stats.trust
    return {
        "trust_score_out_of_100": str(trust.trust_score),
        "rated_deliveries": str(trust.rated_bookings),
        "average_stars": str(trust.average_stars),
        "completed_deliveries": str(stats.completed_deliveries),
        "ratings_in_last_30_days": str(stats.ratings_last_30_days),
    }


@lru_cache(maxsize=512)
def _ask_claude(facts: tuple[tuple[str, str], ...]) -> str:
    """Cached on the facts, so a page refresh costs nothing. Failures are not cached."""
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=10.0, max_retries=1)
    response = client.beta.messages.create(
        model=settings.claude_model,
        max_tokens=2000,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n".join(f"{k}: {v}" for k, v in facts)}],
    )
    if response.stop_reason != "end_turn":
        raise ValueError(f"unusable stop reason: {response.stop_reason}")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ValueError("empty reply")
    return text


def _figures_are_supplied(text: str, facts: dict[str, str]) -> bool:
    allowed = {float(v) for v in facts.values() if _NUMBER_RE.fullmatch(v)} | {100.0}
    return all(float(n) in allowed for n in _NUMBER_RE.findall(text))


def explain(stats: RiderStats) -> tuple[str, str]:
    """Returns (text, source), where source is "claude" or "template"."""
    if not get_settings().anthropic_api_key or stats.trust.is_new:
        return template(stats), "template"
    facts = _facts(stats)
    try:
        text = _ask_claude(tuple(sorted(facts.items())))
    except (anthropic.AnthropicError, ValueError) as exc:
        logger.warning("Score explanation fell back to the template: %s", exc)
        return template(stats), "template"
    if not _figures_are_supplied(text, facts):
        logger.warning("Score explanation had unsupplied figures; using the template")
        return template(stats), "template"
    return text, "claude"
