"""
Nualco — Secondary Aluminum Alloy Production Tracker
Streamlit application for batch, chemistry, and yield tracking.
Runs on Neon Postgres (DATABASE_URL) with local SQLite as fallback.
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Nualco Alloy Tracker",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject Streamlit Cloud secrets into the environment BEFORE importing the
# database module, which binds SQLAlchemy's engine at import time.
if not os.environ.get("DATABASE_URL"):
    try:
        if "DATABASE_URL" in st.secrets:
            os.environ["DATABASE_URL"] = str(st.secrets["DATABASE_URL"]).strip().strip('"').strip("'")
    except Exception:
        pass

import database as db  # noqa: E402

# ── Theme tweaks ─────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; max-width: 1200px; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    .yield-ok { color: #1b7a3d; font-weight: 700; font-size: 1.4rem; }
    .yield-bad { color: #c62828; font-weight: 700; font-size: 1.4rem; }
    .batch-id { font-family: ui-monospace, monospace; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def bootstrap() -> bool:
    db.init_db()
    return True


bootstrap()


def df_from_rows(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def photo_bytes(uploaded) -> bytes | None:
    if uploaded is None:
        return None
    return uploaded.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("Nualco")
st.sidebar.caption("Secondary Aluminum Alloy Manufacturing")

PAGE = st.sidebar.radio(
    "Navigate",
    [
        "Dashboard",
        "Raw Material Logging",
        "Production Batch & Chemistry",
        "Production Workflow Tracker",
        "Material Recovery & Yield",
        "Customers",
        "Vendors",
        "Alloys",
        "Furnaces",
        "Bill of Materials",
        "Masters Overview",
    ],
)

st.sidebar.divider()
st.sidebar.markdown(
    f"**Yield target:** {db.YIELD_TARGET_PCT:.0f}%  \n"
    f"**DB:** `{db.DB_LABEL}`"
)
if not db.IS_POSTGRES:
    st.sidebar.error(
        "Not connected to Neon. In Streamlit Cloud go to "
        "**Manage app → Settings → Secrets** and set:\n\n"
        '```\nDATABASE_URL = "postgresql://..."\n```\n\n'
        "Then reboot the app."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════
if PAGE == "Dashboard":
    st.title("Production Dashboard")
    batches = db.list_batches()
    materials = db.list_raw_materials()
    lots = db.list_inventory_lots()
    alloys = db.list_alloys()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Batches", len(batches))
    c2.metric("Raw materials", len(materials))
    c3.metric("Active lots", len(lots))
    c4.metric("Alloys", len(alloys))

    st.subheader("Recent production batches")
    bdf = df_from_rows(batches)
    if bdf.empty:
        st.info("No batches yet. Create one under **Production Batch & Chemistry**.")
    else:
        show = bdf.copy()
        if "Output_Weight" in show.columns and "Weight" in show.columns:
            show["Recovery_%"] = show.apply(
                lambda r: round((r["Output_Weight"] / r["Weight"] * 100), 2)
                if r["Weight"] and r["Output_Weight"] is not None
                else None,
                axis=1,
            )
        st.dataframe(show, use_container_width=True, hide_index=True)

    st.subheader("Inventory on hand")
    idf = df_from_rows(lots)
    if idf.empty:
        st.info("No inventory lots with remaining weight.")
    else:
        st.dataframe(idf, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Raw Material Logging
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Raw Material Logging":
    st.title("Raw Material Logging")
    st.caption(
        "Register a raw material grade, receive a lot (weight & cost), "
        "and capture chemistry (Si, Fe, Cu, Mn, Mg, …)."
    )

    vendors = db.list_vendors()
    vendor_opts = {f"{v['Vendor_name']} (#{v['Vendor_code']})": v["Vendor_code"] for v in vendors}
    if not vendors:
        st.warning("Add at least one vendor under **Vendors** before logging material.")

    with st.form("rm_log_form", clear_on_submit=True):
        st.markdown("#### Material & receipt")
        col_a, col_b = st.columns(2)
        with col_a:
            rm_name = st.text_input(
                "Raw material name *",
                placeholder="e.g. Tense, Taint/Tabor, UBC, Pure Al",
            )
            isri_opts = {
                f"{c['ISRI_CODE']} — {c['Description']}": c["ISRI_CODE"]
                for c in db.list_isri_codes()
            }
            isri_label = st.selectbox("ISRI code", options=[""] + list(isri_opts.keys()))
            alloy_family = st.text_input("Alloy family", placeholder="e.g. Al-Si")
            fe_level = st.selectbox("Fe", [""] + db.ELEMENT_LEVEL)
            cu_level = st.selectbox("Cu", [""] + db.ELEMENT_LEVEL)
            mg_level = st.selectbox("Mg", [""] + db.ELEMENT_LEVEL)
            availability = st.selectbox(
                "Availability class",
                ["Standard", "Spot", "Contract", "Internal"],
            )
            recovery = st.number_input("Expected recovery %", 0.0, 100.0, 95.0, 0.1)
            cost = st.number_input("Cost per kg", min_value=0.0, value=0.0, step=0.01)
        with col_b:
            vendor_label = st.selectbox("Vendor", options=[""] + list(vendor_opts.keys()))
            effective = st.date_input("Effective date", value=date.today())
            received = st.date_input("Received date", value=date.today())
            invoice = st.text_input("Vendor invoice")
            invoice_date = st.date_input("Supplier invoice date", value=date.today())
            weight = st.number_input("Total weight (kg) *", min_value=0.0, value=1000.0, step=1.0)
            storage = st.text_input("Storage bay", placeholder="e.g. Bay-A1")
            inv_status = st.selectbox("Inventory status", db.INVENTORY_STATUS, index=1)
            rm_status = st.selectbox("Master status", db.ACTIVE_STATUS)
            photo = st.file_uploader("Photo", type=["png", "jpg", "jpeg", "webp"])

        st.markdown("#### Chemical composition (%)")
        st.caption("Enter assay percentages for this lot. Common scrap elements shown first.")
        chem_cols = st.columns(6)
        composition: dict[str, float] = {}
        primary = ["Si", "Fe", "Cu", "Mn", "Mg", "Al"]
        for i, sym in enumerate(primary):
            with chem_cols[i]:
                composition[sym] = st.number_input(
                    f"{sym} %", min_value=0.0, max_value=100.0, value=0.0, step=0.01, key=f"chem_{sym}"
                )

        with st.expander("Additional elements"):
            extras = [e for e in db.list_elements() if e["Element_Symbol"] not in primary]
            extra_cols = st.columns(4)
            for i, el in enumerate(extras):
                with extra_cols[i % 4]:
                    composition[el["Element_Symbol"]] = st.number_input(
                        f"{el['Element_Symbol']} ({el['Element_Name']})",
                        min_value=0.0,
                        max_value=100.0,
                        value=0.0,
                        step=0.001,
                        key=f"chem_x_{el['Element_Symbol']}",
                    )

        submitted = st.form_submit_button("Save raw material lot", type="primary")

    if submitted:
        if not rm_name.strip():
            st.error("Raw material name is required.")
        elif weight <= 0:
            st.error("Total weight must be greater than zero.")
        elif not vendors:
            st.error("Create a vendor first.")
        else:
            try:
                vendor_code = vendor_opts[vendor_label] if vendor_label else None
                db.add_raw_material_master(
                    name=rm_name.strip(),
                    effective_date=effective.isoformat(),
                    vendor_code=vendor_code,
                    alloy_family=alloy_family.strip(),
                    availability_class=availability,
                    recovery=recovery,
                    status=rm_status,
                    cost_per_kg=cost,
                    photo=photo_bytes(photo),
                    isri_code=isri_opts[isri_label] if isri_label else None,
                    fe=fe_level or None,
                    cu=cu_level or None,
                    mg=mg_level or None,
                )
                lot_id = db.add_inventory_lot(
                    material=rm_name.strip(),
                    vendor_code=vendor_code,
                    invoice=invoice.strip(),
                    received_date=received.isoformat(),
                    weight=weight,
                    storage_bay=storage.strip(),
                    status=inv_status,
                    photo=photo_bytes(photo),
                    supplier_invoice_date=invoice_date.isoformat(),
                    cost_per_kg=cost,
                )
                cleaned = {k: v for k, v in composition.items() if v and v > 0}
                if cleaned:
                    db.set_lot_chemistry(rm_name.strip(), lot_id, cleaned)
                st.success(
                    f"Saved **{rm_name.strip()}** as Lot **{lot_id}** "
                    f"({weight:,.1f} kg @ {cost:,.2f}/kg)."
                )
                st.cache_data.clear()
            except Exception as exc:
                st.error(f"Could not save: {exc}")

    st.divider()
    st.subheader("Recent inventory lots")
    recent = df_from_rows(
        db.fetch_all(
            """
            SELECT i.Lot_id AS "Lot_id", i.Raw_Material_Name AS "Raw_Material_Name",
                   v.Vendor_name AS "Vendor_name",
                   i.Supplier_Invoice AS "Supplier_Invoice",
                   i.Supplier_invoice_date AS "Supplier_invoice_date",
                   i.Received_date AS "Received_date",
                   i.Received_weight AS "Received_weight",
                   i.Remaining_Weight AS "Remaining_Weight",
                   i.Cost_per_kg AS "Cost_per_kg",
                   i.Storage_bay AS "Storage_bay", i.Status AS "Status"
            FROM Raw_Material_Inventory i
            LEFT JOIN Vendor_Master v ON v.Vendor_code = i.Vendor_code
            ORDER BY i.Lot_id DESC
            LIMIT 50
            """
        )
    )
    if recent.empty:
        st.info("No lots logged yet.")
    else:
        st.dataframe(recent, use_container_width=True, hide_index=True)

    lot_pick = st.number_input("View chemistry for Lot ID", min_value=0, step=1, value=0)
    if lot_pick > 0:
        chem = db.get_lot_chemistry(int(lot_pick))
        if chem:
            st.dataframe(
                pd.DataFrame([{"Element": k, "Percentage": v} for k, v in chem.items()]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning("No chemistry recorded for that lot.")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Production Batch & Chemistry
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Production Batch & Chemistry":
    st.title("Production Batch & Chemistry Input")
    st.caption(
        "Batch ID is automatically built as **F{Furnace}-H{Heat}** "
        "(e.g. Furnace 3 + Heat 7 → `F3-H07`)."
    )

    furnaces = db.list_furnaces()
    alloys = db.list_alloys()
    alloy_labels = {
        f"{a['Alloy_id']} — {a['Alloy_name']}"
        + (f" ({a['Customer_name']})" if a["Customer_name"] else ""): a["Alloy_id"]
        for a in alloys
    }
    materials = db.list_raw_materials()

    if not furnaces:
        st.error("Define at least one furnace under **Furnaces**.")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            furnace = st.selectbox("Furnace *", furnaces)
            heat_no = st.selectbox("Heat no *", db.HEAT_NOS)
            melt_no = st.selectbox("Melt no", db.MELT_NOS)
        with col2:
            prod_date = st.date_input("Production date", value=date.today())
            shift = st.selectbox("Shift", db.SHIFTS)
            melting_team = st.text_input("Melting team")
        with col3:
            alloy_label = st.selectbox(
                "Alloy",
                options=["— none —"] + list(alloy_labels.keys()),
            )
            notes = st.text_area("Notes", height=100)
            preview_id = db.make_batch_id(furnace, heat_no)
            st.markdown(f"**Batch ID preview:** `{preview_id}`")

        alloy_id = None if alloy_label == "— none —" else alloy_labels[alloy_label]

        st.markdown("#### Charge / raw material inputs")
        st.caption("Select one or more lots and the weight charged to this melt.")

        if "charge_lines" not in st.session_state:
            st.session_state.charge_lines = [{"material": "", "lot_id": None, "weight": 0.0, "notes": ""}]

        add_col, rem_col, _ = st.columns([1, 1, 4])
        if add_col.button("Add charge line"):
            st.session_state.charge_lines.append(
                {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
            )
            st.rerun()
        if rem_col.button("Remove last line") and len(st.session_state.charge_lines) > 1:
            st.session_state.charge_lines.pop()
            st.rerun()

        charge_inputs: list[dict] = []
        for idx, line in enumerate(st.session_state.charge_lines):
            st.markdown(f"**Charge line {idx + 1}**")
            lc1, lc2, lc3, lc4 = st.columns([2, 2, 1.2, 2])
            with lc1:
                mat = st.selectbox(
                    "Raw material",
                    options=[""] + materials,
                    key=f"mat_{idx}",
                )
            lots = db.list_inventory_lots(material=mat or None) if mat else []
            lot_opts = {
                f"Lot {l['Lot_id']} — rem {l['Remaining_Weight']:.1f} kg ({l['Status']})": l["Lot_id"]
                for l in lots
            }
            with lc2:
                lot_label = st.selectbox(
                    "Lot",
                    options=[""] + list(lot_opts.keys()),
                    key=f"lot_{idx}",
                )
            with lc3:
                w = st.number_input(
                    "Weight (kg)",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    key=f"wt_{idx}",
                )
            with lc4:
                n = st.text_input("Line notes", key=f"ln_{idx}")

            if mat and lot_label and w > 0:
                charge_inputs.append(
                    {
                        "Raw_Material_Name": mat,
                        "Lot_id": lot_opts[lot_label],
                        "Weight": w,
                        "Notes": n,
                        "Charge_time": datetime.now().isoformat(timespec="seconds"),
                    }
                )

        total_in = sum(c["Weight"] for c in charge_inputs)
        st.info(f"Total input weight: **{total_in:,.2f} kg** across {len(charge_inputs)} charge line(s).")

        st.markdown("#### Batch chemistry (ladle / spectrometer)")
        chem_cols = st.columns(6)
        batch_chem: dict[str, float] = {}
        for i, sym in enumerate(["Si", "Fe", "Cu", "Mn", "Mg", "Al"]):
            with chem_cols[i]:
                batch_chem[sym] = st.number_input(
                    f"{sym} %",
                    min_value=0.0,
                    max_value=100.0,
                    value=0.0,
                    step=0.01,
                    key=f"bchem_{sym}",
                )
        with st.expander("More elements"):
            extras = [e for e in db.list_elements() if e["Element_Symbol"] not in batch_chem]
            xs = st.columns(4)
            for i, el in enumerate(extras):
                with xs[i % 4]:
                    batch_chem[el["Element_Symbol"]] = st.number_input(
                        el["Element_Symbol"],
                        min_value=0.0,
                        max_value=100.0,
                        value=0.0,
                        step=0.001,
                        key=f"bchem_x_{el['Element_Symbol']}",
                    )

        if st.button("Create production batch", type="primary"):
            if not charge_inputs:
                st.error("Add at least one charge line with weight > 0.")
            else:
                try:
                    bid = db.create_batch(
                        furnace=furnace,
                        heat_no=heat_no,
                        alloy_id=alloy_id,
                        production_date=prod_date.isoformat(),
                        shift=shift,
                        melt_no=melt_no,
                        melting_team=melting_team.strip(),
                        notes=notes.strip(),
                        inputs=charge_inputs,
                        composition={k: v for k, v in batch_chem.items() if v and v > 0},
                    )
                    st.success(f"Created batch **{bid}** — Heat {heat_no}, Furnace {furnace}.")
                    st.session_state.charge_lines = [
                        {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
                    ]
                    st.balloons()
                except Exception as exc:
                    st.error(str(exc))

    st.divider()
    st.subheader("Existing batches")
    st.dataframe(df_from_rows(db.list_batches()), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Production Workflow Tracker
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Production Workflow Tracker":
    st.title("Production Workflow Tracker")
    st.caption(
        "Move each batch through: Raw Material → Melting/Furnace → Casting → "
        "Quality Inspection → Finished Goods."
    )

    batches = db.list_batches()
    if not batches:
        st.info("No batches to track yet.")
    else:
        batch_ids = [b["Batch_ID"] for b in batches]
        selected = st.selectbox("Select Batch ID", batch_ids)
        batch = db.get_batch(selected)
        if batch:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Batch ID", batch["Batch_ID"])
            m2.metric("Furnace", batch["Furnace"])
            m3.metric("Heat No", batch["Heat_no"])
            m4.metric("Input kg", f"{batch['Weight'] or 0:,.1f}")

            st.markdown(
                f"**Current stage:** `{batch['Workflow_stage']}` &nbsp;|&nbsp; "
                f"**QA status:** `{batch['Status']}`"
            )

            # Stage progress
            try:
                stage_idx = db.WORKFLOW_STAGES.index(batch["Workflow_stage"])
            except ValueError:
                stage_idx = 0
            prog = (stage_idx + 1) / len(db.WORKFLOW_STAGES)
            st.progress(prog, text=" → ".join(db.WORKFLOW_STAGES))

            with st.form("workflow_form"):
                wc1, wc2, wc3 = st.columns(3)
                with wc1:
                    new_stage = st.selectbox(
                        "Workflow stage",
                        db.WORKFLOW_STAGES,
                        index=stage_idx,
                    )
                with wc2:
                    qa = st.selectbox(
                        "QA / batch status",
                        db.BATCH_QA_STATUS,
                        index=db.BATCH_QA_STATUS.index(batch["Status"])
                        if batch["Status"] in db.BATCH_QA_STATUS
                        else 0,
                    )
                with wc3:
                    out_w = st.number_input(
                        "Output weight (kg) — optional here",
                        min_value=0.0,
                        value=float(batch["Output_Weight"] or 0),
                        step=1.0,
                        help="Also editable on the Yield page at Casting / Finished Goods.",
                    )
                save = st.form_submit_button("Update workflow", type="primary")

            if save:
                out_val = out_w if out_w > 0 else None
                db.update_batch_workflow(
                    selected,
                    workflow_stage=new_stage,
                    qa_status=qa,
                    output_weight=out_val,
                )
                st.success(f"Batch {selected} → **{new_stage}** ({qa}).")
                st.rerun()

            st.subheader("Charge details")
            st.dataframe(
                df_from_rows(db.get_batch_inputs(selected)),
                use_container_width=True,
                hide_index=True,
            )
            st.subheader("Chemistry")
            chem_df = df_from_rows(db.get_batch_chemistry(selected))
            if chem_df.empty:
                st.caption("No chemistry recorded.")
            else:
                st.dataframe(chem_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("All batches by stage")
        overview = df_from_rows(batches)
        if not overview.empty:
            st.dataframe(
                overview[
                    [
                        "Batch_ID",
                        "Production_Date",
                        "Furnace",
                        "Heat_no",
                        "Workflow_stage",
                        "Status",
                        "Weight",
                        "Output_Weight",
                        "Alloy_name",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Material Recovery & Yield
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Material Recovery & Yield":
    st.title("Material Recovery & Yield Calculator")
    st.caption(
        f"Recovery % = (Output Weight / Input Weight) × 100. "
        f"Below **{db.YIELD_TARGET_PCT:.0f}%** is highlighted in red."
    )

    batches = db.list_batches()
    # Prefer batches at casting / finished goods, but allow any
    eligible = [
        b
        for b in batches
        if b["Workflow_stage"] in ("Casting", "Quality Inspection", "Finished Goods")
        or True
    ]
    if not eligible:
        st.info("No batches available.")
    else:
        labels = {
            f"{b['Batch_ID']}  |  stage={b['Workflow_stage']}  |  in={b['Weight'] or 0:.0f} kg": b[
                "Batch_ID"
            ]
            for b in eligible
        }
        pick = st.selectbox("Batch", list(labels.keys()))
        bid = labels[pick]
        batch = db.get_batch(bid)
        assert batch is not None

        input_w = float(batch["Weight"] or 0)
        current_out = float(batch["Output_Weight"] or 0)

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Total input weight (kg)", f"{input_w:,.2f}")
            st.caption("Sum of all charge weights for this batch.")
        with c2:
            output_w = st.number_input(
                "Final output weight (kg)",
                min_value=0.0,
                value=current_out if current_out > 0 else 0.0,
                step=1.0,
            )

        if st.button("Save output & calculate yield", type="primary"):
            # Auto-advance stage if still early and output entered
            stage = batch["Workflow_stage"]
            if output_w > 0 and stage in ("Raw Material", "Melting/Furnace"):
                stage = "Casting"
            db.update_batch_workflow(bid, workflow_stage=stage, output_weight=output_w)
            st.success("Output weight saved.")
            st.rerun()

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

            # Simple visual bar
            bar = min(pct / 100.0, 1.0)
            st.progress(bar, text=f"Recovery {pct:.1f}% (target {db.YIELD_TARGET_PCT:.0f}%)")

        st.divider()
        st.subheader("Yield summary — all batches with output")
        rows = []
        for b in batches:
            if b["Output_Weight"] is None:
                continue
            yw = db.calc_yield(float(b["Weight"] or 0), float(b["Output_Weight"]))
            rows.append(
                {
                    "Batch_ID": b["Batch_ID"],
                    "Furnace": b["Furnace"],
                    "Heat_no": b["Heat_no"],
                    "Stage": b["Workflow_stage"],
                    "Input_kg": yw["input_weight"],
                    "Output_kg": yw["output_weight"],
                    "Recovery_%": round(yw["recovery_pct"], 2),
                    "Flag": "OK" if yw["recovery_pct"] >= db.YIELD_TARGET_PCT else "LOW",
                }
            )
        if rows:
            ydf = pd.DataFrame(rows)
            st.dataframe(ydf, use_container_width=True, hide_index=True)
            low = ydf[ydf["Flag"] == "LOW"]
            if not low.empty:
                st.warning(
                    f"{len(low)} batch(es) below {db.YIELD_TARGET_PCT:.0f}% recovery target."
                )
        else:
            st.caption("No finished yields recorded yet.")


# ═══════════════════════════════════════════════════════════════════════════════
# Customers
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Customers":
    st.title("Customer Master")
    with st.form("cust_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            code = st.text_input("Customer code (PK) *", placeholder="e.g. CUST_0026")
            name = st.text_input("Customer name *")
            gst = st.text_input("GST")
            pan = st.text_input("PAN")
            contact1 = st.text_input("Contact 1 name")
            phone1 = st.text_input("Phone 1")
            contact2 = st.text_input("Contact 2 name")
            phone2 = st.text_input("Phone 2")
            email = st.text_input("Email")
            website = st.text_input("Website")
            status = st.selectbox("Status", db.ACTIVE_STATUS)
        with c2:
            address = st.text_input("Address")
            city = st.text_input("City")
            state = st.text_input("State")
            pincode = st.text_input("Pincode")
            country = st.text_input("Country", value="India")
            bank_account = st.text_input("Bank account")
            ifsc_code = st.text_input("IFSC code")
            bank_name = st.text_input("Bank name")
            branch_category = st.text_input("Branch")
        if st.form_submit_button("Save customer", type="primary"):
            if not code.strip() or not name.strip():
                st.error("Customer code and name are required.")
            else:
                db.upsert_customer(
                    {
                        "Cust_code": code.strip(),
                        "Customer_name": name.strip(),
                        "GST": gst,
                        "PAN": pan,
                        "Address": address,
                        "City": city,
                        "State": state,
                        "Pincode": pincode,
                        "Country": country,
                        "Contact1_name": contact1,
                        "Phone1": phone1,
                        "Contact_name2": contact2,
                        "Phone2": phone2,
                        "Email": email,
                        "Website": website,
                        "Bank_account": bank_account,
                        "IFSC_code": ifsc_code,
                        "Bank_name": bank_name,
                        "Branch_category": branch_category,
                        "Status": status,
                    }
                )
                st.success(f"Saved customer **{name.strip()}**.")

    st.dataframe(
        df_from_rows(db.get_all_records("Customer_Master", order_by="Cust_code")),
        use_container_width=True,
        hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Vendors
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Vendors":
    st.title("Vendor Master")
    st.caption("Vendor code is auto-generated serially when a new vendor is created.")
    with st.form("sup_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Vendor name *")
            gst = st.text_input("GST")
            pan = st.text_input("PAN")
            status = st.selectbox("Status", db.ACTIVE_STATUS)
        with c2:
            address = st.text_input("Address")
            city = st.text_input("City")
            state = st.text_input("State")
            pincode = st.text_input("Pincode")
            country = st.text_input("Country", value="India")

        st.markdown("#### Contacts")
        k1, k2 = st.columns(2)
        with k1:
            contact1 = st.text_input("Contact person 1")
            phone1 = st.text_input("Phone 1")
            email = st.text_input("Email")
        with k2:
            contact2 = st.text_input("Contact person 2")
            phone2 = st.text_input("Phone 2")
            website = st.text_input("Website")

        st.markdown("#### Commercial & bank details")
        b1, b2 = st.columns(2)
        with b1:
            credit_period = st.number_input("Credit period (days)", min_value=0, value=0, step=1)
            bank_account = st.text_input("Bank account no.")
            bank_name = st.text_input("Bank name")
        with b2:
            branch = st.text_input("Branch")
            ifsc = st.text_input("IFSC code")

        if st.form_submit_button("Save vendor", type="primary"):
            if not name.strip():
                st.error("Vendor name is required.")
            else:
                db.upsert_supplier(
                    {
                        "Vendor_name": name.strip(),
                        "GST": gst,
                        "PAN": pan,
                        "Address": address,
                        "City": city,
                        "State": state,
                        "Pincode": pincode,
                        "Country": country,
                        "Contact1": contact1.strip(),
                        "Phone1": phone1.strip(),
                        "Contact2": contact2.strip(),
                        "Phone2": phone2.strip(),
                        "Email": email.strip(),
                        "Website": website.strip(),
                        "Credit_period": int(credit_period),
                        "Bank_account": bank_account.strip(),
                        "Branch": branch.strip(),
                        "IFSC_code": ifsc.strip().upper(),
                        "Bank_name": bank_name.strip(),
                        "Status": status,
                    }
                )
                st.success(f"Saved vendor **{name.strip()}**.")

    st.dataframe(
        df_from_rows(db.get_all_records("Vendor_Master", order_by="Vendor_code")),
        use_container_width=True,
        hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Alloys
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Alloys":
    st.title("Alloy Master & Spec")
    cust_opts = {
        f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"]
        for c in db.list_customer_codes()
    }

    with st.form("alloy_form", clear_on_submit=True):
        a1, a2 = st.columns(2)
        with a1:
            aname = st.text_input("Alloy name *", placeholder="e.g. ADC12, LM6")
            family = st.text_input("Alloy family", placeholder="e.g. Al-Si-Cu")
            created_by = st.text_input("Created by", value="operator")
        with a2:
            customer = st.selectbox("Customer", [""] + list(cust_opts.keys()))
            colour = st.text_input("Colour code", placeholder="e.g. Red, #FF0000")
            bis_desig = st.text_input("BIS designation", placeholder="e.g. IS 617")
            sludge = st.number_input("Sludge factor", min_value=0.0, value=0.0, step=0.01)
            other_each = st.number_input(
                "Other elements each %", min_value=0.0, value=0.0, step=0.01
            )
            other_total = st.number_input(
                "Other elements total %", min_value=0.0, value=0.0, step=0.01
            )
            rev_dt = st.text_input(
                "Revision datetime",
                value=datetime.now().isoformat(timespec="seconds"),
            )
            remarks = st.text_area("Remarks")
            alloy_status = st.selectbox("Status", db.ACTIVE_STATUS)
            st.caption("Spec range (%) for key elements")

        specs: dict[str, tuple[float | None, float | None]] = {}
        for sym in ["Si", "Fe", "Cu", "Mn", "Mg", "Zn", "Ni", "Ti", "Al"]:
            sc1, sc2, sc3 = st.columns([1, 2, 2])
            sc1.markdown(f"**{sym}**")
            mn = sc2.number_input(f"{sym} min", 0.0, 100.0, 0.0, 0.01, key=f"amin_{sym}")
            mx = sc3.number_input(f"{sym} max", 0.0, 100.0, 0.0, 0.01, key=f"amax_{sym}")
            specs[sym] = (mn if mn > 0 else None, mx if mx > 0 else None)

        if st.form_submit_button("Create alloy", type="primary"):
            if not aname.strip():
                st.error("Alloy name is required.")
            else:
                aid = db.add_alloy(
                    cust_code=cust_opts[customer] if customer else None,
                    alloy_name=aname.strip(),
                    family=family.strip(),
                    created_by=created_by.strip(),
                    specs=specs,
                    colour_code=colour.strip() or None,
                    bis_designation=bis_desig.strip() or None,
                    sludge_factor=sludge if sludge > 0 else None,
                    revision_datetime=rev_dt.strip() or None,
                    remarks=remarks.strip() or None,
                    status=alloy_status,
                    other_elements_each=other_each if other_each > 0 else None,
                    other_elements_total=other_total if other_total > 0 else None,
                )
                st.success(f"Created alloy **{aname}** (ID {aid}).")

    st.subheader("Alloys")
    st.dataframe(df_from_rows(db.list_alloys()), use_container_width=True, hide_index=True)

    aid_view = st.number_input("View specs for Alloy ID", min_value=0, step=1, value=0)
    if aid_view > 0:
        specs_df = df_from_rows(
            db.fetch_all(
                'SELECT Element_symbol AS "Element_symbol", Min_percent AS "Min_percent", '
                'Max_percent AS "Max_percent" FROM Alloy_Master_spec WHERE Alloy_id = ?',
                (int(aid_view),),
            )
        )
        st.dataframe(specs_df, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Furnaces
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Furnaces":
    st.title("Furnace Master")
    with st.form("furn_form", clear_on_submit=True):
        fname = st.text_input("Furnace ID *", placeholder="e.g. 1, 2, 3, 4")
        fstatus = st.selectbox("Status", db.ACTIVE_STATUS)
        if st.form_submit_button("Save furnace", type="primary"):
            if not fname.strip():
                st.error("Furnace ID is required.")
            else:
                db.upsert_furnace(fname.strip(), fstatus)
                st.success(f"Saved furnace **{fname.strip()}**.")

    st.dataframe(
        df_from_rows(db.get_all_records("Furnace_Master", order_by="Furnace")),
        use_container_width=True,
        hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BOM
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Bill of Materials":
    st.title("Build of Material (BOM)")
    cust_opts = {
        f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"]
        for c in db.list_customer_codes()
    }
    materials = db.list_raw_materials()
    alloys = [a["Alloy_name"] for a in db.list_alloys()]

    with st.form("bom_form", clear_on_submit=True):
        b1, b2 = st.columns(2)
        with b1:
            bom_id = st.number_input("BOM ID *", min_value=1.0, value=1.0, step=1.0)
            eff = st.date_input("Effective date", value=date.today())
            customer = st.selectbox("Customer", [""] + list(cust_opts.keys()))
            alloy_name = st.selectbox("Alloy name", [""] + alloys)
        with b2:
            rm = st.selectbox("Raw material", [""] + materials)
            qty = st.number_input("Quantity", min_value=0.0, value=1.0, step=0.1)
            seq = st.number_input("Sequence order", min_value=0.0, value=1.0, step=1.0)
            notes = st.text_input("Notes")
        if st.form_submit_button("Save BOM line", type="primary"):
            db.add_bom_line(
                bom_id=bom_id,
                effective_date=eff.isoformat(),
                cust_code=cust_opts[customer] if customer else None,
                alloy_name=alloy_name or None,
                raw_material=rm or None,
                quantity=qty,
                sequence=seq,
                notes=notes,
            )
            st.success(f"Saved BOM {bom_id} / {eff.isoformat()}.")

    st.dataframe(
        df_from_rows(
            db.fetch_all(
                """
                SELECT b.BOMID AS "BOMID", b.Effective_date AS "Effective_date",
                       b.Cust_code AS "Cust_code", c.Customer_name AS "Customer_name",
                       b.Alloy_Name AS "Alloy_Name",
                       b.Raw_Material_Name AS "Raw_Material_Name", b.Quantity AS "Quantity",
                       b.Sequence_Order AS "Sequence_Order", b.notes AS "notes"
                FROM Build_of_Material b
                LEFT JOIN Customer_Master c ON c.Cust_code = b.Cust_code
                ORDER BY b.BOMID, b.Sequence_Order
                """
            )
        ),
        use_container_width=True,
        hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Masters overview
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Masters Overview":
    st.title("Masters Overview")
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Elements", "Raw material master", "Raw material specs", "Schema info"]
    )
    with tab1:
        st.dataframe(df_from_rows(db.list_elements()), use_container_width=True, hide_index=True)
    with tab2:
        st.dataframe(
            df_from_rows(
                db.fetch_all(
                    """
                    SELECT m.Raw_Material_Name AS "Raw_Material_Name",
                           m.Effective_date AS "Effective_date",
                           v.Vendor_name AS "Vendor_name",
                           m.ISRI_CODE AS "ISRI_CODE",
                           m.Alloy_family AS "Alloy_family",
                           m.Fe AS "Fe", m.Cu AS "Cu", m.Mg AS "Mg",
                           m.Availability_class AS "Availability_class",
                           m.Recovery AS "Recovery", m.Cost_per_kg AS "Cost_per_kg",
                           m.Status AS "Status"
                    FROM Raw_Material_Master m
                    LEFT JOIN Vendor_Master v ON v.Vendor_code = m.Vendor_code
                    ORDER BY m.Raw_Material_Name, m.Effective_date DESC
                    """
                )
            ),
            use_container_width=True,
            hide_index=True,
        )
    with tab3:
        st.dataframe(
            df_from_rows(
                db.fetch_all(
                    """
                    SELECT Raw_Material_Name AS "Raw_Material_Name", Lot_id AS "Lot_id",
                           Element_symbol AS "Element_symbol", Percentage AS "Percentage"
                    FROM Raw_Material_Spec
                    ORDER BY Lot_id DESC, Element_symbol
                    LIMIT 200
                    """
                )
            ),
            use_container_width=True,
            hide_index=True,
        )
    with tab4:
        st.markdown(
            """
            **Tables created automatically**

            | # | Table | Purpose |
            |---|-------|---------|
            | 1 | Customer_Master | Customers |
            | 2 | Vendor_Master | Vendors (auto-serial Vendor_code PK) |
            | 3 | Element_Master | 36 chemistry elements (seeded) |
            | 4 | Raw_Material_Master | Material grades |
            | 5 | Raw_Material_Spec | Lot chemistry |
            | 6 | Raw_Material_Inventory | Lots / stock |
            | 7 | Alloy_Master | Alloys |
            | 8 | Alloy_Master_spec | Alloy min/max % |
            | 9 | Furnace_Master | Furnaces (1–4 seeded) |
            | 10 | Production_batch | Melts / heats |
            | 11 | batch_input | Charge sheets |
            | 12 | Batch_Chemical_Composition | Ladle chemistry |
            | 13 | Build_of_Material | BOM |
            | 14 | Purchase_Order | Customer purchase orders |
            | 15 | ISRI_CODE_TABLE | ISRI scrap specification codes |

            Extra production columns: `Workflow_stage`, `Output_Weight`, `Cost_per_kg`.
            """
        )
