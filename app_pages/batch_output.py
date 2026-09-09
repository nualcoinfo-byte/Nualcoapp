import streamlit as st
import database as db
from pages_common import df_from_rows, format_ui_date, render_batch_output_editor, show_dataframe


st.title("Batch Output")
st.caption(
    "Enter metal that left a heat into **batch_output**. "
    "Each batch can have more than one output line: the product alloy selected "
    "on the batch, plus **Broken Ingot (78)**, **Furnace Empty (79)**, and "
    "**Not Ok Ingot (80)** for samples and portions taken out so they do not "
    "spoil the chemistry. Those three have no spec. "
    "Create and mark the batch **Completed** first on **Production Batch & Chemistry**. "
    "Output lines can be saved as drafts and edited freely until you click "
    "**Mark Output as Completed**, which locks this heat's output and posts it "
    "into **Finished Goods Inventory** — only Completed output reaches Finished "
    "Goods. Dispatch those bundles on **Packing List**. "
    "Avg piece weight is net output ÷ pieces; product alloy pieces are typically "
    f"{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg and show in red if outside that range. "
    "On save, each output line stores material ₹/kg (charge lot cost ÷ total output kg) "
    "and overall ₹/kg (material + Cost of Conversion for the production month, "
    "or the previous available month if that month's rates are not in yet). "
    "Broken Ingot, Furnace Empty, and Not Ok Ingot are also stored as remelt lots "
    "in **Raw Material Inventory**, linked to this heat so they can be charged later."
)

batches = db.list_batches()
if not batches:
    st.info("No batches yet. Create one under **Production Batch & Chemistry**.")
else:
    furnace_opts = ["All furnaces"] + sorted(
        {str(b["Furnace"]) for b in batches if b.get("Furnace")}
    )
    furnace_filter = st.selectbox("Furnace", furnace_opts, key="bo_furnace")
    filtered = [
        b
        for b in batches
        if furnace_filter == "All furnaces" or str(b.get("Furnace")) == furnace_filter
    ]
    if not filtered:
        st.info("No batches for this furnace.")
    else:
        labels = {
            f"{b['Batch_ID']}  |  Shift {b.get('Shift') or '—'}  |  "
            f"Melt {b.get('Melt_No') or '—'}  |  {b.get('Alloy_name') or '—'}  |  "
            f"in={float(b.get('Input_Weight') or 0):.0f} kg  |  "
            f"out={float(b.get('Output_Weight') or 0):.0f} kg": b["Batch_ID"]
            for b in filtered
        }
        pick = st.selectbox("Batch *", list(labels.keys()), key="bo_batch")
        bid = labels[pick]
        batch = db.get_batch(bid)
        if not batch:
            st.error(f"Batch {bid} was not found.")
        else:
            alloy_name = next(
                (b.get("Alloy_name") for b in filtered if b["Batch_ID"] == bid),
                None,
            )
            m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)
            m1.metric("Batch ID", batch["Batch_ID"])
            m2.metric("Shift", batch.get("Shift") or "—")
            m3.metric("Melt", batch.get("Melt_No") or "—")
            m4.metric("Furnace", batch.get("Furnace") or "—")
            m5.metric("Heat No", batch.get("Heat_no") or "—")
            m6.metric(
                "Product alloy",
                f"{batch.get('Alloy_id')} — {alloy_name or '—'}",
            )
            m7.metric(
                "Production status",
                batch.get("Production_status") or "—",
            )
            m8.metric(
                "Output status",
                batch.get("Output_status") or "—",
            )
            st.caption(
                f"Charge input: {float(batch.get('Input_Weight') or 0):,.2f} kg"
            )

            with st.expander("Charge input lines"):
                charges = db.get_batch_inputs(bid)
                if charges:
                    show_dataframe(df_from_rows(charges))
                else:
                    st.caption("No charge lines on this batch.")

            render_batch_output_editor(batch, key_prefix="bo_page")

            # Re-read so the yield reflects outputs just saved above.
            batch = db.get_batch(bid) or batch
            input_w = float(batch.get("Input_Weight") or 0)
            output_w = float(batch.get("Output_Weight") or 0)
            st.markdown("#### Batch yield")
            y1, y2, y3 = st.columns(3)
            y1.metric("Total input (kg)", f"{input_w:,.2f}")
            y2.metric("Total output (kg)", f"{output_w:,.2f}")
            with y3:
                st.markdown(
                    '<p class="avg-piece-label">Yield %</p>', unsafe_allow_html=True
                )
                if input_w <= 0:
                    st.markdown(
                        '<p class="avg-piece">—</p>', unsafe_allow_html=True
                    )
                elif output_w <= 0:
                    st.markdown(
                        '<p class="avg-piece">—</p>', unsafe_allow_html=True
                    )
                else:
                    pct = db.calc_yield(input_w, output_w)["recovery_pct"]
                    css = (
                        "yield-ok" if pct >= db.YIELD_TARGET_PCT else "yield-bad"
                    )
                    st.markdown(
                        f'<p class="{css}">{pct:.2f}%</p>', unsafe_allow_html=True
                    )
            if input_w <= 0:
                st.caption(
                    "No charge input on this heat yet — add it on "
                    "**Production Batch & Chemistry**."
                )
            elif output_w <= 0:
                st.caption("Save output lines above to see this heat's yield.")
            else:
                st.caption(
                    f"Total output ÷ total input for **{bid}**. "
                    f"Melt loss {input_w - output_w:,.2f} kg. "
                    f"Target {db.YIELD_TARGET_PCT:.0f}%."
                )

            day_label = format_ui_date(batch.get("Production_Date")) or bid[:6]
            st.subheader(f"Saved outputs for {day_label}")
            saved_day = df_from_rows(
                db.list_all_batch_outputs(date_prefix=bid[:6])
            )
            if saved_day.empty:
                st.info("No output rows yet for this day.")
            else:
                show_dataframe(saved_day, highlight_avg_piece=True)

