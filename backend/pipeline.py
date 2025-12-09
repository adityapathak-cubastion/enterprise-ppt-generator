from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
import uuid
import logging

from .models import (
    TemplateProfile,
    SlidePlan,
    SlidePlanItem,
    GeneratedDeck,
    GeneratedSlide,
)
from .storage import save_slide_plan, save_generated_deck
from .progress import progress_store
from .ai_client import chat_completion_json

logger = logging.getLogger(__name__)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


ProgressCallback = Optional[Callable[[int, str], None]]  # (percent, message)


# helper to broadcast progress to store + UI callback
def _report_progress(
    job_id: str,
    step: str,
    step_index: int,
    total_steps: int,
    progress_percent: int,
    message: str,
    cb: ProgressCallback,
) -> None:
    progress_store.update(
        job_id,
        step=step,
        step_index=step_index,
        total_steps=total_steps,
        progress_percent=progress_percent,
        message=message,
    )
    print(
        f"[PIPELINE] job={job_id} step={step} ({step_index}/{total_steps}) "
        f"{progress_percent}% :: {message}"
    )
    if cb:
        cb(progress_percent, message)


# ---------------------------------------------------------------------------
# Helper: layout selection based on content type / role
# ---------------------------------------------------------------------------

def _choose_layout_for_content_type(
    template: TemplateProfile,
    content_type: Optional[str],
    role: str,
) -> str:
    """
    Map a logical content_type + role to a layout_id from TemplateProfile.
    Fallbacks to first layout if no good match is found.
    """

    # 1. Exact label match
    if content_type:
        matches = [
            l for l in template.slide_layouts
            if (l.label or "").upper() == content_type.upper()
        ]
        if matches:
            return matches[0].layout_id

    # 2. Role-based heuristics
    role_lower = role.lower()

    if role_lower == "cover":
        # Prefer TITLE_COVER or SECTION_DIVIDER
        for label in ("TITLE_COVER", "SECTION_DIVIDER"):
            matches = [l for l in template.slide_layouts if (l.label or "").upper() == label]
            if matches:
                return matches[0].layout_id

    if role_lower in ("section", "summary"):
        matches = [
            l for l in template.slide_layouts
            if (l.label or "").upper() in ("SECTION_DIVIDER", "TEXT_MEDIUM")
        ]
        if matches:
            return matches[0].layout_id

    if role_lower in ("closing",):
        matches = [
            l for l in template.slide_layouts
            if (l.label or "").upper() == "THANK_YOU"
        ]
        if matches:
            return matches[0].layout_id

    # 3. Content-type heuristics when we didn't match above
    if content_type:
        ct = content_type.upper()
        if ct in ("TEXT_HEAVY", "TEXT_MEDIUM"):
            candidates = [
                l for l in template.slide_layouts
                if (l.text_capacity in ("medium", "high"))
            ]
            if candidates:
                return candidates[0].layout_id

        if ct in ("TWO_COLUMN_COMPARISON",):
            candidates = [
                l for l in template.slide_layouts
                if "comparison" in (l.name or "").lower()
            ]
            if candidates:
                return candidates[0].layout_id

        if ct in ("IMAGE_WITH_CAPTION", "FULL_IMAGE"):
            candidates = [
                l for l in template.slide_layouts
                if (l.image_capacity in ("medium", "high"))
            ]
            if candidates:
                return candidates[0].layout_id

    # 4. Fallback: just pick first layout
    if template.slide_layouts:
        return template.slide_layouts[0].layout_id

    # Last-resort fallback if template is empty (shouldn't happen)
    return "layout_0"


# ---------------------------------------------------------------------------
# Agent 1: Template Analyzer (LLM refines layout labels / roles)
# ---------------------------------------------------------------------------

def _template_analysis_agent(template: TemplateProfile) -> TemplateProfile:
    """
    Use the LLM to refine layout labels and intended roles
    based on structural info extracted from the PPTX.
    """
    print("[PIPELINE] Starting template analysis agent...")
    layout_descriptions: List[Dict[str, Any]] = []
    for layout in template.slide_layouts:
        text_placeholders = [
            ph for ph in layout.placeholders
            if ph.type in ("TITLE", "BODY", "SUBTITLE", "CENTER_TITLE")
        ]
        image_placeholders = [
            ph for ph in layout.placeholders
            if ph.type in ("PICTURE", "MEDIA_CLIP")
        ]
        layout_descriptions.append(
            {
                "layout_id": layout.layout_id,
                "name": layout.name,
                "pptx_layout_index": layout.pptx_layout_index,
                "text_placeholders": len(text_placeholders),
                "image_placeholders": len(image_placeholders),
                "text_capacity": layout.text_capacity,
                "image_capacity": layout.image_capacity,
            }
        )

    system_prompt = (
        "You are an expert in PowerPoint template design. "
        "You will receive a list of slide layouts with structural information. "
        "For each layout, classify it into one of these labels:\n"
        "- TITLE_COVER\n"
        "- SECTION_DIVIDER\n"
        "- TEXT_HEAVY\n"
        "- TEXT_MEDIUM\n"
        "- TWO_COLUMN_COMPARISON\n"
        "- IMAGE_WITH_CAPTION\n"
        "- FULL_IMAGE\n"
        "- THANK_YOU\n"
        "- UNKNOWN\n\n"
        "Also propose a list of intended roles from:\n"
        "- cover, intro, section, content, summary, closing, other\n\n"
        "Return a JSON object with this shape:\n"
        "{\n"
        '  "layouts": [\n'
        "    {\"layout_id\": \"layout_0\", \"label\": \"TITLE_COVER\", "
        "\"intended_roles\": [\"cover\", \"intro\"]},\n"
        "    ...\n"
        "  ]\n"
        "}"
    )

    user_content = {
        "template_name": template.name,
        "layouts": layout_descriptions,
    }

    try:
        result = chat_completion_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": str(user_content)},
            ],
            temperature=0.0,
        )
    except Exception as e:
        logger.exception("Template analysis agent failed, returning template unchanged: %s", e)
        return template

    layouts_info = result.get("layouts", [])
    mapping: Dict[str, Dict[str, Any]] = {
        item.get("layout_id"): item for item in layouts_info
    }

    for layout in template.slide_layouts:
        info = mapping.get(layout.layout_id)
        if not info:
            continue
        label = info.get("label")
        roles = info.get("intended_roles", [])
        if isinstance(label, str):
            layout.label = label
        if isinstance(roles, list):
            # Make sure all roles are strings
            layout.intended_roles = [str(r) for r in roles]

    print("[PIPELINE] Template analysis agent complete.")
    return template


# ---------------------------------------------------------------------------
# Agent 2: Slide Planner (outline + slide roles + content types)
# ---------------------------------------------------------------------------

def _slide_planner_agent(
    template: TemplateProfile,
    topic: str,
    requested_slide_count: int,
) -> List[SlidePlanItem]:
    """
    Use the LLM to create a high-level slide outline and assign
    roles + content types for each slide.
    """
    print("[PIPELINE] Starting slide planner agent...")
    template_summary = [
        {
            "layout_id": l.layout_id,
            "label": l.label,
            "intended_roles": l.intended_roles,
            "text_capacity": l.text_capacity,
            "image_capacity": l.image_capacity,
        }
        for l in template.slide_layouts
    ]

    system_prompt = (
        "You are an expert presentation designer. "
        "Given a presentation topic, a desired number of slides, and "
        "some information about the available slide layout types in the template, "
        "design a concise, logical slide outline.\n\n"
        "For each slide, you must output:\n"
        "- role: one of [\"cover\", \"section\", \"content\", \"summary\", \"closing\", \"other\"]\n"
        "- title: short, clear title for the slide\n"
        "- content_type: one of [\n"
        "  \"TITLE_COVER\", \"SECTION_DIVIDER\", \"TEXT_HEAVY\", \"TEXT_MEDIUM\",\n"
        "  \"TWO_COLUMN_COMPARISON\", \"IMAGE_WITH_CAPTION\", \"FULL_IMAGE\", \"THANK_YOU\"\n"
        "]\n"
        "- meta: an object that can include \"max_bullets\" (integer) if the slide is text-heavy.\n\n"
        "Constraints:\n"
        "- Start with exactly one cover slide.\n"
        "- End with either a summary or closing slide (e.g. THANK_YOU).\n"
        "- Total number of slides should be close to the requested count, "
        "but you may add or remove 1-2 slides if it improves structure.\n"
        "- Use text-heavy content types when explanation is required.\n"
        "- Use image/visual content types when visuals or diagrams would help.\n\n"
        "Return JSON ONLY in this shape:\n"
        "{\n"
        "  \"slides\": [\n"
        "    {\n"
        "      \"role\": \"cover\",\n"
        "      \"title\": \"...\",\n"
        "      \"content_type\": \"TITLE_COVER\",\n"
        "      \"meta\": {\"max_bullets\": 0}\n"
        "    },\n"
        "    ...\n"
        "  ]\n"
        "}"
    )

    user_content = {
        "topic": topic,
        "requested_slide_count": requested_slide_count,
        "template_layouts": template_summary,
    }

    try:
        result = chat_completion_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": str(user_content)},
            ],
            temperature=0.3,
        )
    except Exception as e:
        logger.exception("Slide planner agent failed: %s", e)
        raise

    slides_data = result.get("slides", [])
    if not isinstance(slides_data, list) or not slides_data:
        raise ValueError("Slide planner agent returned no slides")

    # Convert to SlidePlanItem list, assign slide_index, choose layout_id
    items: List[SlidePlanItem] = []
    for idx, raw_slide in enumerate(slides_data, start=1):
        role = str(raw_slide.get("role", "content"))
        title = str(raw_slide.get("title", f"Slide {idx}")).strip() or f"Slide {idx}"
        content_type = raw_slide.get("content_type") or "TEXT_MEDIUM"
        meta = raw_slide.get("meta") or {}

        layout_id = _choose_layout_for_content_type(
            template=template,
            content_type=content_type,
            role=role,
        )

        item = SlidePlanItem(
            slide_index=idx,
            role=role if role in {"cover", "section", "content", "summary", "closing", "other"} else "content",
            title=title,
            content_type=content_type,
            layout_id=layout_id,
            notes="AI-generated slide plan",
            meta=meta,
        )
        items.append(item)

    print(f"[PIPELINE] Slide planner produced {len(items)} slides.")
    return items


# ---------------------------------------------------------------------------
# Agent 3: Style Guide Generator
# ---------------------------------------------------------------------------

def _style_guide_agent(topic: str) -> Dict[str, Any]:
    """
    Generate a simple style guide for the presentation
    (tone, audience, formality). This is used as context for slide content.
    """
    print(f"[PIPELINE] Generating style guide for topic: {topic!r}")
    system_prompt = (
        "You are defining a short style guide for a presentation deck. "
        "Given the topic, infer the likely audience and appropriate tone.\n\n"
        "Return JSON ONLY in this shape:\n"
        "{\n"
        "  \"voice\": \"...\",\n"
        "  \"audience\": \"...\",\n"
        "  \"reading_level\": \"...\",\n"
        "  \"tone_adjectives\": [\"...\", \"...\"]\n"
        "}"
    )

    user_content = {"topic": topic}

    try:
        result = chat_completion_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": str(user_content)},
            ],
            temperature=0.3,
            max_tokens=256,
        )
    except Exception as e:
        logger.exception("Style guide agent failed, falling back to default: %s", e)
        return {
            "voice": "Professional and concise",
            "audience": "Enterprise stakeholders",
            "reading_level": "Upper intermediate",
            "tone_adjectives": ["clear", "confident", "helpful"],
        }

    # Basic validation + defaults
    style = {
        "voice": result.get("voice", "Professional and concise"),
        "audience": result.get("audience", "Enterprise stakeholders"),
        "reading_level": result.get("reading_level", "Upper intermediate"),
        "tone_adjectives": result.get("tone_adjectives", ["clear", "confident", "helpful"]),
    }
    print("[PIPELINE] Style guide agent complete.")
    return style


# ---------------------------------------------------------------------------
# Agent 4: Slide Content Generator
# ---------------------------------------------------------------------------

def _slide_content_agent(
    slide_item: SlidePlanItem,
    topic: str,
    style_guide: Dict[str, Any],
) -> GeneratedSlide:
    """
    Generate content for a single slide using the LLM.
    """
    print(
        f"[PIPELINE] Generating content for slide {slide_item.slide_index} "
        f"role={slide_item.role} type={content_type}"
    )
    max_bullets = slide_item.meta.get("max_bullets", 5)
    role = slide_item.role
    content_type = slide_item.content_type or "TEXT_MEDIUM"

    system_prompt = (
        "You are an expert presentation copywriter. "
        "You will be given:\n"
        "- The overall deck topic\n"
        "- A style guide\n"
        "- A single slide plan (role, title, content type, optional max bullet count)\n\n"
        "Your job is to write content for that slide ONLY.\n\n"
        "Guidelines:\n"
        "- Keep the slide self-contained and concise.\n"
        "- Use bullets for explanatory or list-style slides.\n"
        "- Use short paragraphs for overview or narrative slides.\n"
        "- If the slide is mainly visual (e.g. content_type includes IMAGE or FULL_IMAGE), "
        "keep text minimal and provide strong visual_instructions.\n"
        "- Bullet texts should be reasonably short (ideally <= 20 words).\n"
        "- Use at most `max_bullets` bullets if provided.\n\n"
        "Return JSON ONLY in this shape:\n"
        "{\n"
        "  \"title\": \"...\",  // may refine the given title but keep it similar\n"
        "  \"body_bullets\": [\"...\", \"...\"],\n"
        "  \"body_paragraphs\": [\"...\"],\n"
        "  \"visual_instructions\": [\"...\", \"...\"],\n"
        "  \"speaker_notes\": \"...\"\n"
        "}\n"
        "You may leave body_paragraphs empty if you focus on bullets, or vice versa, "
        "but not both empty. You may leave visual_instructions empty if the slide is not visual."
    )

    user_content = {
        "deck_topic": topic,
        "style_guide": style_guide,
        "slide": {
            "slide_index": slide_item.slide_index,
            "role": role,
            "title": slide_item.title,
            "content_type": content_type,
            "max_bullets": max_bullets,
        },
    }

    result = chat_completion_json(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": str(user_content)},
        ],
        temperature=0.5,
        max_tokens=800,
    )

    title = result.get("title", slide_item.title) or slide_item.title
    body_bullets = result.get("body_bullets") or []
    body_paragraphs = result.get("body_paragraphs") or []
    visual_instructions = result.get("visual_instructions") or []
    speaker_notes = result.get("speaker_notes")

    # Enforce max bullets
    if max_bullets and isinstance(body_bullets, list):
        body_bullets = body_bullets[:max_bullets]

    slide = GeneratedSlide(
        slide_index=slide_item.slide_index,
        layout_id=slide_item.layout_id or "layout_0",
        role=slide_item.role,
        title=title,
        body_bullets=[str(b).strip() for b in body_bullets if str(b).strip()],
        body_paragraphs=[str(p).strip() for p in body_paragraphs if str(p).strip()],
        visual_instructions=[str(v).strip() for v in visual_instructions if str(v).strip()],
        speaker_notes=str(speaker_notes).strip() if speaker_notes else None,
        meta=slide_item.meta,
    )
    print(f"[PIPELINE] Slide {slide_item.slide_index} content generated.")
    return slide


# ---------------------------------------------------------------------------
# Original stub helpers (kept as fallback)
# ---------------------------------------------------------------------------

def create_basic_slide_plan(
    template: TemplateProfile,
    topic: str,
    requested_slide_count: int,
) -> SlidePlan:
    """
    NON-AI stub for creating a minimal slide plan.
    Just creates: cover + content placeholders + closing slide.
    """
    slides: List[SlidePlanItem] = []

    # cover
    slides.append(
        SlidePlanItem(
            slide_index=1,
            role="cover",
            title=topic,
            content_type="TITLE_COVER",
            layout_id=template.slide_layouts[0].layout_id if template.slide_layouts else None,
            notes="Auto-generated stub cover",
        )
    )

    # simple content placeholders
    content_count = max(1, requested_slide_count - 2)
    for i in range(content_count):
        slides.append(
            SlidePlanItem(
                slide_index=2 + i,
                role="content",
                title=f"Section {i + 1}",
                content_type="TEXT_MEDIUM",
                layout_id=template.slide_layouts[1].layout_id if len(template.slide_layouts) > 1 else None,
                notes="Stub content slide",
                meta={"max_bullets": 5},
            )
        )

    # closing
    closing_index = content_count + 2
    closing_layout_id = (
        template.slide_layouts[-1].layout_id
        if template.slide_layouts
        else None
    )
    slides.append(
        SlidePlanItem(
            slide_index=closing_index,
            role="closing",
            title="Thank You",
            content_type="THANK_YOU",
            layout_id=closing_layout_id,
            notes="Stub closing slide",
        )
    )

    plan = SlidePlan(
        plan_id=_new_id("plan"),
        template_id=template.template_id,
        topic=topic,
        requested_slide_count=requested_slide_count,
        final_slide_count=len(slides),
        slides=slides,
    )
    save_slide_plan(plan)
    return plan


def create_stub_generated_deck(
    plan: SlidePlan,
) -> GeneratedDeck:
    """
    NON-AI stub for generated deck:
    - Adds placeholder bullets based on titles.
    """
    slides: List[GeneratedSlide] = []
    for item in plan.slides:
        bullets: List[str] = []
        if item.role == "content":
            bullets = [
                f"Placeholder bullet 1 for {item.title}",
                f"Placeholder bullet 2 for {item.title}",
            ]

        slide = GeneratedSlide(
            slide_index=item.slide_index,
            layout_id=item.layout_id or "layout_0",
            role=item.role,
            title=item.title,
            body_bullets=bullets,
            body_paragraphs=[],
            visual_instructions=[],
            speaker_notes=None,
            meta=item.meta,
        )
        slides.append(slide)

    deck = GeneratedDeck(
        deck_id=_new_id("deck"),
        plan_id=plan.plan_id,
        template_id=plan.template_id,
        topic=plan.topic,
        created_at=datetime.utcnow(),
        slides=slides,
    )
    save_generated_deck(deck)
    return deck


# ---------------------------------------------------------------------------
# Public entrypoint: AI pipeline with fallback
# ---------------------------------------------------------------------------

def run_generation_job(
    job_id: str,
    template: TemplateProfile,
    topic: str,
    requested_slide_count: int,
    progress_callback: ProgressCallback = None,
) -> GeneratedDeck:
    """
    Entire pipeline:
    - Template analysis (AI)
    - Slide planning (AI)
    - Style guide (AI)
    - Slide content generation (AI)
    - Progress tracking
    - Fallback to stub pipeline if anything fails
    """

    total_steps = 4
    print(
        f"[PIPELINE] run_generation_job started job_id={job_id}, "
        f"topic={topic!r}, requested_slide_count={requested_slide_count}"
    )

    try:
        # Step 1: Template analysis
        _report_progress(
            job_id,
            step="analyze_template",
            step_index=1,
            total_steps=total_steps,
            progress_percent=5,
            message="Analyzing template with AI...",
            cb=progress_callback,
        )
        analyzed_template = _template_analysis_agent(template)

        # Step 2: Slide plan
        _report_progress(
            job_id,
            step="plan_slides",
            step_index=2,
            total_steps=total_steps,
            progress_percent=25,
            message="Designing slide outline with AI...",
            cb=progress_callback,
        )
        slide_items = _slide_planner_agent(
            template=analyzed_template,
            topic=topic,
            requested_slide_count=requested_slide_count,
        )

        plan = SlidePlan(
            plan_id=_new_id("plan"),
            template_id=template.template_id,
            topic=topic,
            requested_slide_count=requested_slide_count,
            final_slide_count=len(slide_items),
            slides=slide_items,
        )
        save_slide_plan(plan)

        # Step 3: Style guide
        _report_progress(
            job_id,
            step="style_guide",
            step_index=3,
            total_steps=total_steps,
            progress_percent=35,
            message="Creating presentation style guide with AI...",
            cb=progress_callback,
        )
        style_guide = _style_guide_agent(topic)

        # Step 4: Slide content generation
        _report_progress(
            job_id,
            step="generate_deck",
            step_index=4,
            total_steps=total_steps,
            progress_percent=40,
            message="Generating slide content with AI...",
            cb=progress_callback,
        )

        slides: List[GeneratedSlide] = []
        total_slides = len(plan.slides)
        for i, item in enumerate(plan.slides, start=1):
            slide = _slide_content_agent(
                slide_item=item,
                topic=topic,
                style_guide=style_guide,
            )
            slides.append(slide)

            progress = 40 + int(50 * i / max(1, total_slides))
            _report_progress(
                job_id,
                step="generate_deck",
                step_index=4,
                total_steps=total_steps,
                progress_percent=min(progress, 95),
                message=f"Generating slide {i}/{total_slides} with AI...",
                cb=progress_callback,
            )

        deck = GeneratedDeck(
            deck_id=_new_id("deck"),
            plan_id=plan.plan_id,
            template_id=plan.template_id,
            topic=plan.topic,
            created_at=datetime.utcnow(),
            slides=slides,
        )
        save_generated_deck(deck)

        _report_progress(
            job_id,
            step="done",
            step_index=4,
            total_steps=total_steps,
            progress_percent=100,
            message="Deck generation finished with AI.",
            cb=progress_callback,
        )
        print(f"[PIPELINE] run_generation_job finished successfully job_id={job_id}")
        return deck

    except Exception as e:
        logger.exception("AI pipeline failed; falling back to stub implementation: %s", e)
        print(f"[PIPELINE] ERROR in AI pipeline: {e}. Falling back to stub.")

        _report_progress(
            job_id,
            step="fallback_stub",
            step_index=1,
            total_steps=1,
            progress_percent=10,
            message="AI pipeline failed; generating stub deck instead...",
            cb=progress_callback,
        )
        plan = create_basic_slide_plan(template, topic, requested_slide_count)
        deck = create_stub_generated_deck(plan)
        _report_progress(
            job_id,
            step="done",
            step_index=1,
            total_steps=1,
            progress_percent=100,
            message="Stub deck generation finished.",
            cb=progress_callback,
        )
        return deck
