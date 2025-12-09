# app.py
import io
import uuid
import hashlib
from pathlib import Path

import streamlit as st

from backend.models import TemplateProfile, GeneratedDeck
from backend.storage import (
    save_template_profile,
    load_template_profile,
    load_generated_deck,
)
from backend.template_parser import parse_pptx_template
from backend.pipeline import run_generation_job
from backend.deck_builder import build_pptx_from_generated_deck


DATA_DIR = Path("data")
TEMPLATE_PPT_DIR = DATA_DIR / "ppt_templates"
TEMPLATE_PPT_DIR.mkdir(parents=True, exist_ok=True)


def init_session_state():
    if "current_template_id" not in st.session_state:
        st.session_state.current_template_id = None
    if "current_deck_id" not in st.session_state:
        st.session_state.current_deck_id = None
    if "topic" not in st.session_state:
        st.session_state.topic = ""
    if "slide_count" not in st.session_state:
        st.session_state.slide_count = 6


def sidebar_navigation() -> str:
    st.sidebar.title("AI Deck Builder")
    page = st.sidebar.radio(
        "Go to",
        ["Generate Deck", "Manage Templates"],
    )
    return page


def _make_short_ppt_name(topic: str) -> str:
    """
    Create a short, filesystem-safe name from the topic.
    """
    base = (topic or "").strip().replace("\n", " ")
    if not base:
        return "generated_deck"
    words = base.split()
    short_words = words[:6]
    short = "_".join(short_words)
    short = "".join(ch for ch in short if ch.isalnum() or ch in ("_", "-"))
    if not short:
        short = "generated_deck"
    return short[:60]


def page_generate_deck():
    st.title("Generate Deck from Template")
    st.write(
        "Upload your enterprise PPT template and describe the deck you want. "
        "The system will analyze the template and generate a matching deck."
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
        raw_bytes = uploaded_file.read()
        # Use file hash as stable template_id so we don't spam data/ with duplicates
        tpl_hash = hashlib.md5(raw_bytes).hexdigest()[:8]
        template_id = f"tpl_{tpl_hash}"
        ppt_path = TEMPLATE_PPT_DIR / f"{template_id}.pptx"
        ppt_path.write_bytes(raw_bytes)
        print(f"[APP] Saved uploaded template to {ppt_path} (id={template_id})")

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
        placeholder="e.g. Deep Research for QM-PQR – how it generates detailed reports vs simple RAG answers",
    )
    st.session_state.topic = topic

    slide_count = st.slider(
        "Approximate number of slides",
        min_value=3,
        max_value=10,
        value=st.session_state.slide_count,
    )
    st.session_state.slide_count = slide_count

    st.caption("We’ll generate a structured deck with a cover, content slides and a closing slide.")

    st.markdown("---")

    st.subheader("3. Generate deck")
    status_placeholder = st.empty()
    progress_bar = st.progress(0)

    generate_clicked = st.button(
        "Generate Deck",
        disabled=(template_profile is None or not topic.strip()),
        type="primary",
    )

    if generate_clicked and template_profile is not None:
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        print(f"[APP] Generate button clicked. job_id={job_id}")

        def ui_progress(percent: int, message: str) -> None:
            print(f"[APP] UI progress update: {percent}% - {message}")
            status_placeholder.write(f"**Status:** {message}")
            progress_bar.progress(percent)

        with st.spinner("Generating deck with AI..."):
            deck = run_generation_job(
                job_id=job_id,
                template=template_profile,
                topic=topic,
                requested_slide_count=slide_count,
                progress_callback=ui_progress,
            )
        st.session_state.current_deck_id = deck.deck_id
        st.success(f"Generation finished. Deck ID: {deck.deck_id}")

    st.markdown("---")
    st.subheader("4. Preview & Download")

    if st.session_state.current_deck_id:
        deck: GeneratedDeck = load_generated_deck(st.session_state.current_deck_id)
        st.write(f"Deck ID: `{deck.deck_id}`")

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

        if template_profile is not None:
            template_ppt_path = TEMPLATE_PPT_DIR / f"{template_profile.template_id}.pptx"
            output_path = DATA_DIR / f"{deck.deck_id}.pptx"

            print(f"[APP] Building final PPTX to {output_path}")
            build_pptx_from_generated_deck(
                template_path=template_ppt_path,
                profile=template_profile,
                deck=deck,
                output_path=output_path,
            )

            short_name = _make_short_ppt_name(deck.topic)
            with open(output_path, "rb") as f:
                st.download_button(
                    label="Download generated PPTX",
                    data=f,
                    file_name=f"{short_name}.pptx",
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
    else:
        st.info("Generate a deck to see preview and download.")


def page_manage_templates():
    st.title("Manage Templates")

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
        page_title="AI Deck Builder",
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
