import streamlit as st
import database as db
from pages_common import df_from_rows, empty_int_input, empty_percent_input, format_ui_date, photo_picker, render_avg_piece_weight, show_dataframe


def _batch_label(b: dict) -> str:
    return (
        f"{b['Batch_ID']}  |  Heat {b.get('Heat_no') or '—'}  |  "
        f"{format_ui_date(b.get('Production_Date'))}, shift {b.get('Shift') or '—'}, "
        f"melt {b.get('Melt_No') or '—'}  |  {b.get('Alloy_name') or '—'}"
    )


def _output_alloy_label(alloy: dict) -> str:
    name = alloy.get("Alloy_name") or f"Alloy {alloy['Alloy_id']}"
    if db.is_sidestream_alloy(alloy["Alloy_id"]):
        return f"{name} (non-spec)"
    return name


st.title("Quick Batch Output")
st.caption(
    "Fast output entry for a heat, made for phones. Choose the furnace, then a batch "
    "whose input is **Completed** and whose output is not completed yet. Each **Save** "
    "adds one weighing to the batch's output. Net weight is **weighment scale − stand**; "
    "enter the stand weight every time (**0** if there was no stand). "
    "When every weighing is in, **Mark Output as Completed** locks the output and "
    "posts it to Finished Goods."
)

flash = st.session_state.pop("_qbo_flash", None)
if flash:
    st.success(flash)

furnaces = db.list_furnaces()
if not furnaces:
    st.error("Define at least one furnace under **Furnaces**.")
    st.stop()

furnace = st.selectbox(
    "Furnace *", furnaces, index=None, placeholder="Select furnace", key="qbo_furnace"
)
if furnace is None:
    st.info("Select a furnace to see its batches waiting for output.")
    st.stop()

waiting = [
    b
    for b in db.list_batches()
    if str(b.get("Furnace")) == str(furnace)
    and b.get("Production_status") == db.BATCH_STATUS_COMPLETED
    and b.get("Output_status") != db.BATCH_STATUS_COMPLETED
]
if not waiting:
    st.info(
        f"No batch on furnace {furnace} is waiting for output. A batch shows here once "
        "its input is marked **Completed** on **Production Batch & Chemistry**, until "
        "its output is marked Completed."
    )
    st.stop()

label_to_id = {_batch_label(b): b["Batch_ID"] for b in waiting}
pick = st.selectbox(
    "Batch *",
    list(label_to_id.keys()),
    index=None,
    placeholder=f"{len(waiting)} batch(es) waiting for output",
    key="qbo_batch",
)
if pick is None:
    st.stop()

batch_id = label_to_id[pick]
batch = db.get_batch(batch_id)
if not batch:
    st.error(f"Batch {batch_id} was not found.")
    st.stop()
saved_lines = db.get_batch_outputs(batch_id)
product_id = batch.get("Alloy_id")
alloys = db.list_batch_output_alloys(product_id)
product_name = next(
    (a["Alloy_name"] for a in alloys if product_id and int(a["Alloy_id"]) == int(product_id)),
    None,
)

input_w = float(batch.get("Input_Weight") or 0)
output_w = sum(float(r.get("Weight") or 0) for r in saved_lines)
with st.container(border=True):
    summary = (
        f"**Batch ID:** `{batch_id}`  \n"
        f"**Heat no:** `{batch.get('Heat_no') or '—'}`  \n"
        f"**Alloy:** {product_name or '—'}  \n"
        f"**Output status:** `{batch.get('Output_status') or db.BATCH_STATUS_IN_PROGRESS}`  \n"
        f"Input **{input_w:,.1f} kg** · output so far **{output_w:,.1f} kg**"
    )
    if input_w > 0 and output_w > 0:
        summary += f" · yield {db.calc_yield(input_w, output_w)['recovery_pct']:.1f}%"
    st.markdown(summary)

if not alloys:
    st.error(
        "Define the batch alloy and non-spec outputs 78 (Broken Ingot), "
        "79 (Furnace Empty), and 80 (Not Ok Ingot) under **Alloys**."
    )
    st.stop()

# Bumping the generation after a save gives every field a fresh key, so the
# form comes back blank for the next weighing.
gen = st.session_state.setdefault("qbo_gen", 0)


def _k(name: str) -> str:
    return f"qbo_{gen}_{name}"


alloy_by_label = {_output_alloy_label(a): int(a["Alloy_id"]) for a in alloys}
labels = list(alloy_by_label.keys())
default_idx = next(
    (i for i, a in enumerate(alloys) if product_id and int(a["Alloy_id"]) == int(product_id)),
    None,
)

st.markdown("#### Add output")
alloy_pick = st.selectbox(
    "Output alloy *",
    labels,
    index=default_idx,
    placeholder="Select output alloy",
    key=_k("alloy"),
)
out_alloy_id = alloy_by_label.get(alloy_pick) if alloy_pick else None

scale_w = empty_percent_input(
    "Weighment scale weight (kg) *", key=_k("scale"), max_value=None, step=1.0
)
stand_w = empty_percent_input(
    "Stand weight (kg) *",
    key=_k("stand"),
    max_value=None,
    step=1.0,
    allow_zero=True,
    help="Enter 0 when the metal was weighed without a stand.",
)
pieces = empty_int_input("Pieces", key=_k("pcs"), help="Whole number of pieces.")

scale_val = float(scale_w or 0)
net_w = max(scale_val - float(stand_w or 0), 0.0) if scale_val > 0 else 0.0
st.info(
    (f"Stand {float(stand_w):,.1f} kg" if stand_w is not None else "Stand —")
    + f"  ·  **Net weight {net_w:,.1f} kg**"
)
render_avg_piece_weight(net_w, pieces, alloy_id=out_alloy_id)

problems: list[str] = []
if scale_val > 0 and stand_w is not None and net_w <= 0:
    problems.append("Weighment scale weight must be more than the stand weight.")
for problem in problems:
    st.error(problem)

# A heat whose output is all Broken Ingot / Furnace Empty / Not Ok Ingot sends
# nothing to Finished Goods; Batch Output asks for the same confirmation.
product_saved = any(
    product_id and int(r["Alloy_id"]) == int(product_id) for r in saved_lines
)
remelt_confirmed = True
if out_alloy_id is not None and db.is_sidestream_alloy(out_alloy_id) and not product_saved:
    st.warning(
        "No **product alloy** output is saved on this heat yet, and this line is a "
        "remelt type. Remelt goes back to raw material stock, not Finished Goods. "
        + (
            f"If this metal is **{product_name}**, change the output alloy first."
            if product_name
            else "This batch has **no alloy** set; set it on Production Batch & "
            "Chemistry (Correct history) first."
        )
    )
    remelt_confirmed = st.checkbox(
        "Yes, this weighing really is remelt material.", key=_k("remelt_ok")
    )

scale_photo = photo_picker("📷 Weighment scale photo", _k("wsp"))
output_photo = photo_picker("📷 Output photo", _k("out"))

if st.button("Save output line", type="primary", use_container_width=True, key=_k("save")):
    missing = [
        name
        for name, ok in (
            ("Output alloy", out_alloy_id is not None),
            ("Weighment scale weight", scale_val > 0),
            ("Stand weight (0 if none)", stand_w is not None),
        )
        if not ok
    ]
    if missing:
        st.error("Enter " + ", ".join(missing) + ".")
    elif problems or net_w <= 0:
        st.error("Fix the weight above before saving.")
    elif not remelt_confirmed:
        st.error("Not saved: tick the remelt confirmation, or change the output alloy.")
    else:
        try:
            db.add_batch_output_line(
                batch_id,
                {
                    "Alloy_id": out_alloy_id,
                    "Weight": net_w,
                    "Weighment_scale_weight": scale_val,
                    "Stand_weight": stand_w,
                    "Pieces": pieces,
                    "Notes": "",
                    "Weighment_scale_photo": scale_photo,
                    "Output_photo": output_photo,
                },
            )
            st.session_state["_qbo_flash"] = (
                f"Saved **{net_w:,.1f} kg** of **{alloy_pick}** to batch **{batch_id}**."
            )
            st.session_state["qbo_gen"] = gen + 1
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

st.markdown("#### Saved output lines")
if not saved_lines:
    st.caption("No output saved on this batch yet.")
else:
    show_dataframe(
        df_from_rows(
            [
                {
                    "Time": format_ui_date(r.get("Output_time"), with_time=True),
                    "Output alloy": r.get("Alloy_name"),
                    "Net (kg)": float(r.get("Weight") or 0),
                    "Pieces": r.get("Pieces"),
                    "Stand (kg)": r.get("Stand_weight"),
                }
                for r in reversed(saved_lines)
            ]
        ),
        column_config={
            "Net (kg)": st.column_config.NumberColumn(format="%.1f"),
            "Stand (kg)": st.column_config.NumberColumn(format="%.1f"),
        },
    )


@st.dialog("Mark output as Completed?")
def _confirm_complete_output(batch_id: str, total_kg: float, n_lines: int, remelt_only: bool) -> None:
    st.markdown(
        f"Batch **{batch_id}**: **{n_lines}** output line(s), **{total_kg:,.1f} kg**"
        + (f", yield **{db.calc_yield(input_w, total_kg)['recovery_pct']:.1f}%**" if input_w > 0 else "")
        + "."
    )
    st.caption(
        "This locks the heat's output and posts it to **Finished Goods Inventory**. "
        "Only an Admin can unlock it afterwards."
    )
    confirmed = True
    if remelt_only:
        st.warning(
            "**Every saved output line is a remelt type**, so nothing will reach "
            "Finished Goods. If the heat produced the product alloy, cancel and "
            "fix the output on **Batch Output**."
        )
        confirmed = st.checkbox(
            "Yes, this heat's entire output really is remelt material.",
            key=f"qbo_complete_remelt_ok_{batch_id}",
        )
    ok_col, cancel_col = st.columns(2)
    if ok_col.button("Confirm", type="primary", use_container_width=True, disabled=not confirmed):
        try:
            db.complete_batch_output(batch_id)
        except Exception as exc:
            st.error(str(exc))
            return
        st.session_state["_qbo_flash"] = (
            f"Output for **{batch_id}** is **Completed** and locked. It is now "
            "posted to **Finished Goods Inventory**."
        )
        # The batch drops out of the waiting list; clear the pick so the list starts fresh.
        st.session_state.pop("qbo_batch", None)
        st.rerun()
    if cancel_col.button("Cancel", use_container_width=True):
        st.rerun()


if saved_lines:
    saved_total = sum(float(r.get("Weight") or 0) for r in saved_lines)
    saved_remelt_only = not product_saved
    if st.button(
        "Mark Output as Completed",
        type="primary",
        use_container_width=True,
        key=f"qbo_complete_{batch_id}",
        help="Locks this heat's output and posts it to Finished Goods Inventory.",
    ):
        _confirm_complete_output(batch_id, saved_total, len(saved_lines), saved_remelt_only)
else:
    st.caption("**Mark Output as Completed** appears once an output line is saved.")
