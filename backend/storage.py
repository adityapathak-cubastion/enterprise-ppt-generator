# backend/storage.py
import json
from pathlib import Path
from typing import Type, TypeVar

from .models import TemplateProfile, SlidePlan, GeneratedDeck

T = TypeVar("T", TemplateProfile, SlidePlan, GeneratedDeck)


BASE_DIR = Path("data")
TEMPLATE_DIR = BASE_DIR / "templates"
PLAN_DIR = BASE_DIR / "plans"
DECK_DIR = BASE_DIR / "decks"

for d in [TEMPLATE_DIR, PLAN_DIR, DECK_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def _save_model(model: T, path: Path) -> None:
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _load_model(path: Path, cls: Type[T]) -> T:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return cls.model_validate(data)


def save_template_profile(profile: TemplateProfile) -> Path:
    path = TEMPLATE_DIR / f"{profile.template_id}.json"
    _save_model(profile, path)
    return path


def load_template_profile(template_id: str) -> TemplateProfile:
    path = TEMPLATE_DIR / f"{template_id}.json"
    return _load_model(path, TemplateProfile)


def save_slide_plan(plan: SlidePlan) -> Path:
    path = PLAN_DIR / f"{plan.plan_id}.json"
    _save_model(plan, path)
    return path


def load_slide_plan(plan_id: str) -> SlidePlan:
    path = PLAN_DIR / f"{plan_id}.json"
    return _load_model(path, SlidePlan)


def save_generated_deck(deck: GeneratedDeck) -> Path:
    path = DECK_DIR / f"{deck.deck_id}.json"
    _save_model(deck, path)
    return path


def load_generated_deck(deck_id: str) -> GeneratedDeck:
    path = DECK_DIR / f"{deck_id}.json"
    return _load_model(path, GeneratedDeck)
