# app.py
import io
import uuid
from pathlib import Path

import streamlit as st

from backend.models import TemplateProfile, SlidePlan, GeneratedDeck
from backend.storage import (
    save_template_profile,
    load_template_profile,
    load_generated_deck,
)
from backend.template_parser import parse_pptx_template
from backend.pipeline import run_generation_job
from backend.progress import progress_store
from backend.deck_builder import build_pptx_from_generated_deck


DATA_DIR = Path("data")
TEMPLATE_PPT_DIR = DATA_DIR / "ppt_templates"
TEMPLATE_PPT_DIR.mkdir(parents=True, exist_ok=True)


def init_session_state():
    if "current_template_id" not in st.session_state:
        st.session_state.current_template_id = None
    if "current_deck_id" not in st.session_state:
        st.session_state.current_deck_id = None
    if "current_job_id" not in st.session_state:
        st.session_state.current_job_id = None
    if "last_status" not in st.session_state:
        st.session_state.last_status = {}
    if "topic" not in st.session_state:
        st.session_state.topic = ""
    if "slide_count" not in st.session_state:
        st.session_state.slide_count = 6


def sidebar_navigation() -> str:
    st.sidebar.title("AI Deck Builder (Skeleton)")
    page = st.sidebar.radio(
        "Go to",
        ["Generate Deck", "Manage Templates"],
    )
    return page


def page_generate_deck():
    st.title("Generate Deck from Template (Skeleton)")
    st.write(
        "This is a starter UI. The AI logic is not wired yet; "
        "we're using stub planning and placeholder content."
    )

    # Template uploader
    st.subheader("1. Upload PPT Template")
    uploaded_file = st.file_uploader(
        "Upload a .pptx template",
        type=["pptx"],
        help="This should be your enterprise PPT master or a specific client template.",
    )

    template_id = st.session_state.current_template_id
    template_profile: TemplateProfile | None = None

    if uploaded_file is not None:
        # Save the raw PPTX file
        raw_bytes = uploaded_file.read()
        template_id = f"tpl_{uuid.uuid4().hex[:8]}"
        ppt_path = TEMPLATE_PPT_DIR / f"{template_id}.pptx"
        ppt_path.write_bytes(raw_bytes)

        # Parse into TemplateProfile
        with st.spinner("Parsing template..."):
            profile = parse_pptx_template(
                file_path=ppt_path,
                template_id=template_id,
                name=uploaded_file.name,
            )
            save_template_profile(profile)
            st.session_state.current_template_id = template_id
            template_profile = profile
            st.success(f"Template parsed and saved with id: {template_id}")
    elif template_id:
        # Try to load existing template profile
        try:
            template_profile = load_template_profile(template_id)
            st.info(f"Using previously loaded template: {template_profile.name}")
        except FileNotFoundError:
            st.warning("Previously selected template not found. Please upload again.")
            template_profile = None

    st.markdown("---")

    st.subheader("2. Describe your deck")
    topic = st.text_area(
        "Topic / description",
        value=st.session_state.topic,
        placeholder="e.g. User Manual for Product X",
    )
    st.session_state.topic = topic

    slide_count = st.slider(
        "Approximate number of slides",
        min_value=3,
        max_value=10,
        value=st.session_state.slide_count,
    )
    st.session_state.slide_count = slide_count

    st.caption(
        "For now, this will create a simple stub: cover + content placeholders + closing slide."
    )

    st.markdown("---")

    st.subheader("3. Generate (stub) deck")
    col1, col2 = st.columns([1, 1])

    with col1:
        generate_clicked = st.button(
            "Generate Deck (Stub)",
            disabled=(template_profile is None or not topic.strip()),
            type="primary",
        )

    with col2:
        check_status_clicked = st.button("Check Progress / Refresh")

    if generate_clicked and template_profile is not None:
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        st.session_state.current_job_id = job_id

        # Trigger pipeline synchronously for now
        deck = run_generation_job(
            job_id=job_id,
            template=template_profile,
            topic=topic,
            requested_slide_count=slide_count,
        )
        st.session_state.current_deck_id = deck.deck_id
        st.success(f"Generation finished (stub). Deck ID: {deck.deck_id}")

    # Show progress
    if st.session_state.current_job_id:
        status = progress_store.get(st.session_state.current_job_id)
        if status:
            st.session_state.last_status = status
            st.write(f"**Status:** {status.get('message', '')}")
            st.progress(int(status.get("progress_percent", 0)))
        else:
            st.write("No progress state found yet.")

    elif check_status_clicked:
        st.info("No job started yet.")

    st.markdown("---")
    st.subheader("4. Preview & Download")

    if st.session_state.current_deck_id:
        deck: GeneratedDeck = load_generated_deck(st.session_state.current_deck_id)
        st.write(f"Deck ID: `{deck.deck_id}` (stub content)")

        # Simple tabular preview
        preview_rows = []
        for s in sorted(deck.slides, key=lambda x: x.slide_index):
            preview_rows.append(
                {
                    "Index": s.slide_index,
                    "Role": s.role,
                    "Title": s.title,
                    "Layout ID": s.layout_id,
                    "Example Bullet": s.body_bullets[0] if s.body_bullets else "",
                }
            )
        st.table(preview_rows)

        # Build a real PPTX file from stub content + template
        if template_profile is not None:
            template_ppt_path = TEMPLATE_PPT_DIR / f"{template_profile.template_id}.pptx"
            output_path = DATA_DIR / f"{deck.deck_id}.pptx"

            build_pptx_from_generated_deck(
                template_path=template_ppt_path,
                profile=template_profile,
                deck=deck,
                output_path=output_path,
            )

            with open(output_path, "rb") as f:
                st.download_button(
                    label="Download generated PPTX (Stub)",
                    data=f,
                    file_name=f"{deck.topic.replace(' ', '_')}.pptx",
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
    else:
        st.info("Generate a deck to see preview and download.")


def page_manage_templates():
    st.title("Manage Templates (Skeleton)")

    st.write("This page can later list, inspect, and delete stored templates.")

    # For now, just show the current template ID
    if st.session_state.current_template_id:
        tpl_id = st.session_state.current_template_id
        try:
            profile = load_template_profile(tpl_id)
        except FileNotFoundError:
            st.warning("Current template not found on disk.")
            return

        st.subheader("Current Template Profile")
        st.json(profile.model_dump())
    else:
        st.info("No template loaded in this session yet.")


def main():
    st.set_page_config(
        page_title="AI Deck Builder (Skeleton)",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_session_state()

    page = sidebar_navigation()
    if page == "Generate Deck":
        page_generate_deck()
    else:
        page_manage_templates()


if __name__ == "__main__":
    main()
