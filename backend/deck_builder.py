# backend/deck_builder.py
from __future__ import annotations
from pathlib import Path
from typing import Dict

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from .models import TemplateProfile, GeneratedDeck, GeneratedSlide


def _layout_by_id(prs, profile: TemplateProfile) -> Dict[str, int]:
    return {l.layout_id: l.pptx_layout_index for l in profile.slide_layouts}


def _get_title_placeholder(slide):
    """
    Return the placeholder best suited for title text.
    """
    for shape in slide.placeholders:
        if not shape.is_placeholder:
            continue
        if shape.placeholder_format.type == PP_PLACEHOLDER.TITLE:
            return shape
    # fallback: first text placeholder
    for shape in slide.placeholders:
        if shape.has_text_frame:
            return shape
    return None


def _get_body_placeholder(slide):
    """
    More robust body placeholder selection:
    - Prefer BODY
    - Else largest text-capable placeholder
    """
    candidates = []

    for shape in slide.placeholders:
        if not shape.is_placeholder:
            continue

        pht = shape.placeholder_format.type

        # BODY gets highest priority
        if pht == PP_PLACEHOLDER.BODY:
            return shape

        # If shape can contain text, consider as candidate
        if shape.has_text_frame:
            try:
                area = float(shape.width * shape.height)
            except Exception:
                area = 0
            candidates.append((area, shape))

    if not candidates:
        print("[DECK_BUILDER] No suitable body placeholder found for slide.")
        return None

    # pick largest area
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _safe_set_textframe(text_frame, paragraphs=None, bullets=None):
    """
    Bullet-safe and crash-safe way to populate a text frame.
    """
    text_frame.clear()
    text_frame.text = ""

    if bullets:
        for i, bullet in enumerate(bullets):
            if i == 0:
                text_frame.text = bullet
                text_frame.paragraphs[0].level = 0
            else:
                p = text_frame.add_paragraph()
                p.text = bullet
                p.level = 0
    elif paragraphs:
        for i, para in enumerate(paragraphs):
            if i == 0:
                text_frame.text = para
            else:
                p = text_frame.add_paragraph()
                p.text = para


def build_pptx_from_generated_deck(
    template_path: Path,
    profile: TemplateProfile,
    deck: GeneratedDeck,
    output_path: Path,
) -> Path:
    print(f"[DECK_BUILDER] Building PPTX from deck_id={deck.deck_id} using template={template_path}")
    prs = Presentation(template_path)
    layout_map = _layout_by_id(prs, profile)

    # Clear template sample slides
    while len(prs.slides) > 0:
        r_id = prs.slides._sldIdLst[0].rId
        prs.part.drop_rel(r_id)
        del prs.slides._sldIdLst[0]

    # Build slides
    for slide_data in sorted(deck.slides, key=lambda s: s.slide_index):
        print(
            f"[DECK_BUILDER] Adding slide index={slide_data.slide_index} "
            f"role={slide_data.role} layout_id={slide_data.layout_id}"
        )

        layout_idx = layout_map.get(slide_data.layout_id, None)
        if layout_idx is None:
            print(f"[DECK_BUILDER] Layout {slide_data.layout_id} not found, using layout index 0")
            layout_idx = 0

        layout = prs.slide_layouts[layout_idx]
        slide = prs.slides.add_slide(layout)

        # TITLE
        title_shape = _get_title_placeholder(slide)
        if title_shape is not None:
            try:
                title_shape.text = slide_data.title
            except Exception as e:
                print(f"[DECK_BUILDER] Failed to set title for slide {slide_data.slide_index}: {e}")

        # BODY
        body_shape = _get_body_placeholder(slide)
        if body_shape is not None:
            try:
                tf = body_shape.text_frame
                _safe_set_textframe(
                    tf,
                    paragraphs=slide_data.body_paragraphs,
                    bullets=slide_data.body_bullets,
                )
            except Exception as e:
                print(f"[DECK_BUILDER] Failed to add body text for slide {slide_data.slide_index}: {e}")
        else:
            print(f"[DECK_BUILDER] No body_shape for slide {slide_data.slide_index}")

        # Speaker notes
        if slide_data.speaker_notes:
            try:
                notes_slide = slide.notes_slide
                notes_ph = notes_slide.notes_placeholder
                notes_ph.text = slide_data.speaker_notes
            except Exception as e:
                print(f"[DECK_BUILDER] Could not set speaker notes for slide {slide_data.slide_index}: {e}")

    prs.save(output_path)
    print(f"[DECK_BUILDER] Saved PPTX to {output_path}")
    return output_path
