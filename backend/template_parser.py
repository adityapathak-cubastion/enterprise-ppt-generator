# backend/template_parser.py
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import List

from pptx import Presentation  # pip install python-pptx

from .models import (
    TemplateProfile,
    SlideLayoutProfile,
    PlaceholderProfile,
    BrandProfile,
    BBox,
)


def _guess_text_capacity(placeholder_count: int) -> str:
    if placeholder_count == 0:
        return "none"
    if placeholder_count == 1:
        return "medium"
    if placeholder_count >= 2:
        return "high"
    return "none"


def _layout_id(pptx_layout_index: int) -> str:
    return f"layout_{pptx_layout_index}"


def parse_pptx_template(file_path: Path, template_id: str, name: str) -> TemplateProfile:
    print(f"[TEMPLATE_PARSER] Parsing PPTX template: {file_path} (id={template_id})")
    prs = Presentation(file_path)

    slide_layouts: List[SlideLayoutProfile] = []
    for idx, layout in enumerate(prs.slide_layouts):
        placeholders: List[PlaceholderProfile] = []
        text_placeholder_count = 0
        image_placeholder_count = 0

        for ph in layout.placeholders:
            # bounding box
            left = getattr(ph, "left", None)
            top = getattr(ph, "top", None)
            width = getattr(ph, "width", None)
            height = getattr(ph, "height", None)

            bbox = None
            if None not in (left, top, width, height):
                bbox = BBox(
                    x=float(left),
                    y=float(top),
                    width=float(width),
                    height=float(height),
                )

            ph_type = "OTHER"
            if ph.is_placeholder:
                pht = ph.placeholder_format.type
                # Map python-pptx enums to our strings
                name_map = {
                    0: "TITLE",
                    1: "BODY",
                    2: "CENTER_TITLE",
                    3: "SUBTITLE",
                    4: "DATE",
                    5: "SLIDE_NUMBER",
                    6: "FOOTER",
                    7: "HEADER",
                    8: "OBJECT",
                    9: "CHART",
                    10: "TABLE",
                    11: "CLIP_ART",
                    12: "DIAGRAM",
                    13: "MEDIA_CLIP",
                    14: "PICTURE",
                }
                ph_type = name_map.get(int(pht), "OTHER")

            if ph_type in ("TITLE", "BODY", "SUBTITLE"):
                text_placeholder_count += 1
            if ph_type in ("PICTURE", "MEDIA_CLIP"):
                image_placeholder_count += 1

            placeholders.append(
                PlaceholderProfile(
                    placeholder_id=f"ph_{ph.placeholder_format.idx}",
                    pptx_idx=ph.placeholder_format.idx,
                    type=ph_type,
                    name=getattr(ph, "name", None),
                    shape_type=str(getattr(ph, "shape_type", "")),
                    bbox=bbox,
                )
            )

        print(
            f"[TEMPLATE_PARSER] Layout index={idx} name={layout.name!r} "
            f"text_placeholders={text_placeholder_count} "
            f"image_placeholders={image_placeholder_count}"
        )

        layout_profile = SlideLayoutProfile(
            layout_id=_layout_id(idx),
            pptx_layout_index=idx,
            name=layout.name,
            label="UNKNOWN",
            intended_roles=[],
            text_capacity=_guess_text_capacity(text_placeholder_count),
            image_capacity=_guess_text_capacity(image_placeholder_count),
            placeholders=placeholders,
        )
        slide_layouts.append(layout_profile)

    brand_profile = BrandProfile(
        primary_colors=[],
        secondary_colors=[],
        fonts={},
        logo_detected=False,
    )

    pptx_metadata = {
        "slide_master_count": len(prs.slide_masters),
        "layout_count": len(prs.slide_layouts),
    }

    print(
        f"[TEMPLATE_PARSER] Parsed template '{name}' with "
        f"{pptx_metadata['layout_count']} layouts, "
        f"{pptx_metadata['slide_master_count']} slide masters."
    )

    profile = TemplateProfile(
        template_id=template_id,
        name=name,
        source_file_name=file_path.name,
        created_at=datetime.utcnow(),
        pptx_metadata=pptx_metadata,
        brand=brand_profile,
        slide_layouts=slide_layouts,
    )
    return profile
