import json

import streamlit as st
import database as db

# Reference example for the background job queue infrastructure
# (database.py: ocr_extraction_job table, start_ocr_job_worker, the
# _ocr_job_* dispatch machinery). Deliberately NOT added to
# MIGRATED_PAGES/NAV_SECTIONS in app.py, so it stays invisible to real
# users until a real OCR/data-science handler replaces the stub in
# database.py's _OCR_JOB_HANDLERS and someone decides to wire this in.
#
# The pattern this demonstrates:
#   1. Upload -> one fast INSERT (status='pending') -> UI unlocks immediately.
#   2. A background thread (already running for the whole process, started
#      once at boot) picks the job up, runs it, and stamps the result.
#   3. A polling fragment re-queries just the job's row on an interval,
#      isolated from the rest of the page, until it reaches a terminal state.


st.title("OCR job queue (reference example)")
st.caption(
    "Not linked from the sidebar - demonstrates the background job queue "
    "pattern in database.py (ocr_extraction_job / start_ocr_job_worker) "
    "for future OCR/data-science work. The 'processing' step is a stub."
)

uploaded = st.file_uploader(
    "Document to process",
    type=["png", "jpg", "jpeg", "pdf"],
    key="ocr_demo_upload",
)
if st.button(
    "Submit for processing",
    type="primary",
    disabled=uploaded is None,
    key="ocr_demo_submit",
):
    job_id = db.create_ocr_extraction_job(
        uploaded.name,
        uploaded.getvalue(),
        uploaded_by=db.get_acting_employee_id(),
    )
    st.session_state["ocr_demo_job_id"] = job_id
    st.success(f"Queued job #{job_id}. Processing happens in the background.")
    st.rerun()


@st.fragment(run_every="3s")
def _poll_ocr_job_status() -> None:
    job_id = st.session_state.get("ocr_demo_job_id")
    if not job_id:
        return
    job = db.get_ocr_extraction_job(job_id)
    if job is None:
        st.warning(f"Job #{job_id} not found.")
        return

    st.markdown(f"#### Job #{job_id}")
    status = job["status"]
    if status in ("pending", "processing"):
        st.info(f"Status: **{status}** - checking again in a few seconds.")
    elif status == "done":
        st.success("Status: **done**")
        result = json.loads(job["result_json"]) if job["result_json"] else {}
        st.json(result)
    elif status == "failed":
        st.error(f"Status: **failed** - {job.get('error_message') or 'unknown error'}")


if st.session_state.get("ocr_demo_job_id"):
    _poll_ocr_job_status()
