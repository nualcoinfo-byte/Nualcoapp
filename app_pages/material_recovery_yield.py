import streamlit as st
import database as db
from pages_common import render_batch_output_editor


st.title("Material Recovery & Yield Calculator")
st.caption(
    f"Recovery % = (total recorded output / charge input) × 100. "
    f"Enter outputs on **Batch Output** after the heat is **Completed** "
    f"(product alloy plus Broken Ingot, Furnace Empty, and Not Ok Ingot). "
    f"Below **{db.YIELD_TARGET_PCT:.0f}%** is highlighted in red."
)

batches = db.list_batches()
if not batches:
    st.info("No batches available.")
else:
    labels = {
        f"{b['Batch_ID']}  |  {b.get('Alloy_name') or '—'}  |  "
        f"in={b.get('Input_Weight') or 0:.0f} kg  |  "
        f"out={b.get('Output_Weight') or 0:.0f} kg": b["Batch_ID"]
        for b in batches
    }
    pick = st.selectbox("Batch", list(labels.keys()))
    bid = labels[pick]
    batch = db.get_batch(bid)
    assert batch is not None

    render_batch_output_editor(batch, key_prefix="yield_out")
    batch = db.get_batch(bid)
    assert batch is not None

    input_w = float(batch.get("Input_Weight") or 0)
    output_rows = db.get_batch_outputs(bid)
    output_w = sum(float(r["Weight"] or 0) for r in output_rows)
    product_id = batch.get("Alloy_id")
    product_w = sum(
        float(r["Weight"] or 0)
        for r in output_rows
        if product_id is not None and int(r["Alloy_id"]) == int(product_id)
    )
    sidestream_w = sum(
        float(r["Weight"] or 0)
        for r in output_rows
        if db.is_sidestream_alloy(r["Alloy_id"])
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Charge input (kg)", f"{input_w:,.2f}")
    c2.metric("Product alloy (kg)", f"{product_w:,.2f}")
    c3.metric("Non-spec output (kg)", f"{sidestream_w:,.2f}")
    c4.metric("Total output (kg)", f"{output_w:,.2f}")

    if output_w > 0 and input_w > 0:
        result = db.calc_yield(input_w, output_w)
        pct = result["recovery_pct"]
        ok = pct >= db.YIELD_TARGET_PCT
        css = "yield-ok" if ok else "yield-bad"

        r1, r2, r3 = st.columns(3)
        r1.metric("Input weight", f"{result['input_weight']:,.2f} kg")
        r2.metric("Output weight", f"{result['output_weight']:,.2f} kg")
        r3.metric("Metal loss", f"{result['loss_kg']:,.2f} kg")

        st.markdown(
            f'<p class="{css}">Material recovery rate: {pct:.2f}%</p>',
            unsafe_allow_html=True,
        )
        if ok:
            st.success(f"Yield meets or exceeds the {db.YIELD_TARGET_PCT:.0f}% target.")
        else:
            st.error(
                f"Yield is below the {db.YIELD_TARGET_PCT:.0f}% efficiency target — investigate melt loss."
            )

        bar = min(pct / 100.0, 1.0)
        st.progress(bar, text=f"Recovery {pct:.1f}% (target {db.YIELD_TARGET_PCT:.0f}%)")
    elif input_w <= 0:
        st.info("This batch has no charge input yet.")
    else:
        st.info("Save batch outputs above to calculate recovery.")
