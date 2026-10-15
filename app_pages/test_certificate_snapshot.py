"""Test Certificate Snapshot: every packing list's certificate on one screen.

Two uses: a snapshot of what was dispatched (invoice, customer, alloy, kg and
pieces), and clearing certificates left at Pending verification or Verified.
Users were verifying certificates but not clicking Issue, so dispatch looked
unfinished on the PO and finished-goods reports. Verify and Issue here call the
same database functions as the Test Certificate page, so the same rules apply
(printed lines and visual inspection checked on Verify; roles on both).
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

import database as db
from pages_common import format_ui_date, show_dataframe, to_storage_date, ui_date_input

ALL_STATUSES = list(db.CERT_STATUSES)
STUCK = [db.CERT_STATUS_PENDING, db.CERT_STATUS_VERIFIED]
NEXT_STEP = {
    db.CERT_STATUS_PENDING: ("Verify", db.CERT_STATUS_VERIFIED),
    db.CERT_STATUS_VERIFIED: ("Issue", db.CERT_STATUS_ISSUED),
}

st.title("Test Certificate Snapshot")
st.caption(
    "Every packing list's test certificate by **invoice date**: invoice, customer, "
    "P.O., alloy, packed (**source**) and **printed** kg and pieces, and the certificate "
    "status. Certificates left at **Pending verification** or **Verified** can be moved "
    "on here: tick them and use **Verify selected** (Production, Management) or **Issue "
    "selected** (Inventory, Management). The same checks as the **Test Certificate** "
    "page apply: a certificate is verified only when its printed lines and visual "
    "inspection are complete. A certificate counts as dispatched only once it is "
    "**Issued**."
)

flash = st.session_state.pop("tcs_flash", None)
if flash:
    kind, text = flash
    (st.success if kind == "ok" else st.warning)(text)

today = db.today_ist()
f1, f2, f3 = st.columns([1, 1, 2], vertical_alignment="bottom")
with f1:
    start = ui_date_input("Invoice date from", value=today - timedelta(days=89), key="tcs_from")
with f2:
    end = ui_date_input("Invoice date to", value=today, key="tcs_to")
with f3:
    picked_statuses = st.multiselect(
        "Certificate status",
        ALL_STATUSES,
        key="tcs_status",
        placeholder="All statuses",
    )
only_stuck = st.checkbox(
    "Show only certificates waiting for Verify or Issue (any date)",
    key="tcs_only_stuck",
    help="Pending verification and Verified certificates, whatever their invoice date.",
)
if not only_stuck and start and end and start > end:
    st.error("The From date is after the To date.")
    st.stop()

try:
    rows = (
        db.list_certificate_snapshot(statuses=STUCK)
        if only_stuck
        else db.list_certificate_snapshot(start, end, picked_statuses)
    )
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load test certificates: {exc}")
    st.stop()

# Waiting certificates are flagged whatever the filters, so they are not missed.
try:
    waiting = db.list_certificate_snapshot(statuses=STUCK)
except Exception:  # noqa: BLE001
    waiting = []
n_pending = sum(1 for r in waiting if r.get("Status") == db.CERT_STATUS_PENDING)
n_verified = sum(1 for r in waiting if r.get("Status") == db.CERT_STATUS_VERIFIED)
if n_pending or n_verified:
    st.warning(
        f"**{n_pending}** certificate(s) at **Pending verification** and **{n_verified}** "
        "at **Verified** are not Issued yet, so their material does not count as "
        "dispatched. Tick **Show only certificates waiting for Verify or Issue** to list them."
    )

if not rows:
    st.info("No test certificates match these filters.")
    st.stop()

df = pd.DataFrame([dict(r) for r in rows])
for col in ("Source_kg", "Printed_kg", "Source_pieces", "Printed_pieces"):
    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

issued = df[df["Status"] == db.CERT_STATUS_ISSUED]
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Certificates", f"{len(df):,}")
m2.metric("Issued", f"{len(issued):,}")
m3.metric("Dispatched (printed kg)", f"{issued['Printed_kg'].sum():,.0f}",
          help="Printed kg on Issued certificates in this list.")
m4.metric("Dispatched pieces", f"{int(issued['Printed_pieces'].sum()):,}")
m5.metric("Waiting (Pending / Verified)", f"{int(df['Status'].isin(STUCK).sum()):,}")

table = pd.DataFrame({
    "Select": False,
    "Invoice date": df["Invoice_date"].map(lambda v: format_ui_date(v, empty="—")),
    "Invoice number": df["Invoice_number"].fillna(""),
    "Test certificate no": df["Certificate_no"].fillna(""),
    "Customer P.O.": df["Customer_PO_No"].fillna(""),
    "Customer name": df["Customer_name"].fillna(""),
    "Alloy name": df["Alloy_name"].fillna(""),
    "Total source kg": df["Source_kg"],
    "Total printed kg": df["Printed_kg"],
    "Source pieces": df["Source_pieces"].astype(int),
    "Total pieces": df["Printed_pieces"].astype(int),
    "Status": df["Status"],
    "Issued date": df["Issued_date"].map(lambda v: format_ui_date(v, empty="")),
    "Packing list": df["Packing_list_id"].astype(int),
    "Next step": df["Status"].map(lambda s: NEXT_STEP.get(s, ("", ""))[0]),
})
selectable = table["Status"].isin(STUCK)

st.markdown("#### Test certificates")
st.caption(
    "Tick **Select** on certificates at Pending verification or Verified, then use the "
    "buttons below. Ticks on other statuses are ignored. **Total source kg** is what the "
    "packing list took from finished goods; **Total printed kg** is what the "
    "certificate prints (round-up up to 0.15% allowed)."
)
edited = st.data_editor(
    table,
    key=f"tcs_editor_{st.session_state.get('tcs_gen', 0)}",
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    disabled=[c for c in table.columns if c != "Select"],
    column_config={
        "Select": st.column_config.CheckboxColumn("Select", width="small"),
        "Total source kg": st.column_config.NumberColumn(format="%.2f"),
        "Total printed kg": st.column_config.NumberColumn(format="%.2f"),
        "Source pieces": st.column_config.NumberColumn(format="%d"),
        "Total pieces": st.column_config.NumberColumn(format="%d"),
        "Packing list": st.column_config.NumberColumn(format="%d"),
    },
)

chosen = edited[edited["Select"] & selectable]
to_verify = chosen[chosen["Status"] == db.CERT_STATUS_PENDING]
to_issue = chosen[chosen["Status"] == db.CERT_STATUS_VERIFIED]

a1, a2, a3 = st.columns([1.2, 1.2, 1.6], vertical_alignment="bottom")
with a3:
    issue_date = ui_date_input("Issued date (for Issue)", value=today, key="tcs_issue_date")
verify_clicked = a1.button(
    f"Verify selected ({len(to_verify)})",
    type="primary",
    disabled=to_verify.empty,
    key="tcs_verify",
    help="Pending verification → Verified. Needs complete printed lines and visual "
    "inspection; Production, Management or Admin.",
)
issue_clicked = a2.button(
    f"Issue selected ({len(to_issue)})",
    type="primary",
    disabled=to_issue.empty,
    key="tcs_issue",
    help="Verified → Issued (dispatched). Inventory, Management or Admin.",
)


def _run(picked: pd.DataFrame, action, verb: str) -> None:
    done: list[str] = []
    failed: list[str] = []
    for _i, row in picked.iterrows():
        label = row["Test certificate no"] or f"packing list #{row['Packing list']}"
        try:
            action(int(row["Packing list"]))
            done.append(label)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"**{label}**: {exc}")
    parts = []
    if done:
        parts.append(f"{verb} {len(done)} certificate(s): {', '.join(done)}.")
    if failed:
        parts.append(
            f"Not {verb.lower()} ({len(failed)}), fix these on **Test Certificate**:  \n"
            + "  \n".join(failed)
        )
    st.session_state["tcs_flash"] = ("ok" if not failed else "warn", "  \n".join(parts))
    st.session_state["tcs_gen"] = int(st.session_state.get("tcs_gen", 0)) + 1
    st.cache_data.clear()
    st.rerun()


if verify_clicked:
    _run(to_verify, db.verify_packing_list_certificate, "Verified")
if issue_clicked:
    stamp = to_storage_date(issue_date) if issue_date else None
    _run(
        to_issue,
        lambda pid: db.issue_packing_list_certificate(pid, issued_date=stamp),
        "Issued",
    )

export = pd.DataFrame({
    "Invoice date": table["Invoice date"],
    "Invoice number": table["Invoice number"],
    "Test certificate no": table["Test certificate no"],
    "Customer P.O.": table["Customer P.O."],
    "Customer name": table["Customer name"],
    "Alloy name": table["Alloy name"],
    "Total source kg": table["Total source kg"].round(2),
    "Total printed kg": table["Total printed kg"].round(2),
    "Source pieces": table["Source pieces"],
    "Total pieces": table["Total pieces"],
    "Status": table["Status"],
    "Issued date": table["Issued date"],
    "Packing list": table["Packing list"],
})
st.download_button(
    "Download CSV",
    data=export.to_csv(index=False).encode("utf-8"),
    file_name=f"test_certificate_snapshot_{today.isoformat()}.csv",
    mime="text/csv",
    key="tcs_csv",
)

with st.expander("Totals by status"):
    by_status = (
        df.groupby("Status")
        .agg(Certificates=("Packing_list_id", "count"),
             Source_kg=("Source_kg", "sum"), Printed_kg=("Printed_kg", "sum"),
             Pieces=("Printed_pieces", "sum"))
        .reindex([s for s in ALL_STATUSES if s in set(df["Status"])])
        .reset_index()
        .rename(columns={"Source_kg": "Source (kg)", "Printed_kg": "Printed (kg)"})
    )
    show_dataframe(
        by_status,
        column_config={
            "Source (kg)": st.column_config.NumberColumn(format="%.2f"),
            "Printed (kg)": st.column_config.NumberColumn(format="%.2f"),
        },
    )
