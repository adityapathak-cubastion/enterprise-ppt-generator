# backend/models.py
from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# ---------- Template Profile ----------

class BBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class PlaceholderProfile(BaseModel):
    placeholder_id: str
    pptx_idx: int
    type: Literal[
        "TITLE", "SUBTITLE", "BODY", "CENTER_TITLE",
        "DATE", "SLIDE_NUMBER", "FOOTER", "HEADER",
        "OBJECT", "CHART", "TABLE", "CLIP_ART",
        "DIAGRAM", "MEDIA_CLIP", "PICTURE",
        "OTHER"
    ]
    name: Optional[str] = None
    shape_type: Optional[str] = None
    bbox: Optional[BBox] = None



class SlideLayoutProfile(BaseModel):
    layout_id: str
    pptx_layout_index: int
    name: Optional[str] = None
    label: Optional[
        Literal[
            "TITLE_COVER",
            "SECTION_DIVIDER",
            "TEXT_HEAVY",
            "TEXT_MEDIUM",
            "TWO_COLUMN_COMPARISON",
            "IMAGE_WITH_CAPTION",
            "FULL_IMAGE",
            "THANK_YOU",
            "UNKNOWN"
        ]
    ] = "UNKNOWN"
    intended_roles: List[str] = Field(default_factory=list)
    text_capacity: Literal["none", "low", "medium", "high"] = "none"
    image_capacity: Literal["none", "low", "medium", "high"] = "none"
    placeholders: List[PlaceholderProfile] = Field(default_factory=list)


class BrandProfile(BaseModel):
    primary_colors: List[str] = Field(default_factory=list)
    secondary_colors: List[str] = Field(default_factory=list)
    fonts: Dict[str, str] = Field(default_factory=dict)
    logo_detected: bool = False


class TemplateProfile(BaseModel):
    template_id: str
    name: str
    source_file_name: str
    created_at: datetime
    pptx_metadata: Dict[str, Any] = Field(default_factory=dict)
    brand: BrandProfile = Field(default_factory=BrandProfile)
    slide_layouts: List[SlideLayoutProfile] = Field(default_factory=list)


# ---------- Slide Plan ----------

class SlidePlanItem(BaseModel):
    slide_index: int
    role: Literal["cover", "section", "content", "summary", "closing", "other"]
    title: str
    content_type: Optional[str] = None  # e.g. "TEXT_HEAVY", "COMPARISON", etc.
    layout_id: Optional[str] = None
    notes: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class SlidePlan(BaseModel):
    plan_id: str
    template_id: str
    topic: str
    requested_slide_count: int
    final_slide_count: int
    outline_version: int = 1
    slides: List[SlidePlanItem]


# ---------- Generated Deck ----------

class GeneratedSlide(BaseModel):
    slide_index: int
    layout_id: str
    role: Literal["cover", "section", "content", "summary", "closing", "other"]
    title: str
    body_bullets: List[str] = Field(default_factory=list)
    body_paragraphs: List[str] = Field(default_factory=list)
    visual_instructions: List[str] = Field(default_factory=list)
    speaker_notes: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class GeneratedDeck(BaseModel):
    deck_id: str
    plan_id: str
    template_id: str
    topic: str
    created_at: datetime
    slides: List[GeneratedSlide]
