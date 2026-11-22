"""Which model an agent runs on, where it does not name one.

An agent names a model, or a class and a strength: the class is what the model is for
(``allround``, ``coding``, ``vision``, or one the installation adds), the strength how large it is
(``SIZES``, ``ModelInfo.tier``). The setting ``model_classes`` holds, per class, a score from 0 to
100 for each model that belongs to it; the user keeps it (Settings, Model classes). Nothing here is
measured while agents work.

One rule (``choose``) answers both questions: which model of its own provider an agent without a
model runs on, and which model of another provider stands in for it (``equivalent_model``): an
agent is given another provider (``agents``), or runs on another one while its own is used up
(``accounts.route``). Among the provider's models of the class, the one of that strength; of several
the one with the highest score; if there is none of that strength the next smaller one, so that
nothing runs on more than it was given.

The scores can be filled once from Arena's public leaderboards (``arena``): only fields that are
empty, and only for models whose provider says what Arena calls them (``ModelInfo.arena``).
"""

from __future__ import annotations

from typing import Any, Protocol

from plainboard_protocol import ProviderInfo

SIZES = ("small", "medium", "large", "xlarge")  # ModelInfo.tier, in order
DEFAULT_CLASS = "allround"
DEFAULT_STRENGTH = "medium"
SUMMARY_STRENGTH = "small"
# The classes every installation has, and the Arena leaderboard each is filled from.
FIXED = {"allround": "text", "coding": "webdev", "vision": "vision"}

Classes = dict[str, dict[str, dict[str, float]]]  # class -> provider -> model -> score (0..100)


def clean(value: Any) -> Classes:
    """The setting as it is stored: the fixed classes always, names trimmed, scores within 0..100.
    Raises ValueError for anything that is not class -> provider -> model -> number."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Model classes: expected classes with their models' scores")
    out: Classes = {name: {} for name in FIXED}
    for name, providers in value.items():
        name = str(name).strip().lower()
        if not name or len(name) > 40:
            raise ValueError("Model classes: a class needs a name of at most 40 characters")
        if not isinstance(providers, dict):
            raise ValueError(f"Model classes: '{name}' needs its models' scores per provider")
        out[name] = {}
        for provider, scores in providers.items():
            if not isinstance(scores, dict):
                raise ValueError(f"Model classes: '{name}' needs its models' scores per provider")
            kept = {}
            for model, score in scores.items():
                if score is None or score == "":
                    continue
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
                    raise ValueError(f"Model classes: the score of {model} in '{name}' must be a number from 0 to 100")
                kept[str(model)] = round(float(score), 1)
            if kept:
                out[name][str(provider)] = kept
    return out


def known(classes: Classes | None, name: str) -> bool:
    return name in FIXED or name in (classes or {})


def size_of(catalog: list[ProviderInfo], model: str) -> str:
    """The size of a model, whichever provider lists it. One nobody lists counts as medium."""
    return next((m.tier for p in catalog for m in p.models if m.id == model), DEFAULT_STRENGTH)


def choose(provider: ProviderInfo, strength: str, scores: dict[str, float] | None = None) -> str | None:
    """The provider's model of that strength. ``scores``: the class's scores of this provider's
    models; with them only those models count and the highest score wins among several of one
    strength, without them (a class nobody filled for this provider) every model counts and the
    first listed wins. No model of that strength: the next smaller one; the smallest, if all are
    larger. None if the provider has no models."""
    scored = {m.id: scores[m.id] for m in provider.models if scores and m.id in scores}
    best: dict[str, str] = {}
    for m in provider.models:
        if scored and m.id not in scored:
            continue
        if m.tier not in best or scored.get(m.id, 0) > scored.get(best[m.tier], 0):
            best[m.tier] = m.id
    at = SIZES.index(strength) if strength in SIZES else SIZES.index(DEFAULT_STRENGTH)
    return next((best[size] for size in (*reversed(SIZES[: at + 1]), *SIZES[at + 1 :]) if size in best), None)


def _scores(classes: Classes | None, model_class: str | None, provider: str) -> dict[str, float] | None:
    return ((classes or {}).get(model_class or DEFAULT_CLASS) or {}).get(provider)


def equivalent_model(catalog: list[ProviderInfo], model: str, target: str, classes: Classes | None = None, model_class: str | None = None) -> str | None:
    """The target provider's model that stands in for ``model``, which may belong to any provider:
    ``model`` itself if the target has it, else the one of its size (``choose``, within
    ``model_class`` if scores are given). None if the target is unknown or has no models."""
    provider = next((p for p in catalog if p.name == target), None)
    if provider is None or not provider.models:
        return None
    if any(m.id == model for m in provider.models):
        return model
    return choose(provider, size_of(catalog, model), _scores(classes, model_class, target))


class Chooser(Protocol):
    """What of an agent decides its models (``models.Agent``)."""

    provider: str
    model: str
    summary_model: str
    model_class: str | None
    strength: str | None
    summary_strength: str | None


def models_of(agent: Chooser, catalog: list[ProviderInfo], classes: Classes | None, provider: str, summary_strength: str | None = None) -> tuple[str, str]:
    """The model and the summary model the agent runs on with ``provider``: its own or another one.
    A model the agent names holds on its own provider and gives the strength on another one; without
    one, class and strength decide on both. Summaries are always ``allround``; ``summary_strength``
    is the installation's, for an agent that names none. "" where nothing can be chosen (the
    providers cannot be read)."""
    target = next((p for p in catalog if p.name == provider), None)
    own = provider == agent.provider

    def one(named: str, strength: str, model_class: str | None) -> str:
        if named and (own or target is None):
            return named
        if target is None:
            return ""
        if named:
            return equivalent_model(catalog, named, provider, classes, model_class) or named
        return choose(target, strength, _scores(classes, model_class, provider)) or ""

    return (
        one(agent.model, agent.strength or DEFAULT_STRENGTH, agent.model_class),
        one(agent.summary_model, agent.summary_strength or summary_strength or SUMMARY_STRENGTH, DEFAULT_CLASS),
    )


def label(agent: Chooser) -> str:
    """What the agent runs on, in one word for lists: its model, or its class and strength."""
    return agent.model or f"{agent.model_class or DEFAULT_CLASS}/{agent.strength or DEFAULT_STRENGTH}"


def fill(classes: Classes | None, catalog: list[ProviderInfo], ratings: dict[str, dict[str, float]]) -> Classes:
    """``classes`` with the empty scores of the fixed classes filled from Arena's ratings
    (class -> Arena's model name -> rating): a score the user gave stays. A rating becomes 0..100
    within its leaderboard. A model takes the best rating among Arena's entries that carry its name
    (``ModelInfo.arena``; Arena lists one model several times, per effort: "Name (High)")."""
    out = clean(classes)
    for name, rated in ratings.items():
        if name not in FIXED or not rated:
            continue
        low, high = min(rated.values()), max(rated.values())
        for provider in catalog:
            for model in provider.models:
                if not model.arena or model.id in out[name].get(provider.name, {}):
                    continue
                found = [r for entry, r in rated.items() if entry == model.arena or entry.startswith(model.arena + " (")]
                if found:
                    score = 100.0 if high == low else (max(found) - low) / (high - low) * 100
                    out[name].setdefault(provider.name, {})[model.id] = round(score, 1)
    return out
