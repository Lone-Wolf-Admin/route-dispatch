import math
import re
import urllib.parse
from datetime import datetime, timezone

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
from supabase import Client, create_client

st.set_page_config(
    page_title="7-Day Route Dispatch & Balancer",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
FALLBACK_COORD = (39.6500, -104.9800)


# ==========================================
# 1. SUPABASE CONNECTION & PHONE AUTH
# ==========================================
@st.cache_resource
def get_supabase() -> Client | None:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception:
        return None


def normalize_phone(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", str(raw_phone))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if str(raw_phone).strip().startswith("+") and len(digits) >= 10:
        return f"+{digits}"
    return ""


def get_user_profile(sb: Client, phone_e164: str) -> dict | None:
    res = sb.table("app_users").select("*").eq("phone", phone_e164).execute()
    if res.data:
        return res.data[0]
    return None


def bootstrap_admin_if_empty(sb: Client):
    """Ensures the configured ADMIN_PHONE from Streamlit Secrets is registered as Admin."""
    admin_raw = st.secrets.get("ADMIN_PHONE", "")
    admin_phone = normalize_phone(admin_raw)
    if not admin_phone:
        return
    existing = get_user_profile(sb, admin_phone)
    if not existing:
        sb.table("app_users").insert(
            {
                "phone": admin_phone,
                "name": "Primary Admin",
                "role": "admin",
                "status": "approved",
            }
        ).execute()


# ==========================================
# 2. GEOCODING, ROUTING & OPTIMIZATION ENGINE
# ==========================================
def clean_address_for_geocoding(address: str) -> str:
    addr = " ".join(str(address).split())
    addr = re.sub(r"\b(STE|SUITE|UNIT|BLDG|APT|#)\s*[A-Z0-9\-]+\b", "", addr, flags=re.IGNORECASE)
    addr = re.sub(r"\s+,", ",", addr)
    return " ".join(addr.split())


def _try_arcgis(query: str):
    url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
    resp = requests.get(url, params={"SingleLine": query, "maxLocations": 1, "f": "json"}, timeout=8)
    data = resp.json()
    cands = data.get("candidates", [])
    if cands and cands[0].get("score", 0) >= 70:
        loc = cands[0]["location"]
        return (float(loc["y"]), float(loc["x"]))
    return None


def _try_census(query: str):
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    resp = requests.get(
        url,
        params={"address": query, "benchmark": "Public_AR_Current", "format": "json"},
        timeout=8,
    )
    matches = resp.json().get("result", {}).get("addressMatches", [])
    if matches:
        c = matches[0]["coordinates"]
        return (float(c["y"]), float(c["x"]))
    return None


def _try_photon(query: str):
    url = "https://photon.komoot.io/api/"
    resp = requests.get(
        url,
        params={"q": query, "limit": 1, "lat": 39.65, "lon": -104.98},
        headers={"User-Agent": "DenverRouteWebDispatch/1.0"},
        timeout=8,
    )
    feats = resp.json().get("features", [])
    if feats:
        lon, lat = feats[0]["geometry"]["coordinates"]
        return (float(lat), float(lon))
    return None


@st.cache_data(show_spinner=False, ttl=86400 * 30)
def geocode_address(address: str) -> tuple[float, float]:
    cleaned = clean_address_for_geocoding(address)
    queries = [cleaned] if cleaned.lower() == address.strip().lower() else [cleaned, address.strip()]
    for q in queries:
        for fn in (_try_arcgis, _try_census, _try_photon):
            try:
                coord = fn(q)
                if coord and 37.0 <= coord[0] <= 41.5 and -109.5 <= coord[1] <= -102.0:
                    return coord
            except Exception:
                continue
    return FALLBACK_COORD


@st.cache_data(show_spinner=False, ttl=86400 * 7)
def fetch_road_polyline(coords_tuple: tuple) -> list[list[float]]:
    if len(coords_tuple) < 2:
        return [list(c) for c in coords_tuple]
    try:
        coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords_tuple)
        url = f"https://router.project-osrm.org/route/v1/driving/{coord_str}"
        resp = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=6)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            raw_pts = data["routes"][0]["geometry"]["coordinates"]
            return [[pt[1], pt[0]] for pt in raw_pts]
    except Exception:
        pass
    return [list(c) for c in coords_tuple]


def road_miles(c1, c2) -> float:
    if c1 == c2:
        return 0.0
    r = 3958.8
    p1, p2 = math.radians(c1[0]), math.radians(c2[0])
    dp = math.radians(c2[0] - c1[0])
    dl = math.radians(c2[1] - c1[1])
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a)) * 1.24


def drive_minutes(c1, c2) -> float:
    miles = road_miles(c1, c2)
    speed = 24.0 if miles < 3 else (30.0 if miles < 8 else 36.0)
    return (miles / speed) * 60.0


def optimize_loop(depot_coord, stops):
    if not stops:
        return [], 0.0, 0.0
    unvisited = list(stops)
    route = []
    curr = depot_coord
    while unvisited:
        nxt = min(unvisited, key=lambda s: drive_minutes(curr, s["coord"]))
        route.append(nxt)
        curr = nxt["coord"]
        unvisited.remove(nxt)

    def route_time(seq):
        t = drive_minutes(depot_coord, seq[0]["coord"])
        for i in range(len(seq) - 1):
            t += drive_minutes(seq[i]["coord"], seq[i + 1]["coord"])
        t += drive_minutes(seq[-1]["coord"], depot_coord)
        return t

    improved = True
    while improved and len(route) > 2:
        improved = False
        best_t = route_time(route)
        for i in range(len(route) - 1):
            for j in range(i + 1, len(route)):
                cand = route[:i] + route[i : j + 1][::-1] + route[j + 1 :]
                cand_t = route_time(cand)
                if cand_t < best_t - 1e-4:
                    route = cand
                    best_t = cand_t
                    improved = True

    total_miles = road_miles(depot_coord, route[0]["coord"])
    for i in range(len(route) - 1):
        total_miles += road_miles(route[i]["coord"], route[i + 1]["coord"])
    total_miles += road_miles(route[-1]["coord"], depot_coord)
    return route, route_time(route), total_miles


def build_routexl_url(depot_address: str, stops: list[dict]) -> str:
    if not stops:
        return ""

    def fmt(addr, label=None, dur=None):
        clean_a = str(addr).replace("$", "").replace("@", "").strip()
        parts = [clean_a]
        if label:
            clean_l = str(label).replace("$", "").replace("@", "").strip()
            parts.append(f"@{clean_l}@")
        if dur is not None:
            parts.append(f"({int(round(dur))})")
        return " ".join(parts)

    entries = [fmt(depot_address, "Depot Start")]
    for s in stops:
        entries.append(fmt(s["address"], s["store"], s["duration"]))
    entries.append(fmt(depot_address, "Depot End"))
    encoded = [urllib.parse.quote(e, safe="") for e in entries]
    return "https://www.routexl.com/?q=" + "$".join(encoded)


# ==========================================
# 3. DATABASE SYNC HELPERS
# ==========================================
def publish_schedule_to_cloud(
    sb: Client,
    depot_address: str,
    depot_coord: tuple,
    day_routes: dict,
    day_stats: dict,
    day_assignments: dict,
    updated_by: str,
):
    payload = {
        "depot_address": depot_address,
        "depot_coord": list(depot_coord),
        "day_routes": day_routes,
        "day_stats": day_stats,
        "day_assignments": day_assignments,
    }
    sb.table("published_schedules").upsert(
        {
            "id": 1,
            "schedule_json": payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
        }
    ).execute()


def fetch_cloud_schedule(sb: Client) -> dict | None:
    res = sb.table("published_schedules").select("*").eq("id", 1).execute()
    if res.data:
        row = res.data[0]
        data = row.get("schedule_json") or {}
        data["_updated_at"] = row.get("updated_at", "")
        data["_updated_by"] = row.get("updated_by", "")
        return data
    return None


# ==========================================
# 4. AUTHENTICATION & USER GATEWAY
# ==========================================
def render_login_screen(sb: Client):
    st.title("🚚 7-Day Route Dispatch & Balancer")
    st.caption("Sign in with your mobile phone number via SMS verification code.")

    col1, _ = st.columns([1.3, 1])
    with col1:
        with st.container(border=True):
            st.subheader("Phone Number Verification")
            raw_phone = st.text_input(
                "Mobile Phone Number",
                placeholder="(303) 555-1234",
                help="Enter your 10-digit US mobile number. An SMS verification code will be sent.",
            )
            full_name = st.text_input("Your Name (for new team members)", placeholder="Alex Rivera")

            if st.button("Send SMS Verification Code", type="primary", use_container_width=True):
                phone_e164 = normalize_phone(raw_phone)
                if not phone_e164:
                    st.error("Please enter a valid 10-digit US phone number.")
                else:
                    try:
                        sb.auth.sign_in_with_otp({"phone": phone_e164})
                        st.session_state["pending_phone"] = phone_e164
                        st.session_state["pending_name"] = full_name.strip() or "Team Member"
                        st.success(f"6-digit SMS code sent to {phone_e164}!")
                    except Exception as e:
                        st.error(f"Could not send SMS code: {e}")

            if st.session_state.get("pending_phone"):
                st.divider()
                otp_code = st.text_input("Enter 6-Digit SMS Code", max_chars=6, placeholder="123456")
                if st.button("Verify & Sign In", type="primary", use_container_width=True):
                    p_phone = st.session_state["pending_phone"]
                    p_name = st.session_state.get("pending_name", "Team Member")
                    try:
                        auth_res = sb.auth.verify_otp(
                            {"phone": p_phone, "token": otp_code.strip(), "type": "sms"}
                        )
                        if auth_res and auth_res.user:
                            profile = get_user_profile(sb, p_phone)
                            if not profile:
                                sb.table("app_users").insert(
                                    {
                                        "phone": p_phone,
                                        "name": p_name,
                                        "role": "driver",
                                        "status": "pending",
                                    }
                                ).execute()
                                profile = get_user_profile(sb, p_phone)
                            elif (
                                profile.get("name") in ("Invited Driver", "Team Member")
                                and p_name != "Team Member"
                            ):
                                sb.table("app_users").update({"name": p_name}).eq(
                                    "phone", p_phone
                                ).execute()
                                profile["name"] = p_name

                            st.session_state["user_profile"] = profile
                            st.session_state.pop("pending_phone", None)
                            st.rerun()
                    except Exception as e:
                        st.error(f"Verification failed: {e}")


# ==========================================
# 5. MAIN APPLICATION UI (ADMIN & DRIVER)
# ==========================================
def render_day_view(
    day: str,
    depot_addr: str,
    depot_coord: tuple,
    stops: list[dict],
    stats: dict,
    assigned_driver: str,
):
    st.markdown(
        f"**Assigned Driver:** `{assigned_driver or 'Unassigned'}` &nbsp;|&nbsp; "
        f"**Stops:** `{stats.get('stops', 0)}` &nbsp;|&nbsp; "
        f"**On-Site:** `{int(stats.get('svc_min', 0))} min` &nbsp;|&nbsp; "
        f"**Est. Miles:** `{stats.get('miles', 0):.1f} mi`"
    )

    if not stops:
        st.info(f"No stops scheduled for {day}.")
        return

    routexl_url = build_routexl_url(depot_addr, stops)
    sms_lines = [f"{day.upper()} ROUTE ({len(stops)} stops | ~{stats.get('miles', 0):.1f} mi)"]
    for idx, s in enumerate(stops, start=1):
        sms_lines.append(f"{idx}. {s['store']} - {s['address']}")
    sms_lines.append(f"\nRouteXL Link:\n{routexl_url}")
    sms_body = "\n".join(sms_lines)
    sms_href = f"sms:?&body={urllib.parse.quote(sms_body)}"

    c1, c2 = st.columns(2)
    with c1:
        st.link_button(
            "🚀 Open Route in RouteXL", routexl_url, use_container_width=True, type="primary"
        )
    with c2:
        st.link_button("💬 Share Route via Text (SMS)", sms_href, use_container_width=True)

    df_view = pd.DataFrame(
        [
            {
                "Stop #": idx,
                "Store": s["store"],
                "Address": s["address"],
                "Service": s["service_line"],
                "Min": int(s["duration"]),
                "Type": s["visit_type"],
            }
            for idx, s in enumerate(stops, start=1)
        ]
    )
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    m = folium.Map(location=list(depot_coord), zoom_start=11, tiles="OpenStreetMap")
    loop_coords = tuple(
        [tuple(depot_coord)] + [tuple(s["coord"]) for s in stops] + [tuple(depot_coord)]
    )
    poly = fetch_road_polyline(loop_coords)
    folium.PolyLine(poly, color="#1a73e8", weight=4, opacity=0.85).add_to(m)

    folium.Marker(
        location=list(depot_coord),
        popup=f"<b>DEPOT (Start/End)</b><br>{depot_addr}",
        tooltip="DEPOT",
        icon=folium.Icon(color="green", icon="home", prefix="fa"),
    ).add_to(m)

    all_pts = [list(depot_coord)]
    for idx, s in enumerate(stops, start=1):
        pt = list(s["coord"])
        all_pts.append(pt)
        folium.Marker(
            location=pt,
            popup=f"<b>#{idx} {s['store']}</b><br>{s['address']}<br>({s['service_line']} - {int(s['duration'])} min)",
            tooltip=f"#{idx} {s['store']}",
            icon=folium.Icon(color="blue", icon="map-pin", prefix="fa"),
        ).add_to(m)

    m.fit_bounds(all_pts, padding=(30, 30))
    st_folium(m, height=400, use_container_width=True, key=f"map_{day}")


def main():
    sb = get_supabase()
    if sb is None:
        st.error(
            "Supabase credentials missing. Add `SUPABASE_URL`, `SUPABASE_KEY`, and `ADMIN_PHONE` "
            "to your `.streamlit/secrets.toml` or Streamlit Cloud Secrets."
        )
        st.stop()

    bootstrap_admin_if_empty(sb)

    profile = st.session_state.get("user_profile")
    if not profile:
        render_login_screen(sb)
        return

    fresh_profile = get_user_profile(sb, profile["phone"])
    if fresh_profile:
        profile = fresh_profile
        st.session_state["user_profile"] = profile

    with st.sidebar:
        st.markdown(f"### 👤 {profile['name']}")
        st.caption(f"Phone: `{profile['phone']}`")
        st.caption(f"Role: **{profile['role'].upper()}** | Status: **{profile['status'].upper()}**")
        if st.button("Sign Out", use_container_width=True):
            try:
                sb.auth.sign_out()
            except Exception:
                pass
            st.session_state.pop("user_profile", None)
            st.rerun()

    if profile["status"] != "approved":
        st.warning("⏳ Your phone number is verified, and your account is awaiting Admin approval.")
        st.info(
            f"Ask your Admin to approve **{profile['phone']}** in their **Team & Phone Verification** tab, "
            "then tap Refresh below."
        )
        if st.button("🔄 Refresh Approval Status", type="primary"):
            st.rerun()
        return

    is_admin = profile["role"] == "admin"

    if is_admin:
        tab_routes, tab_builder, tab_users = st.tabs(
            [
                "📅 Live 7-Day Routes",
                "⚙️ Route Builder & Excel Upload",
                "👥 Team & Phone Verification",
            ]
        )
    else:
        (tab_routes,) = st.tabs(["📅 My Live 7-Day Routes"])
        tab_builder = None
        tab_users = None

    # ------------------------------------------
    # TAB 1: LIVE SYNCED ROUTES (ALL APPROVED USERS)
    # ------------------------------------------
    with tab_routes:
        c_head, c_ref = st.columns([4, 1])
        with c_head:
            st.subheader("Synced 7-Day Route Schedule")
        with c_ref:
            if st.button("🔄 Sync Latest", use_container_width=True):
                st.rerun()

        cloud_data = fetch_cloud_schedule(sb)
        if not cloud_data or not cloud_data.get("day_routes"):
            st.info(
                "No schedule has been published yet. "
                + (
                    "Go to **Route Builder & Excel Upload** to upload your Excel file and publish."
                    if is_admin
                    else "Ask your Admin to publish this week's routes."
                )
            )
        else:
            st.caption(
                f"Depot: **{cloud_data['depot_address']}** | Last Published: `{cloud_data.get('_updated_at', '')[:19]} UTC` by {cloud_data.get('_updated_by', 'Admin')}"
            )

            day_routes = cloud_data["day_routes"]
            day_stats = cloud_data["day_stats"]
            day_assignments = cloud_data.get("day_assignments", {})

            sum_df = pd.DataFrame(
                [
                    {
                        "Day": d,
                        "Assigned To": day_assignments.get(d, "Unassigned"),
                        "Stops": day_stats[d]["stops"],
                        "On-Site Time": f"{int(day_stats[d]['svc_min'] // 60)}h {int(day_stats[d]['svc_min'] % 60):02d}m",
                        "Est. Drive": f"{int(day_stats[d]['drv_min'] // 60)}h {int(day_stats[d]['drv_min'] % 60):02d}m",
                        "Est. Miles": f"{day_stats[d]['miles']:.1f} mi",
                    }
                    for d in DAYS
                ]
            )
            st.dataframe(sum_df, use_container_width=True, hide_index=True)

            selected_day = st.selectbox("Select Day to View Route & Map:", DAYS, index=0)
            render_day_view(
                selected_day,
                cloud_data["depot_address"],
                tuple(cloud_data["depot_coord"]),
                day_routes.get(selected_day, []),
                day_stats.get(selected_day, {}),
                day_assignments.get(selected_day, "Unassigned"),
            )

    # ------------------------------------------
    # TAB 2: ADMIN ROUTE BUILDER & EXCEL UPLOAD
    # ------------------------------------------
    if is_admin and tab_builder is not None:
        with tab_builder:
            st.subheader("Upload Excel & Balance Weekly Routes")
            uploaded_file = st.file_uploader(
                "Upload Route Spreadsheet (.xlsx)", type=["xlsx", "xls"]
            )

            col_s1, col_s2 = st.columns(2)
            with col_s1:
                single_min = st.number_input(
                    "Single-Line Stop Duration (min)", min_value=1, max_value=240, value=15
                )
            with col_s2:
                dual_min = st.number_input(
                    "Dual Stop Duration (min)", min_value=1, max_value=240, value=15
                )

            if uploaded_file is not None and st.button("⚡ Balance & Optimize Routes", type="primary"):
                with st.spinner("Geocoding addresses & balancing Monday–Friday workload..."):
                    raw = pd.read_excel(uploaded_file, header=None)
                    depot_addr = str(raw.iloc[0, 1]).strip()
                    df = pd.read_excel(uploaded_file, skiprows=2)

                    depot_coord = geocode_address(depot_addr)
                    stops_data = []
                    for idx, r in df.iterrows():
                        if pd.isna(r.get("Store")) or pd.isna(r.get("Address")):
                            continue
                        sl = str(r.get("Service Line", "Sweep")).strip()
                        if sl.lower() == "nan" or not sl:
                            sl = "Single"
                        flex_rule = str(r.get("Flexible Day", "Yes")).strip()
                        visits = int(r["how many visits a week"])
                        fixed = [d for d in DAYS if str(r.get(d, "")).strip().upper() == "X"]

                        if flex_rule.upper() == "NO":
                            allowed = []
                        elif "Wed-Friday" in flex_rule:
                            allowed = [
                                d for d in ["Wednesday", "Thursday", "Friday"] if d not in fixed
                            ]
                        else:
                            allowed = [d for d in WEEKDAYS if d not in fixed]

                        addr_str = str(r["Address"]).strip()
                        coord = geocode_address(addr_str)
                        is_dual = sl.lower() == "dual"
                        stops_data.append(
                            {
                                "id": idx + 1,
                                "store": str(r["Store"]).strip(),
                                "address": addr_str,
                                "service_line": sl,
                                "is_dual": is_dual,
                                "duration": float(dual_min if is_dual else single_min),
                                "coord": list(coord),
                                "visits": visits,
                                "fixed": fixed,
                                "allowed_flex": allowed,
                            }
                        )

                    day_buckets = {d: [] for d in DAYS}
                    flex_queue = []
                    for s in stops_data:
                        for d in s["fixed"]:
                            item = dict(s)
                            item["visit_type"] = "Fixed (X)"
                            day_buckets[d].append(item)
                        rem = s["visits"] - len(s["fixed"])
                        if rem > 0:
                            flex_queue.append((s, rem))

                    flex_queue.sort(key=lambda x: (len(x[0]["allowed_flex"]), -x[0]["duration"]))

                    for s, rem in flex_queue:
                        used_days = set(s["fixed"])
                        for _ in range(rem):
                            candidates = [d for d in s["allowed_flex"] if d not in used_days]
                            if not candidates:
                                candidates = [d for d in WEEKDAYS if d not in used_days]

                            def day_score(d):
                                svc_total = sum(x["duration"] for x in day_buckets[d])
                                if day_buckets[d]:
                                    min_detour = min(
                                        drive_minutes(x["coord"], s["coord"])
                                        for x in day_buckets[d]
                                    )
                                else:
                                    min_detour = drive_minutes(depot_coord, s["coord"])
                                return svc_total * 2.5 + min_detour

                            best_day = min(candidates, key=day_score)
                            used_days.add(best_day)
                            item = dict(s)
                            item["visit_type"] = "Flexible"
                            day_buckets[best_day].append(item)

                    draft_routes = {}
                    draft_stats = {}
                    for d in DAYS:
                        ordered, drv_min, miles = optimize_loop(depot_coord, day_buckets[d])
                        svc_min = sum(x["duration"] for x in ordered)
                        draft_routes[d] = ordered
                        draft_stats[d] = {
                            "stops": len(ordered),
                            "svc_min": svc_min,
                            "drv_min": drv_min,
                            "tot_min": svc_min + drv_min,
                            "miles": miles,
                        }

                    st.session_state["draft_schedule"] = {
                        "depot_address": depot_addr,
                        "depot_coord": list(depot_coord),
                        "day_routes": draft_routes,
                        "day_stats": draft_stats,
                    }
                    st.success(
                        "Routes balanced! Assign drivers below and click **Publish to All Android Devices**."
                    )

            if "draft_schedule" in st.session_state:
                draft = st.session_state["draft_schedule"]
                st.divider()
                st.markdown("#### Assign Drivers & Publish Live")

                users_res = sb.table("app_users").select("*").eq("status", "approved").execute()
                driver_options = ["Unassigned"] + [
                    f"{u['name']} ({u['phone']})" for u in (users_res.data or [])
                ]

                assignments = {}
                cols = st.columns(4)
                for i, d in enumerate(DAYS):
                    with cols[i % 4]:
                        assignments[d] = st.selectbox(
                            f"{d} Driver", driver_options, key=f"assign_{d}"
                        )

                if st.button(
                    "📡 Publish to All Android Devices", type="primary", use_container_width=True
                ):
                    publish_schedule_to_cloud(
                        sb,
                        draft["depot_address"],
                        tuple(draft["depot_coord"]),
                        draft["day_routes"],
                        draft["day_stats"],
                        assignments,
                        f"{profile['name']} ({profile['phone']})",
                    )
                    st.success(
                        "Published! Every approved Android phone can now see this schedule in the **Live 7-Day Routes** tab."
                    )

    # ------------------------------------------
    # TAB 3: ADMIN USER & PHONE VERIFICATION MANAGER
    # ------------------------------------------
    if is_admin and tab_users is not None:
        with tab_users:
            st.subheader("Add & Verify Team Members by Phone Number")
            st.write(
                "Pre-approve a driver's phone number below so they gain immediate access as soon as they verify their SMS code, "
                "or approve self-registered numbers waiting in the list."
            )

            with st.form("add_user_form", clear_on_submit=True):
                c1, c2, c3 = st.columns([2, 2, 1])
                new_name = c1.text_input("Team Member Name", placeholder="Jordan Smith")
                new_phone = c2.text_input("Mobile Phone Number", placeholder="(303) 555-9876")
                new_role = c3.selectbox("Role", ["driver", "admin"])
                submitted = st.form_submit_button("➕ Pre-Approve Phone Number", type="primary")

                if submitted:
                    norm_p = normalize_phone(new_phone)
                    if not norm_p:
                        st.error("Enter a valid 10-digit US phone number.")
                    else:
                        sb.table("app_users").upsert(
                            {
                                "phone": norm_p,
                                "name": new_name.strip() or "Invited Driver",
                                "role": new_role,
                                "status": "approved",
                            },
                            on_conflict="phone",
                        ).execute()
                        st.success(
                            f"Added & pre-approved {norm_p}! When they sign in with their SMS code, they will enter immediately."
                        )
                        st.rerun()

            st.divider()
            st.markdown("#### Registered & Pending Phone Numbers")
            all_users = (
                sb.table("app_users").select("*").order("created_at", desc=False).execute().data
                or []
            )

            for u in all_users:
                with st.container(border=True):
                    uc1, uc2, uc3, uc4 = st.columns([2.2, 2, 1.5, 2.3])
                    uc1.markdown(f"**{u['name']}**\n\n`{u['phone']}`")
                    uc2.markdown(
                        f"Role: `{u['role'].upper()}`\n\nStatus: **{u['status'].upper()}**"
                    )

                    if u["phone"] == profile["phone"]:
                        uc3.caption("Current Admin")
                    else:
                        if u["status"] != "approved":
                            if uc3.button(
                                "✅ Approve", key=f"app_{u['phone']}", use_container_width=True
                            ):
                                sb.table("app_users").update({"status": "approved"}).eq(
                                    "phone", u["phone"]
                                ).execute()
                                st.rerun()
                        else:
                            if uc3.button(
                                "🚫 Revoke", key=f"rev_{u['phone']}", use_container_width=True
                            ):
                                sb.table("app_users").update({"status": "revoked"}).eq(
                                    "phone", u["phone"]
                                ).execute()
                                st.rerun()

                        next_role = "admin" if u["role"] == "driver" else "driver"
                        if uc4.button(
                            f"Make {next_role.title()}",
                            key=f"role_{u['phone']}",
                            use_container_width=True,
                        ):
                            sb.table("app_users").update({"role": next_role}).eq(
                                "phone", u["phone"]
                            ).execute()
                            st.rerun()


if __name__ == "__main__":
    main()
