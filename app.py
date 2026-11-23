import calendar
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import quote
import pandas as pd
import streamlit as st
from fpdf import FPDF

st.set_page_config(
    page_title="Lone Wolf Field Portal",
    page_icon="🐺",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Mobile-first CSS: keeps the 7-column calendar grid on 1 row on Android screens and styles cards/buttons
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 680px; }

    /* Force 7-column calendar rows to stay side-by-side on mobile phones instead of stacking */
    div[data-testid="stHorizontalBlock"]:has(div[data-testid="stColumn"]:nth-child(7)) {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 4px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(div[data-testid="stColumn"]:nth-child(7)) > div[data-testid="stColumn"] {
        flex: 1 1 0% !important;
        min-width: 0 !important;
        width: auto !important;
    }

    /* Calendar day buttons */
    div[data-testid="stHorizontalBlock"]:has(div[data-testid="stColumn"]:nth-child(7)) button {
        min-height: 2.6rem !important;
        padding: 0.15rem 0 !important;
        font-size: 0.9rem !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        width: 100% !important;
    }

    /* Highlight selected calendar dates in green */
    div[data-testid="stHorizontalBlock"]:has(div[data-testid="stColumn"]:nth-child(7)) button[kind="primary"] {
        background-color: #16a34a !important;
        border-color: #15803d !important;
        color: #ffffff !important;
    }

    /* Main action buttons */
    div[data-testid="stDownloadButton"] button {
        min-height: 3.2rem;
        font-size: 1.05rem;
        font-weight: 700;
        border-radius: 10px;
        width: 100%;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Master rate card (40 items grouped by Account -> Location -> Service, with street fragments for DMG Pro matching)
RATE_CARD = [
    {"id": "bb_mex_swp", "account": "Best Buy", "location": "Mexico", "service": "Sweeping", "rate": 30.00, "addr_keys": ["4100 e mexico"]},
    {"id": "bb_mex_qc", "account": "Best Buy", "location": "Mexico", "service": "QC", "rate": 7.00, "addr_keys": ["4100 e mexico"]},
    {"id": "bb_wad_swp", "account": "Best Buy", "location": "Wadsworth", "service": "Sweeping", "rate": 30.00, "addr_keys": ["5395 s wadsworth"]},
    {"id": "bb_wad_qc", "account": "Best Buy", "location": "Wadsworth", "service": "QC", "rate": 7.00, "addr_keys": ["5395 s wadsworth"]},
    {"id": "hr_prt", "account": "Highlands Ranch / Safeway", "location": "Highlands Ranch", "service": "Porter", "rate": 50.00, "addr_keys": ["9255 s broadway"]},
    {"id": "hr_saf_swp", "account": "Highlands Ranch / Safeway", "location": "Highlands Ranch Safeway", "service": "Sweeping", "rate": 50.00, "addr_keys": ["9255 s broadway"]},
    {"id": "ks_ark_swp", "account": "King Soopers", "location": "Arkansas", "service": "Sweeping", "rate": 40.00, "addr_keys": ["4201 e arkansas"]},
    {"id": "ks_ark_qc", "account": "King Soopers", "location": "Arkansas", "service": "QC", "rate": 7.00, "addr_keys": ["4201 e arkansas"]},
    {"id": "ks_ark_blk", "account": "King Soopers", "location": "Arkansas", "service": "Bulk Trash", "rate": 500.00, "addr_keys": ["4201 e arkansas"]},
    {"id": "ks_eva_swp", "account": "King Soopers", "location": "Evans", "service": "Sweeping", "rate": 40.00, "addr_keys": ["2727 w evans"]},
    {"id": "ks_eva_qc", "account": "King Soopers", "location": "Evans", "service": "QC", "rate": 7.00, "addr_keys": ["2727 w evans"]},
    {"id": "ks_fed_swp", "account": "King Soopers", "location": "Federal", "service": "Sweeping", "rate": 40.00, "addr_keys": ["5050 s federal"]},
    {"id": "ks_fed_qc", "account": "King Soopers", "location": "Federal", "service": "QC", "rate": 7.00, "addr_keys": ["5050 s federal"]},
    {"id": "ks_ham_qc", "account": "King Soopers", "location": "Hampden", "service": "QC", "rate": 5.00, "addr_keys": ["6470 e hampden"]},
    {"id": "ks_lee_swp", "account": "King Soopers", "location": "Leetsdale", "service": "Sweeping", "rate": 40.00, "addr_keys": ["4600 leetsdale"]},
    {"id": "ks_lee_qc", "account": "King Soopers", "location": "Leetsdale", "service": "QC", "rate": 7.00, "addr_keys": ["4600 leetsdale"]},
    {"id": "ks_lit_qc", "account": "King Soopers", "location": "Littleton", "service": "QC", "rate": 5.00, "addr_keys": ["100 w littleton"]},
    {"id": "ks_lit_prt", "account": "King Soopers", "location": "Littleton", "service": "Porter", "rate": 7.00, "addr_keys": ["100 w littleton"]},
    {"id": "ks_qui_qc", "account": "King Soopers", "location": "Quincy", "service": "QC", "rate": 5.00, "addr_keys": ["19001 e quincy"]},
    {"id": "ks_sho_prt", "account": "King Soopers", "location": "S Holly", "service": "Porter", "rate": 7.00, "addr_keys": ["6000 south holly", "6000 s holly"]},
    {"id": "ko_20m_swp", "account": "Kohl's", "location": "20 Mile", "service": "Sweeping", "rate": 35.00, "addr_keys": ["11485 s twenty mile"]},
    {"id": "ko_20m_qc", "account": "Kohl's", "location": "20 Mile", "service": "QC", "rate": 7.00, "addr_keys": ["11485 s twenty mile"]},
    {"id": "ko_cas_swp", "account": "Kohl's", "location": "Castle Rock", "service": "Sweeping", "rate": 35.00, "addr_keys": ["4800 milestone"]},
    {"id": "ko_cas_qc", "account": "Kohl's", "location": "Castle Rock", "service": "QC", "rate": 7.00, "addr_keys": ["4800 milestone"]},
    {"id": "ko_kip_swp", "account": "Kohl's", "location": "Kipling", "service": "Sweeping", "rate": 35.00, "addr_keys": ["8444 s kipling"]},
    {"id": "ko_kip_qc", "account": "Kohl's", "location": "Kipling", "service": "QC", "rate": 7.00, "addr_keys": ["8444 s kipling"]},
    {"id": "ko_que_swp", "account": "Kohl's", "location": "Quebec", "service": "Sweeping", "rate": 35.00, "addr_keys": ["8660 s quebec"]},
    {"id": "ko_que_qc", "account": "Kohl's", "location": "Quebec", "service": "QC", "rate": 7.00, "addr_keys": ["8660 s quebec"]},
    {"id": "ko_bus_trh", "account": "Kohl's", "location": "All Kohl's", "service": "Bus Trash", "rate": 15.00, "addr_keys": ["kohl"]},
    {"id": "lo_20m_swp", "account": "Lowe's", "location": "20 Mile", "service": "Sweeping", "rate": 50.00, "addr_keys": ["10000 s twenty mile"]},
    {"id": "lo_buc_swp", "account": "Lowe's", "location": "Buckley", "service": "Sweeping", "rate": 50.00, "addr_keys": ["4455 south buckley", "4455 s buckley"]},
    {"id": "lo_pea_swp", "account": "Lowe's", "location": "Peakview", "service": "Sweeping", "rate": 50.00, "addr_keys": ["9100 east peakview", "9100 e peakview"]},
    {"id": "lo_san_swp", "account": "Lowe's", "location": "Santa Fe", "service": "Sweeping", "rate": 50.00, "addr_keys": ["5095 s santa fe"]},
    {"id": "lo_whe_swp", "account": "Lowe's", "location": "Wheatlands", "service": "Sweeping", "rate": 50.00, "addr_keys": ["24505 e wheatlands"]},
    {"id": "sc_wad_swp", "account": "Sam's Club", "location": "Wadsworth", "service": "Sweeping", "rate": 50.00, "addr_keys": ["4827 s wadsworth"]},
    {"id": "wg_ara_swp", "account": "Walgreens", "location": "Arapahoe", "service": "Sweeping", "rate": 25.00, "addr_keys": ["16440 e arapahoe"]},
    {"id": "wg_bel_swp", "account": "Walgreens", "location": "Belleview", "service": "Sweeping", "rate": 25.00, "addr_keys": ["2697 w belleview"]},
    {"id": "wg_hgr_swp", "account": "Walgreens", "location": "Highlands Ranch", "service": "Sweeping", "rate": 25.00, "addr_keys": ["9390 s colorado"]},
    {"id": "wg_hgr_qc", "account": "Walgreens", "location": "Highlands Ranch", "service": "QC", "rate": 7.00, "addr_keys": ["9390 s colorado"]},
    {"id": "wg_kip_swp", "account": "Walgreens", "location": "Kipling", "service": "Sweeping", "rate": 25.00, "addr_keys": ["5870 s kipling"]},
]

# Denver Route Stops from Addresses for routing.xlsx (with approximate lat/lon for nearest-neighbor route sorting)
HOME_BASE = {
    "name": "Home Base (Lakewood)",
    "address": "2345 Allison St, Lakewood, CO 80214",
    "lat": 39.7517,
    "lon": -105.0861,
}

ROUTE_STOPS = [
    {"id": "r_hr_saf", "store": "Highlands Ranch Safeway", "address": "9255 S Broadway, Highlands Ranch, CO 80129", "line": "Dual", "flex": "NO", "days": ["Mon", "Wed", "Fri"], "lat": 39.5481, "lon": -104.9877},
    {"id": "r_ks_ham", "store": "King Soopers - Hampden", "address": "6470 E Hampden Ave, Denver, CO 80222", "line": "Dual", "flex": "NO", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "lat": 39.6534, "lon": -104.9126},
    {"id": "r_ks_eva", "store": "King Soopers - Evans", "address": "2727 W Evans Ave, Denver, CO 80219", "line": "Sweep", "flex": "NO", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "lat": 39.6787, "lon": -105.0216},
    {"id": "r_ks_sho", "store": "King Soopers - S Holly", "address": "6000 S Holly St, Greenwood Village, CO 80111", "line": "Dual", "flex": "NO", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "lat": 39.6075, "lon": -104.9225},
    {"id": "r_ks_fed", "store": "King Soopers - Federal", "address": "5050 S Federal Blvd, Englewood, CO 80110", "line": "Sweep", "flex": "NO", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "lat": 39.6248, "lon": -105.0253},
    {"id": "r_ks_lee", "store": "King Soopers - Leetsdale", "address": "4600 Leetsdale Dr, Glendale, CO 80246", "line": "Sweep", "flex": "NO", "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "lat": 39.7011, "lon": -104.9336},
    {"id": "r_ks_ark", "store": "King Soopers - Arkansas (2x/wk)", "address": "4201 E Arkansas Ave, Denver, CO 80222", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.6908, "lon": -104.9383},
    {"id": "r_ks_qui", "store": "King Soopers - Quincy (1x/wk)", "address": "19001 E Quincy Ave, Aurora, CO 80015", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.6384, "lon": -104.7663},
    {"id": "r_ks_lit", "store": "King Soopers - Littleton", "address": "100 W Littleton Blvd, Littleton, CO 80120", "line": "Dual", "flex": "NO", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "lat": 39.6135, "lon": -105.0177},
    {"id": "r_bb_mex", "store": "Best Buy - Mexico", "address": "4100 E Mexico Ave Ste A, Denver, CO 80222", "line": "Sweep", "flex": "NO", "days": ["Tue", "Thu"], "lat": 39.6836, "lon": -104.9398},
    {"id": "r_bb_wad", "store": "Best Buy - Wadsworth", "address": "5395 S Wadsworth Blvd, Littleton, CO 80123", "line": "Sweep", "flex": "NO", "days": ["Tue", "Thu"], "lat": 39.6186, "lon": -105.0815},
    {"id": "r_ko_que", "store": "Kohl's 2 - Quebec (2x/wk)", "address": "8660 S Quebec St, Lone Tree, CO 80124", "line": "Dual", "flex": "Yes", "days": [], "lat": 39.5594, "lon": -104.9036},
    {"id": "r_ko_kip", "store": "Kohl's 3 - Kipling (2x/wk)", "address": "8444 S Kipling Pkwy, Littleton, CO 80127", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.5638, "lon": -105.1101},
    {"id": "r_ko_cas", "store": "Kohl's 4 - Castle Rock (2x/wk)", "address": "4800 Milestone Ln, Castle Rock, CO 80104", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.4106, "lon": -104.8814},
    {"id": "r_ko_20m", "store": "Kohl's 5 - 20 Mile (2x/wk)", "address": "11485 S Twenty Mile Rd, Parker, CO 80134", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.5082, "lon": -104.7654},
    {"id": "r_sc_wad", "store": "Sam's Club - Wadsworth (2x/wk)", "address": "4827 S Wadsworth Way, Littleton, CO 80123", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.6285, "lon": -105.0814},
    {"id": "r_wg_ara", "store": "Walgreens - Arapahoe (1x/wk)", "address": "16440 E Arapahoe Rd, Foxfield, CO 80016", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.5955, "lon": -104.7963},
    {"id": "r_wg_kip", "store": "Walgreens - Kipling (1x/wk)", "address": "5870 S Kipling Pkwy, Littleton, CO 80127", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.6104, "lon": -105.1099},
    {"id": "r_wg_hgr", "store": "Walgreens - Highlands Ranch (2x/wk)", "address": "9390 S Colorado Blvd, Highlands Ranch, CO 80126", "line": "Dual", "flex": "Yes", "days": [], "lat": 39.5461, "lon": -104.9432},
    {"id": "r_wg_bel", "store": "Walgreens - Belleview (1x/wk)", "address": "2697 W Belleview Ave, Littleton, CO 80123", "line": "Sweep", "flex": "Yes", "days": [], "lat": 39.6238, "lon": -105.0209},
    {"id": "r_lo_pea", "store": "Lowe's - Peakview (Mon + 1 Flex)", "address": "9100 E Peakview Ave, Greenwood Village, CO 80111", "line": "Sweep", "flex": "Mon + Wed-Fri", "days": ["Mon"], "lat": 39.5979, "lon": -104.8825},
    {"id": "r_lo_san", "store": "Lowe's - Santa Fe (Mon + 1 Flex)", "address": "5095 S Santa Fe Dr, Littleton, CO 80120", "line": "Sweep", "flex": "Mon + Wed-Fri", "days": ["Mon"], "lat": 39.6244, "lon": -105.0232},
    {"id": "r_lo_buc", "store": "Lowe's - Buckley (Mon + 1 Flex)", "address": "4455 S Buckley Rd, Aurora, CO 80015", "line": "Sweep", "flex": "Mon + Wed-Fri", "days": ["Mon"], "lat": 39.6355, "lon": -104.7915},
    {"id": "r_lo_20m", "store": "Lowe's - 20 Mile (Mon + 1 Flex)", "address": "10000 S Twenty Mile Rd, Parker, CO 80134", "line": "Sweep", "flex": "Mon + Wed-Fri", "days": ["Mon"], "lat": 39.5342, "lon": -104.7652},
    {"id": "r_lo_whe", "store": "Lowe's - Wheatlands (Mon + 1 Flex)", "address": "24505 E Wheatlands Pkwy, Aurora, CO 80016", "line": "Sweep", "flex": "Mon + Wed-Fri", "days": ["Mon"], "lat": 39.5919, "lon": -104.7024},
]

# --- SESSION STATE INITIALIZATION ---
if "current_page" not in st.session_state:
    st.session_state.current_page = "home"
if "confirm_leave_log" not in st.session_state:
    st.session_state.confirm_leave_log = False
if "log_submitted" not in st.session_state:
    st.session_state.log_submitted = False

if "visits" not in st.session_state or not isinstance(st.session_state.visits, dict):
    st.session_state.visits = {}
for row in RATE_CARD:
    st.session_state.visits.setdefault(row["id"], [])

if "open_account" not in st.session_state:
    st.session_state.open_account = None
if "open_calendar" not in st.session_state:
    st.session_state.open_calendar = None
if "extra_items" not in st.session_state:
    st.session_state.extra_items = []
if "extra_temp_days" not in st.session_state:
    st.session_state.extra_temp_days = []


def navigate_to(page_name: str):
    st.session_state.current_page = page_name
    st.session_state.confirm_leave_log = False
    st.rerun()


def reset_visit_log_data():
    for row in RATE_CARD:
        st.session_state.visits[row["id"]] = []
    st.session_state.extra_items = []
    st.session_state.extra_temp_days = []
    st.session_state.open_account = None
    st.session_state.open_calendar = None
    st.session_state.log_submitted = False
    st.session_state.confirm_leave_log = False


def render_calendar_picker(selected_days: list[int], year: int, month_num: int, key_prefix: str) -> list[int]:
    """Renders a 7-column Sun-Sat calendar grid where tapping a day toggles it on/off and stays open."""
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdayscalendar(year, month_num)
    updated = set(selected_days)

    hdr_cols = st.columns(7)
    for i, day_name in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
        hdr_cols[i].markdown(
            f"<div style='text-align:center; font-size:0.75rem; font-weight:700; color:#64748b;'>{day_name}</div>",
            unsafe_allow_html=True,
        )

    for week in weeks:
        cols = st.columns(7)
        for d_idx, day_num in enumerate(week):
            with cols[d_idx]:
                if day_num == 0:
                    st.markdown("<div style='height:2.6rem;'></div>", unsafe_allow_html=True)
                else:
                    is_selected = day_num in updated
                    label = f"✓{day_num}" if is_selected else str(day_num)
                    btn_type = "primary" if is_selected else "secondary"
                    if st.button(label, key=f"{key_prefix}_d{day_num}", type=btn_type):
                        if is_selected:
                            updated.remove(day_num)
                        else:
                            updated.add(day_num)
                        return sorted(updated)

    return sorted(updated)


def build_summary_df() -> pd.DataFrame:
    rows = []
    for item in RATE_CARD:
        days = sorted(st.session_state.visits.get(item["id"], []))
        qty = len(days)
        rows.append(
            {
                "Account": item["account"],
                "Store": f"{item['account']} - {item['location']}",
                "Line Item": f"{item['account']} - {item['location']} - {item['service']}",
                "Visits": qty,
                "Rate": item["rate"],
                "Amount": round(qty * item["rate"], 2),
                "Dates Visited": ", ".join(str(d) for d in days),
            }
        )
    for extra in st.session_state.extra_items:
        days = sorted(extra.get("days", []))
        qty = len(days)
        rows.append(
            {
                "Account": "Extra / One-Off",
                "Store": extra["name"],
                "Line Item": extra["name"],
                "Visits": qty,
                "Rate": extra["rate"],
                "Amount": round(qty * extra["rate"], 2),
                "Dates Visited": ", ".join(str(d) for d in days),
            }
        )
    return pd.DataFrame(rows)


def generate_invoice_pdf(df: pd.DataFrame, billing_month: str, invoice_num: str, tech_name: str = "") -> bytes:
    active_df = df[df["Visits"] > 0].copy()
    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_fill_color(30, 41, 59)
    pdf.rect(10, 10, 196, 22, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(14, 14)
    header_title = f"{tech_name.strip().upper()} - MONTHLY VISIT LOG" if tech_name.strip() else "MONTHLY VISIT LOG"
    pdf.cell(135, 7, header_title[:42])
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(14, 22)
    pdf.cell(135, 6, f"Bill To: Lone Wolf Parking Lot Services LLC  |  Period: {billing_month}")
    pdf.set_xy(150, 16)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(50, 8, f"Invoice #{invoice_num}", align="R")

    total_visits = int(active_df["Visits"].sum()) if not active_df.empty else 0
    total_amount = float(active_df["Amount"].sum()) if not active_df.empty else 0.0

    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(10, 36)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(98, 8, f"Total Verified Visits: {total_visits}", border=1)
    pdf.cell(98, 8, f"Total Invoice Amount: ${total_amount:,.2f}", border=1, align="R")

    pdf.set_xy(10, 48)
    pdf.set_fill_color(15, 76, 129)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    col_w = [68, 14, 20, 24, 70]
    headers = ["Service Line Item", "Visits", "Rate", "Amount", "Days Visited"]
    for w, h in zip(col_w, headers):
        align = "R" if h in ("Visits", "Rate", "Amount") else "L"
        pdf.cell(w, 8, h, border=1, fill=True, align=align)
    pdf.ln()

    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Helvetica", "", 8.5)
    fill = False
    for _, r in active_df.iterrows():
        pdf.set_fill_color(248, 250, 252) if fill else pdf.set_fill_color(255, 255, 255)
        dates_str = r["Dates Visited"] if len(r["Dates Visited"]) <= 44 else r["Dates Visited"][:41] + "..."
        pdf.cell(col_w[0], 7, r["Line Item"][:38], border=1, fill=True)
        pdf.cell(col_w[1], 7, str(int(r["Visits"])), border=1, fill=True, align="R")
        pdf.cell(col_w[2], 7, f"${r['Rate']:,.2f}", border=1, fill=True, align="R")
        pdf.cell(col_w[3], 7, f"${r['Amount']:,.2f}", border=1, fill=True, align="R")
        pdf.cell(col_w[4], 7, dates_str, border=1, fill=True)
        pdf.ln()
        fill = not fill

    pdf.set_fill_color(226, 232, 240)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(col_w[0], 8, "TOTAL", border=1, fill=True, align="R")
    pdf.cell(col_w[1], 8, str(total_visits), border=1, fill=True, align="R")
    pdf.cell(col_w[2], 8, "", border=1, fill=True)
    pdf.cell(col_w[3], 8, f"${total_amount:,.2f}", border=1, fill=True, align="R")
    pdf.cell(col_w[4], 8, "Attach to Novo Invoice", border=1, fill=True)

    return bytes(pdf.output())


def send_completion_email(
    pdf_bytes: bytes,
    billing_month: str,
    invoice_num: str,
    total_visits: int,
    total_amount: float,
    tech_name: str = "",
    tech_copy_email: str = "",
):
    smtp_host = st.secrets["email"]["smtp_host"]
    smtp_port = int(st.secrets["email"]["smtp_port"])
    sender = st.secrets["email"]["sender_email"]
    password = st.secrets["email"]["sender_password"]
    recipient = st.secrets["email"]["recipient_email"]

    recipients = [recipient]
    if tech_copy_email and tech_copy_email.strip() and tech_copy_email.strip().lower() != recipient.lower():
        recipients.append(tech_copy_email.strip())

    sender_label = tech_name.strip() if tech_name.strip() else "Tech"

    msg = EmailMessage()
    msg["Subject"] = f"Monthly Visit Log Ready ({sender_label}) — {billing_month} (Inv #{invoice_num} | ${total_amount:,.2f})"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"Hi Team,\n\n"
        f"{sender_label} has completed the monthly visit log for {billing_month}.\n\n"
        f"- Tech / Vendor: {sender_label}\n"
        f"- Invoice #: {invoice_num}\n"
        f"- Total Verified Visits: {total_visits}\n"
        f"- Total Invoice Amount: ${total_amount:,.2f}\n\n"
        f"The clean PDF visit log is attached to this email for your records, attach a copy to your invoice and bill the total as shown on this document. All payments require valid proof of service and may not be paid if proof is not rendered.\n"
    )
    filename = f"Monthly_Visit_Log_{billing_month.replace(' ', '_')}_Inv_{invoice_num}.pdf"
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(sender, password)
        server.send_message(msg)


def order_stops_nearest_neighbor(stops: list[dict], round_trip: bool = True) -> list[dict]:
    """Orders selected stops starting from Home Base (Lakewood) using nearest-neighbor routing."""
    remaining = stops.copy()
    ordered = []
    curr_lat, curr_lon = HOME_BASE["lat"], HOME_BASE["lon"]

    while remaining:
        nearest = min(
            remaining,
            key=lambda s: (s["lat"] - curr_lat) ** 2 + (s["lon"] - curr_lon) ** 2,
        )
        ordered.append(nearest)
        curr_lat, curr_lon = nearest["lat"], nearest["lon"]
        remaining.remove(nearest)

    return ordered


def build_google_maps_url(ordered_stops: list[dict], round_trip: bool = True) -> str:
    origin = quote(HOME_BASE["address"])
    if round_trip:
        destination = origin
        waypoints = "|".join(quote(s["address"]) for s in ordered_stops)
    else:
        destination = quote(ordered_stops[-1]["address"])
        waypoints = "|".join(quote(s["address"]) for s in ordered_stops[:-1])

    url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}&travelmode=driving"
    if waypoints:
        url += f"&waypoints={waypoints}"
    return url


# ==============================================================================
# PAGE 1: LANDING / HUB PAGE
# ==============================================================================
def render_home_page():
    st.title("🐺 Lone Wolf Field Portal")
    st.caption("Select a tool below to get started.")

    summary_df = build_summary_df()
    active_visits = int(summary_df["Visits"].sum())
    active_amt = float(summary_df["Amount"].sum())

    # Card 1: Monthly Visit Log
    with st.container(border=True):
        st.subheader("🧹 Monthly Visit Log")
        st.write("Log store visits by calendar date, export your Novo-ready PDF, and email your completed report.")
        if active_visits > 0 and not st.session_state.log_submitted:
            st.warning(f"In-progress draft: **{active_visits} visits (${active_amt:,.2f})** not yet submitted.")
        if st.button("Open Monthly Visit Log →", type="primary", use_container_width=True):
            navigate_to("visit_log")

    # Card 2: Route Optimizer
    with st.container(border=True):
        st.subheader("🗺️ Denver Route Optimizer")
        st.write("Build your daily sweep & porter route from Lakewood HQ, add flexible stops, and launch Google Maps.")
        if st.button("Open Route Optimizer →", type="primary", use_container_width=True):
            navigate_to("route_optimizer")

    # Card 3: Placeholder for Upcoming Pages
    with st.container(border=True):
        st.subheader("🧰 More Tools Coming Soon")
        st.caption("DMG Pro Cross-Reference Audit, Snow / L&L Logs, and Equipment Checklists will live right here.")


# ==============================================================================
# PAGE 2: MONTHLY VISIT LOG (WITH UNSAVED DATA PROTECTION)
# ==============================================================================
def render_visit_log_page():
    summary_df = build_summary_df()
    tot_visits = int(summary_df["Visits"].sum())
    tot_amt = float(summary_df["Amount"].sum())
    has_unsaved = (tot_visits > 0) and (not st.session_state.log_submitted)

    # Top Navigation Bar with Back Button
    nav_col1, nav_col2 = st.columns([1.4, 2.6])
    with nav_col1:
        if st.button("← Back to Home", use_container_width=True):
            if has_unsaved:
                st.session_state.confirm_leave_log = True
                st.rerun()
            else:
                navigate_to("home")

    # Unsaved Changes Warning Banner
    if st.session_state.confirm_leave_log and has_unsaved:
        with st.container(border=True):
            st.error(
                f"⚠️ **Data Will Be Lost if Not Submitted!**  \n"
                f"You currently have **{tot_visits} visit(s) (${tot_amt:,.2f})** entered that have not been emailed yet. "
                f"Please scroll down and tap **Email Completed Log to Team** first, or confirm below if you want to discard and return Home."
            )
            w_col1, w_col2 = st.columns(2)
            with w_col1:
                if st.button("Stay & Submit First", type="primary", use_container_width=True):
                    st.session_state.confirm_leave_log = False
                    st.rerun()
            with w_col2:
                if st.button("Discard & Go Home", type="secondary", use_container_width=True):
                    reset_visit_log_data()
                    navigate_to("home")

    st.title("🧹 Monthly Visit Log")
    st.caption("Open a store group below, tap 'Tap to pick dates', select your days, then tap OK.")

    tech_name = st.text_input("Tech / Company Name (Optional)", value="", placeholder="Enter your name or company")

    now = datetime.now()
    prev_year = now.year - 1 if now.month == 1 else now.year
    prev_month = 12 if now.month == 1 else now.month - 1

    period_options = [
        (now.year, now.month, f"{calendar.month_name[now.month]} {now.year}"),
        (prev_year, prev_month, f"{calendar.month_name[prev_month]} {prev_year}"),
    ]

    col_p, col_i = st.columns([2.3, 1.3])
    with col_p:
        selected_period = st.selectbox(
            "Billing Period",
            options=period_options,
            format_func=lambda x: x[2],
            index=0,
        )
    with col_i:
        invoice_num = st.text_input("Invoice #", value="0026")

    selected_year, selected_month_num, billing_month = selected_period

    k1, k2 = st.columns(2)
    k1.metric("Total Visits Logged", f"{tot_visits}")
    k2.metric("Total Invoice Amount", f"${tot_amt:,.2f}")

    st.divider()

    # --- STEP 1: TAP DATES VISITED BY ACCOUNT ---
    st.subheader("1. Select Dates Visited")
    accounts = [
        "King Soopers",
        "Lowe's",
        "Kohl's",
        "Best Buy",
        "Highlands Ranch / Safeway",
        "Sam's Club",
        "Walgreens",
    ]

    for acct in accounts:
        acct_items = [x for x in RATE_CARD if x["account"] == acct]
        acct_visits = sum(len(st.session_state.visits.get(x["id"], [])) for x in acct_items)
        acct_dollars = sum(len(st.session_state.visits.get(x["id"], [])) * x["rate"] for x in acct_items)
        badge = f" — ✅ {acct_visits} visits (${acct_dollars:,.0f})" if acct_visits > 0 else ""

        is_acct_open = (st.session_state.open_account == acct) or any(
            st.session_state.open_calendar == x["id"] for x in acct_items
        )

        with st.expander(f"{acct}{badge}", expanded=is_acct_open):
            for item in acct_items:
                current_days = st.session_state.visits.get(item["id"], [])
                is_cal_open = st.session_state.open_calendar == item["id"]

                if current_days:
                    days_preview = ", ".join(str(d) for d in current_days)
                    st.markdown(
                        f"**{item['location']} — {item['service']}** (${item['rate']:.0f}/visit)  \n"
                        f"Selected ({len(current_days)}): `{days_preview}`"
                    )
                else:
                    st.markdown(f"**{item['location']} — {item['service']}** (${item['rate']:.0f}/visit)")

                if not is_cal_open:
                    if st.button("📅 Tap to pick dates", key=f"open_{item['id']}", use_container_width=True):
                        st.session_state.open_account = acct
                        st.session_state.open_calendar = item["id"]
                        st.rerun()
                else:
                    with st.container(border=True):
                        new_days = render_calendar_picker(
                            selected_days=current_days,
                            year=selected_year,
                            month_num=selected_month_num,
                            key_prefix=item["id"],
                        )
                        if new_days != current_days:
                            st.session_state.visits[item["id"]] = new_days
                            st.session_state.log_submitted = False
                            st.rerun()

                        if st.button("✅ OK", key=f"ok_{item['id']}", type="primary", use_container_width=True):
                            st.session_state.open_account = acct
                            st.session_state.open_calendar = None
                            st.rerun()

                st.markdown("<hr style='margin:0.5rem 0; border-color:#e2e8f0;'>", unsafe_allow_html=True)

            if st.button(f"Done with {acct} ▲", key=f"done_{acct}", use_container_width=True):
                st.session_state.open_account = None
                st.session_state.open_calendar = None
                st.rerun()

    # Optional One-Off / Bulk Trash section
    with st.expander(
        "➕ Add One-Off Job (e.g., Bulk Trash)",
        expanded=(st.session_state.open_account == "extra_job" or st.session_state.open_calendar == "extra_job"),
    ):
        ex_name = st.text_input("Job Description", placeholder="e.g., Arkansas Bulk Trash")
        ex_rate = st.number_input("Price per Visit ($)", min_value=0.0, value=500.0, step=25.0)

        if st.session_state.extra_temp_days:
            st.caption(f"Selected days: {', '.join(str(d) for d in st.session_state.extra_temp_days)}")

        if st.session_state.open_calendar != "extra_job":
            if st.button("📅 Tap to pick dates", key="open_extra_job", use_container_width=True):
                st.session_state.open_account = "extra_job"
                st.session_state.open_calendar = "extra_job"
                st.rerun()
        else:
            with st.container(border=True):
                new_ex_days = render_calendar_picker(
                    selected_days=st.session_state.extra_temp_days,
                    year=selected_year,
                    month_num=selected_month_num,
                    key_prefix="extra_job",
                )
                if new_ex_days != st.session_state.extra_temp_days:
                    st.session_state.extra_temp_days = new_ex_days
                    st.rerun()

                if st.button("✅ OK", key="ok_extra_job", type="primary", use_container_width=True):
                    st.session_state.open_calendar = None
                    st.rerun()

        if st.button("Add One-Off Job to Log", use_container_width=True):
            if ex_name and st.session_state.extra_temp_days:
                st.session_state.extra_items.append(
                    {"name": ex_name, "rate": ex_rate, "days": st.session_state.extra_temp_days}
                )
                st.session_state.extra_temp_days = []
                st.session_state.open_calendar = None
                st.session_state.open_account = None
                st.session_state.log_submitted = False
                st.rerun()
            else:
                st.warning("Enter a job description and tap at least one date on the calendar.")

        if st.session_state.extra_items:
            st.markdown("**Added One-Off Jobs:**")
            for idx, extra in enumerate(st.session_state.extra_items):
                c1, c2 = st.columns([4, 1])
                c1.write(f"• {extra['name']} — {len(extra['days'])} visit(s) @ ${extra['rate']:,.2f}")
                if c2.button("🗑️", key=f"del_extra_{idx}"):
                    st.session_state.extra_items.pop(idx)
                    st.session_state.log_submitted = False
                    st.rerun()

    st.divider()

    # --- STEP 2: REVIEW & SUBMIT ---
    st.subheader("2. Download PDF & Email Completed Log")
    active_preview = summary_df[summary_df["Visits"] > 0][["Line Item", "Visits", "Amount", "Dates Visited"]]

    if active_preview.empty:
        st.info("No visits selected yet. Open any store group above and tap 'Tap to pick dates'.")
    else:
        st.dataframe(active_preview, use_container_width=True, hide_index=True)

        pdf_bytes = generate_invoice_pdf(summary_df, billing_month, invoice_num, tech_name=tech_name)
        pdf_filename = f"Monthly_Visit_Log_{billing_month.replace(' ', '_')}_Inv_{invoice_num}.pdf"

        st.download_button(
            label="📄 1. Download PDF to Attach in Novo",
            data=pdf_bytes,
            file_name=pdf_filename,
            mime="application/pdf",
            type="secondary",
        )

        tech_copy_email = st.text_input(
            "Send a PDF copy to yourself (optional — for uploading to Novo):",
            value="",
            placeholder="your.email@gmail.com",
            help="Leave blank if you only want to send to the Team email.",
        )

        if st.button("📧 2. Email Completed Log to Team", type="primary", use_container_width=True):
            try:
                send_completion_email(
                    pdf_bytes,
                    billing_month,
                    invoice_num,
                    tot_visits,
                    tot_amt,
                    tech_name=tech_name,
                    tech_copy_email=tech_copy_email,
                )
                st.session_state.log_submitted = True
                st.session_state.confirm_leave_log = False
                if tech_copy_email.strip():
                    st.success(
                        f"Sent! Completed {billing_month} PDF log (${tot_amt:,.2f} across {tot_visits} visits) "
                        f"emailed to Team and {tech_copy_email.strip()}. You can now safely return Home."
                    )
                else:
                    st.success(
                        f"Sent! Completed {billing_month} PDF log (${tot_amt:,.2f} across {tot_visits} visits) "
                        f"emailed to Team. You can now safely return Home."
                    )
            except Exception as e:
                st.error(f"Could not send email — check Streamlit Secrets email settings. Details: {e}")


# ==============================================================================
# PAGE 3: DENVER ROUTE OPTIMIZER
# ==============================================================================
def render_route_optimizer_page():
    if st.button("← Back to Home"):
        navigate_to("home")

    st.title("🗺️ Denver Route Optimizer")
    st.caption(f"Starts & ends at **{HOME_BASE['address']}**")

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    today_short = day_names[datetime.now().weekday()]

    col_d, col_rt = st.columns([2, 1.5])
    with col_d:
        selected_day = st.selectbox("Route Day", options=day_names, index=day_names.index(today_short))
    with col_rt:
        st.markdown("<div style='height:1.65rem;'></div>", unsafe_allow_html=True)
        round_trip = st.checkbox("Return to Lakewood", value=True)

    # Split stops into Mandatory for selected day vs. Flexible / Other stops
    scheduled_stops = [s for s in ROUTE_STOPS if selected_day in s["days"]]
    flex_stops = [s for s in ROUTE_STOPS if selected_day not in s["days"]]

    st.subheader(f"1. Scheduled Stops for {selected_day} ({len(scheduled_stops)})")
    selected_stop_ids = []
    if scheduled_stops:
        for s in scheduled_stops:
            if st.checkbox(f"**{s['store']}** ({s['line']}) — {s['address']}", value=True, key=f"sch_{s['id']}"):
                selected_stop_ids.append(s["id"])
    else:
        st.info(f"Only King Soopers Leetsdale is fixed on {selected_day}s. Add flexible stops below.")

    with st.expander(f"➕ Add Flexible / Extra Stops ({len(flex_stops)} available)", expanded=(selected_day in ["Wed", "Thu", "Fri", "Sat"])):
        for s in flex_stops:
            if st.checkbox(f"**{s['store']}** ({s['line']}) — {s['address']}", value=False, key=f"flx_{s['id']}"):
                selected_stop_ids.append(s["id"])

    chosen_stops = [s for s in ROUTE_STOPS if s["id"] in selected_stop_ids]

    st.divider()
    st.subheader(f"2. Optimized Stop Sequence ({len(chosen_stops)} stops)")

    if not chosen_stops:
        st.warning("Select at least one stop above to build your route.")
    else:
        ordered = order_stops_nearest_neighbor(chosen_stops, round_trip=round_trip)

        route_rows = [{"Stop #": "Start", "Location": HOME_BASE["name"], "Service": "-", "Address": HOME_BASE["address"]}]
        for idx, s in enumerate(ordered, start=1):
            route_rows.append(
                {
                    "Stop #": f"#{idx}",
                    "Location": s["store"],
                    "Service": s["line"],
                    "Address": s["address"],
                }
            )
        if round_trip:
            route_rows.append({"Stop #": "End", "Location": HOME_BASE["name"], "Service": "-", "Address": HOME_BASE["address"]})

        st.dataframe(pd.DataFrame(route_rows), use_container_width=True, hide_index=True)

        # Google Maps supports up to 9-10 waypoints per link; split cleanly if route has >9 stops
        if len(ordered) <= 9:
            gmaps_url = build_google_maps_url(ordered, round_trip=round_trip)
            st.link_button("🚀 Open Full Route in Google Maps", gmaps_url, type="primary", use_container_width=True)
        else:
            st.info(f"Your route has {len(ordered)} stops. Google Maps allows up to 9 stops per link, so it is split into Leg 1 & Leg 2:")
            leg1_url = build_google_maps_url(ordered[:9], round_trip=False)
            leg2_origin = quote(ordered[8]["address"])
            leg2_dest = quote(HOME_BASE["address"]) if round_trip else quote(ordered[-1]["address"])
            leg2_wp_list = ordered[9:] if round_trip else ordered[9:-1]
            leg2_url = f"https://www.google.com/maps/dir/?api=1&origin={leg2_origin}&destination={leg2_dest}&travelmode=driving"
            if leg2_wp_list:
                leg2_url += "&waypoints=" + "|".join(quote(s["address"]) for s in leg2_wp_list)

            c_leg1, c_leg2 = st.columns(2)
            c_leg1.link_button("🚀 Leg 1 (Stops 1–9)", leg1_url, type="primary", use_container_width=True)
            c_leg2.link_button(f"🚀 Leg 2 (Stops 10–{len(ordered)})", leg2_url, type="primary", use_container_width=True)


# ==============================================================================
# ROUTER
# ==============================================================================
if st.session_state.current_page == "home":
    render_home_page()
elif st.session_state.current_page == "visit_log":
    render_visit_log_page()
elif st.session_state.current_page == "route_optimizer":
    render_route_optimizer_page()
