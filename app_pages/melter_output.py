import io
from datetime import timedelta

import pandas as pd
import streamlit as st

import database as db
from pages_common import _render_dashboard_refresh_bar, format_ui_date, show_dataframe

ALL_MELTERS = "All melters"
PRESET_LAST_7 = "Last 7 days"
PRESET_LAST_14 = "Last 14 days"
PRESET_THIS_WEEK = "This week (Mon to today)"
PRESET_LAST_WEEK = "Last week (Mon to Sun)"
PRESET_LAST_2_WEEKS = "Last 2 weeks (Mon to Sun)"
PRESET_CUSTOM = "Custom range"

st.title("Melter Output")
st.caption(
    "Output per melting team for paying melters weekly or fortnightly. Pick the "
    "production date range and a melter: the report lists each production date, "
    "shift and furnace with the output by alloy (net kg from **Batch Output**, "
    "including Broken Ingot, Furnace Empty and Not Ok Ingot as their own rows), and "
    "totals per melter and alloy. Download it as CSV or Excel. Data refreshes with "
    "the Dashboard every 4 hours; use **Refresh now** for the latest."
)
_render_dashboard_refresh_bar(key_prefix="melter")

today = db.today_ist()
monday = today - timedelta(days=today.weekday())
f1, f2 = st.columns([1, 2])
with f1:
    preset = st.selectbox(
        "Period",
        [
            PRESET_LAST_7,
            PRESET_LAST_14,
            PRESET_THIS_WEEK,
            PRESET_LAST_WEEK,
            PRESET_LAST_2_WEEKS,
            PRESET_CUSTOM,
        ],
        key="melter_preset",
    )
with f2:
    if preset == PRESET_CUSTOM:
        picked = st.date_input(
            "Production date range",
            value=(today - timedelta(days=6), today),
            format="DD-MM-YYYY",
            key="melter_range",
        )
        start_date, end_date = (
            picked if isinstance(picked, (tuple, list)) and len(picked) == 2 else (None, None)
        )
    else:
        start_date, end_date = {
            PRESET_LAST_7: (today - timedelta(days=6), today),
            PRESET_LAST_14: (today - timedelta(days=13), today),
            PRESET_THIS_WEEK: (monday, today),
            PRESET_LAST_WEEK: (monday - timedelta(days=7), monday - timedelta(days=1)),
            PRESET_LAST_2_WEEKS: (monday - timedelta(days=14), monday - timedelta(days=1)),
        }[preset]
        st.markdown(
            "<p style='font-size:0.875rem;margin-bottom:0.25rem'>Production date range</p>"
            f"<p style='font-size:1.05rem;font-weight:600;margin-top:0.55rem'>"
            f"{format_ui_date(start_date)} to {format_ui_date(end_date)}</p>",
            unsafe_allow_html=True,
        )

if start_date is None or end_date is None:
    st.info("Pick both the start and the end of the date range.")
    st.stop()

teams = db.melter_output_teams(start_date, end_date)
g1, g2 = st.columns([1, 2])
with g1:
    team_opts = [ALL_MELTERS] + teams
    if st.session_state.get("melter_team") not in team_opts:
        st.session_state.pop("melter_team", None)
    team = st.selectbox("Melting team", team_opts, key="melter_team")
with g2:
    st.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
    completed_only = st.checkbox(
        "Only heats whose output is Completed",
        key="melter_completed_only",
        help=(
            "Output is final once it is marked Completed on Batch Output. Tick this "
            "to leave out heats whose output is still a draft."
        ),
    )

rows = db.melter_output_report(
    start_date,
    end_date,
    melting_team=None if team == ALL_MELTERS else team,
    completed_only=completed_only,
)
period = f"{format_ui_date(start_date)} to {format_ui_date(end_date)}"
if not rows:
    st.info(
        f"No output for **{team}** between {period}."
        + ("" if completed_only else " Output is entered on **Batch Output**.")
    )
    st.stop()

raw = pd.DataFrame(rows)
raw["Alloy"] = raw["Alloy_name"].fillna(raw["Alloy_id"].astype(str))
draft_kg = float(raw.loc[raw["Output_status"] != db.BATCH_STATUS_COMPLETED, "Output_kg"].sum())

# One row per team / date / shift / furnace / alloy (heats in different output
# states are added together here).
detail = (
    raw.groupby(
        ["Melting_team", "Production_Date", "Shift", "Furnace", "Alloy"],
        dropna=False,
        as_index=False,
    )[["Heats", "Output_kg", "Pieces"]]
    .sum()
    .sort_values(["Melting_team", "Production_Date", "Shift", "Furnace", "Alloy"])
)
detail_view = pd.DataFrame(
    {
        "Melting team": detail["Melting_team"],
        "Production date": detail["Production_Date"].map(format_ui_date),
        "Shift": detail["Shift"],
        "Furnace": detail["Furnace"],
        "Alloy": detail["Alloy"],
        "Heats": detail["Heats"],
        "Output (kg)": detail["Output_kg"].round(2),
        "Pieces": detail["Pieces"],
    }
)

summary = (
    raw.groupby(["Melting_team", "Alloy"], as_index=False)[["Heats", "Output_kg", "Pieces"]]
    .sum()
    .sort_values(["Melting_team", "Output_kg"], ascending=[True, False])
)
summary_view = pd.DataFrame(
    {
        "Melting team": summary["Melting_team"],
        "Alloy": summary["Alloy"],
        "Heats": summary["Heats"],
        "Output (kg)": summary["Output_kg"].round(2),
        "Pieces": summary["Pieces"],
    }
)
by_team = (
    raw.groupby("Melting_team", as_index=False)[["Output_kg"]]
    .sum()
    .sort_values("Output_kg", ascending=False)
)
team_view = pd.DataFrame(
    {
        "Melting team": by_team["Melting_team"],
        "Total output (kg)": by_team["Output_kg"].round(2),
    }
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total output (kg)", f"{raw['Output_kg'].sum():,.2f}")
# A heat with product and remelt output is on two alloy rows, so heats are not
# summed across alloys here; shifts worked is what the melters' pay covers.
m2.metric(
    "Shifts worked",
    f"{len(raw[['Melting_team', 'Production_Date', 'Shift']].drop_duplicates()):,}",
)
m3.metric("Production days", f"{raw['Production_Date'].nunique():,}")
m4.metric("Melting teams", f"{raw['Melting_team'].nunique():,}")
if draft_kg > 0.005:
    st.caption(
        f"**{draft_kg:,.2f} kg** of this is on heats whose output is not marked "
        "Completed yet, so it can still change. Tick **Only heats whose output is "
        "Completed** to leave it out."
    )

kg = st.column_config.NumberColumn(format="%.2f")
st.subheader(f"Totals by melter and alloy, {period}")
show_dataframe(summary_view, column_config={"Output (kg)": kg})
if team == ALL_MELTERS and len(team_view) > 1:
    st.markdown("**Total per melting team**")
    show_dataframe(team_view, column_config={"Total output (kg)": kg})

st.subheader("Detail by production date, shift and furnace")
show_dataframe(detail_view, column_config={"Output (kg)": kg})

# ── Downloads ─────────────────────────────────────────────────────────────────
slug = "all_melters" if team == ALL_MELTERS else "".join(
    ch if ch.isalnum() else "_" for ch in team
).strip("_")
base_name = f"melter_output_{slug}_{start_date.isoformat()}_{end_date.isoformat()}"
refreshed = db.get_dashboard_last_refreshed()
refreshed_text = (
    format_ui_date(refreshed.astimezone(db.IST), with_time=True) + " IST"
    if refreshed
    else "live data"
)


def _excel_bytes() -> bytes:
    about = pd.DataFrame(
        {
            "Item": [
                "Report",
                "Production dates",
                "Melting team",
                "Output included",
                "Data as of",
                "Generated",
                "Generated by",
            ],
            "Value": [
                "Melter Output",
                period,
                team,
                "Completed output only" if completed_only else "All output (drafts included)",
                refreshed_text,
                format_ui_date(db.now_ist(), with_time=True) + " IST",
                db.get_acting_user(),
            ],
        }
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary_view.to_excel(writer, sheet_name="Totals", index=False)
        if team == ALL_MELTERS:
            team_view.to_excel(writer, sheet_name="Per melter", index=False)
        detail_view.to_excel(writer, sheet_name="Detail", index=False)
        about.to_excel(writer, sheet_name="About", index=False)
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                width = max(len(str(c.value or "")) for c in column) + 2
                sheet.column_dimensions[column[0].column_letter].width = min(width, 50)
    return buf.getvalue()


d1, d2, _sp = st.columns([1, 1, 3])
d1.download_button(
    "Download Excel",
    data=_excel_bytes(),
    file_name=f"{base_name}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="melter_xlsx",
)
d2.download_button(
    "Download CSV",
    data=detail_view.to_csv(index=False).encode("utf-8"),
    file_name=f"{base_name}.csv",
    mime="text/csv",
    key="melter_csv",
    help="The detail table (one row per date, shift, furnace and alloy).",
)
