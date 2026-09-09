import streamlit as st
import database as db
from pages_common import CHEM_PERCENT_STEP, _column_is_date, _column_is_datetime, _is_blank_date, df_from_rows, format_df_dates, show_dataframe, to_storage_date


def row_dates_to_storage(row: dict) -> dict:
    out = dict(row)
    for key, value in list(out.items()):
        if _column_is_date(key) and not _is_blank_date(value):
            out[key] = to_storage_date(value, with_time=_column_is_datetime(key))
    return out


st.title("Data Browser")
st.caption(
    "View, filter, analyse, and correct master data. "
    "Edit cells in the grid, then click **Save changes**."
)

table_opts = {t["label"]: t for t in db.EDITABLE_TABLES}
t1, t2, t3 = st.columns([2, 2, 1])
with t1:
    chosen_label = st.selectbox("Table", list(table_opts.keys()))
meta = table_opts[chosen_label]
table_key = meta["key"]
pk_cols = meta["pk"]
identity_cols = meta.get("identity") or []

# Reload token so Save / Reset refreshes the grid
state_token = f"data_browser_{table_key}_token"
if state_token not in st.session_state:
    st.session_state[state_token] = 0

rows = db.load_editable_table(table_key, order_by=meta["order_by"])
full_df = df_from_rows(rows)
if table_key == "raw_material_spec" and not full_df.empty:
    sym_col = next(
        (c for c in full_df.columns if c.lower() == "element_symbol"),
        None,
    )
    if sym_col:
        full_df = full_df[
            ~full_df[sym_col]
            .astype(str)
            .str.upper()
            .isin(db.RAW_MATERIAL_SPEC_HIDDEN_SYMBOLS)
        ].copy()
full_df = format_df_dates(full_df)

with t2:
    search = st.text_input(
        "Search",
        placeholder="Filter any column…",
        key=f"search_{table_key}_{st.session_state[state_token]}",
    )
with t3:
    st.metric("Rows", len(full_df))

if full_df.empty:
    st.info(f"No rows in **{chosen_label}** yet.")
else:
    # Quick analysis strip
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Columns", len(full_df.columns))
    status_col = next((c for c in full_df.columns if c.lower() == "status"), None)
    if status_col is not None:
        active_n = int((full_df[status_col].astype(str).str.lower() == "active").sum())
        a2.metric("Active", active_n)
        a3.metric("Other status", len(full_df) - active_n)
    else:
        a2.metric("Primary key", ", ".join(pk_cols))
        a3.metric("Editable", "Yes")
    a4.download_button(
        "Download CSV",
        data=full_df.to_csv(index=False).encode("utf-8"),
        file_name=f"{table_key}.csv",
        mime="text/csv",
    )

    # Optional status / categorical filter
    filter_df = full_df
    filter_cols = [
        c for c in full_df.columns
        if c.lower() in {
            "status", "cust_code", "vendor_code", "isri_code",
            "alloy_id", "availability_class", "fe", "cu", "mg",
        }
    ]
    if filter_cols:
        with st.expander("Column filters", expanded=False):
            fcols = st.columns(min(4, len(filter_cols)))
            for i, col in enumerate(filter_cols):
                opts = sorted({str(v) for v in full_df[col].dropna().unique()})
                with fcols[i % len(fcols)]:
                    picked = st.multiselect(
                        col,
                        opts,
                        default=[],
                        key=f"filt_{table_key}_{col}_{st.session_state[state_token]}",
                    )
                if picked:
                    filter_df = filter_df[filter_df[col].astype(str).isin(picked)]

    if search.strip():
        q = search.strip().lower()
        mask = filter_df.apply(
            lambda r: any(q in str(v).lower() for v in r.values if v is not None),
            axis=1,
        )
        filter_df = filter_df[mask]

    st.caption(
        f"Showing **{len(filter_df)}** of **{len(full_df)}** rows. "
        f"Locked key column(s): `{', '.join(pk_cols)}`."
        + (" New rows: use the dedicated entry pages for auto-IDs." if identity_cols else "")
    )
    if table_key == "employees":
        try:
            st.caption(
                "Leave **employee_id** blank on a new row — the next number "
                f"(**{db.next_employee_id()}**) is assigned on save."
            )
        except Exception:
            pass

    # Keep an unfiltered original snapshot for save (full table)
    original_key = f"data_browser_{table_key}_original_{st.session_state[state_token]}"
    st.session_state[original_key] = full_df.copy()

    disabled = [c for c in pk_cols if c in filter_df.columns]
    column_config = {}
    for c in filter_df.columns:
        if (
            c.lower() in {"percentage", "min_percent", "max_percent"}
            and table_key in {
                "raw_material_spec",
                "batch_chemical_composition",
                "alloy_master_spec",
            }
        ):
            column_config[c] = st.column_config.NumberColumn(
                c, format="%.4f", step=CHEM_PERCENT_STEP
            )
        elif filter_df[c].dtype == object:
            # Keep text columns as text (avoid Streamlit guessing badly)
            column_config[c] = st.column_config.TextColumn(c)

    edited = st.data_editor(
        filter_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic" if meta.get("allow_add") else "fixed",
        disabled=disabled,
        column_config=column_config or None,
        key=f"editor_{table_key}_{st.session_state[state_token]}",
    )

    b1, b2, b3 = st.columns([1, 1, 3])
    with b1:
        save = st.button("Save changes", type="primary", key=f"save_{table_key}")
    with b2:
        reset = st.button("Reset view", key=f"reset_{table_key}")

    if reset:
        st.session_state[state_token] += 1
        st.rerun()

    if save:
        try:
            # Merge edits: apply changes from filtered view onto full original by PK
            original = st.session_state[original_key]
            orig_rows = [
                row_dates_to_storage(r) for r in original.to_dict(orient="records")
            ]
            edited_rows = [
                row_dates_to_storage(r) for r in edited.to_dict(orient="records")
            ]

            # If user filtered, only upsert rows present in the editor
            # (plus any newly added blank-key rows when allow_add)
            result = db.save_table_edits(
                table_name=table_key,
                pk_cols=pk_cols,
                original_rows=orig_rows,
                edited_rows=edited_rows,
                identity_cols=identity_cols,
            )
            st.success(
                f"Saved **{chosen_label}**: "
                f"{result['updated']} updated, {result['inserted']} inserted."
            )
            st.session_state[state_token] += 1
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

    # Simple pivot-style peek for chemistry / spec tables
    if table_key in {"raw_material_spec", "alloy_master_spec"} and not filter_df.empty:
        st.subheader("Quick analysis")
        sym_col = next(
            (c for c in filter_df.columns if "element" in c.lower()),
            None,
        )
        val_col = next(
            (
                c for c in filter_df.columns
                if c.lower() in {"percentage", "min_percent", "max_percent"}
            ),
            None,
        )
        if sym_col:
            serial_order = {
                e["Element_Symbol"]: e["Serial_no"] for e in db.list_elements()
            }
            counts = (
                filter_df[sym_col]
                .value_counts()
                .rename_axis(sym_col)
                .reset_index(name="rows")
            )
            counts["_ord"] = counts[sym_col].map(
                lambda s: serial_order.get(s, 9999)
            )
            counts = counts.sort_values("_ord").drop(columns="_ord")
            c_left, c_right = st.columns(2)
            with c_left:
                st.markdown("**Rows by element**")
                show_dataframe(counts)
            if val_col and val_col in filter_df.columns:
                with c_right:
                    st.markdown(f"**{val_col} summary**")
                    summary = (
                        filter_df.groupby(sym_col)[val_col]
                        .agg(["count", "min", "mean", "max"])
                        .round(3)
                        .reset_index()
                    )
                    summary["_ord"] = summary[sym_col].map(
                        lambda s: serial_order.get(s, 9999)
                    )
                    summary = summary.sort_values("_ord").drop(columns="_ord")
                    show_dataframe(summary)

