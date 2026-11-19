import math
import re
import urllib.parse

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(
    page_title="7-Day Route Optimizer & Dispatcher",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
FALLBACK_COORD = (39.6500, -104.9800)


# ==========================================
# 1. GEOCODING, ROUTING & OPTIMIZATION ENGINE
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
        headers={"User-Agent": "DenverRouteWebDispatch/2.1"},
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


def _clean_routexl_label(label: str) -> str:
    """Strips apostrophes, colons, and symbols that truncate RouteXL's URL parser."""
    clean = re.sub(r"[^A-Za-z0-9\s\-]", "", str(label))
    return " ".join(clean.split())[:24].strip()


def build_routexl_url(depot_address: str, depot_coord: tuple, stops: list[dict]) -> str:
    if not stops:
        return ""

    def fmt(addr, coord, label=None, dur=None):
        if coord and len(coord) == 2 and tuple(coord) != FALLBACK_COORD:
            loc = f"{coord[0]:.5f},{coord[1]:.5f}"
        else:
            loc = re.sub(r"[^A-Za-z0-9\s,\-]", "", str(addr)).strip()
        parts = [loc]
        if label:
            clean_l = _clean_routexl_label(label)
            if clean_l:
                parts.append(f"@{clean_l}@")
        if dur is not None:
            parts.append(f"({int(round(dur))})")
        return " ".join(parts)

    entries = [fmt(depot_address, depot_coord, "Depot Start")]
    for idx, s in enumerate(stops, start=1):
        entries.append(
            fmt(s["address"], s.get("coord"), f"{idx} {s['store']}", s["duration"])
        )
    entries.append(fmt(depot_address, depot_coord, "Depot End"))
    encoded = [urllib.parse.quote(e, safe=",@()") for e in entries]
    return "https://www.routexl.com/?q=" + "$".join(encoded)


def build_sms_text(
    day: str, depot_address: str, depot_coord: tuple, stops: list[dict], stats: dict
) -> str:
    routexl_url = build_routexl_url(depot_address, depot_coord, stops)
    lines = [
        f"{day.upper()} ROUTE ({len(stops)} stops | ~{stats.get('miles', 0):.1f} mi)",
        f"Start/End: {depot_address}",
        "",
    ]
    for idx, s in enumerate(stops, start=1):
        lines.append(f"{idx}. {s['store']} - {s['address']}")
    lines.append("")
    lines.append("Open Route in RouteXL:")
    lines.append(routexl_url)
    return "\n".join(lines)


# ==========================================
# 2. EXCEL PARSING & ROUTE BALANCING
# ==========================================
def process_excel_and_balance(uploaded_file, single_min: float, dual_min: float) -> dict:
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
            allowed = [d for d in ["Wednesday", "Thursday", "Friday"] if d not in fixed]
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
                    min_detour = min(drive_minutes(x["coord"], s["coord"]) for x in day_buckets[d])
                else:
                    min_detour = drive_minutes(depot_coord, s["coord"])
                return svc_total * 2.5 + min_detour

            best_day = min(candidates, key=day_score)
            used_days.add(best_day)
            item = dict(s)
            item["visit_type"] = "Flexible"
            day_buckets[best_day].append(item)

    day_routes = {}
    day_stats = {}
    for d in DAYS:
        ordered, drv_min, miles = optimize_loop(depot_coord, day_buckets[d])
        svc_min = sum(x["duration"] for x in ordered)
        day_routes[d] = ordered
        day_stats[d] = {
            "stops": len(ordered),
            "svc_min": svc_min,
            "drv_min": drv_min,
            "tot_min": svc_min + drv_min,
            "miles": miles,
        }

    return {
        "depot_address": depot_addr,
        "depot_coord": list(depot_coord),
        "day_routes": day_routes,
        "day_stats": day_stats,
    }


# ==========================================
# 3. MAIN WEB APP UI
# ==========================================
def main():
    st.title("🚚 7-Day Route Optimizer & Balancer")
    st.caption(
        "Upload your Excel spreadsheet to automatically balance Monday–Friday workloads, "
        "view interactive street maps, launch routes in RouteXL, or text routes to drivers."
    )

    with st.container(border=True):
        c_up, c_s1, c_s2 = st.columns([2.5, 1, 1])
        with c_up:
            uploaded_file = st.file_uploader(
                "Upload Route Spreadsheet (.xlsx)", type=["xlsx", "xls"]
            )
        with c_s1:
            single_min = st.number_input(
                "Single-Line Stop (min)", min_value=1, max_value=240, value=15
            )
        with c_s2:
            dual_min = st.number_input(
                "Dual Stop (min)", min_value=1, max_value=240, value=15
            )

        if uploaded_file is not None:
            if st.button(
                "⚡ Balance & Optimize 7-Day Routes", type="primary", use_container_width=True
            ):
                with st.spinner("Pinpointing addresses & balancing Monday–Friday routes..."):
                    try:
                        st.session_state["route_plan"] = process_excel_and_balance(
                            uploaded_file, float(single_min), float(dual_min)
                        )
                        st.success("Routes balanced and ready across all 7 days!")
                    except Exception as e:
                        st.error(f"Could not process spreadsheet: {e}")

    plan = st.session_state.get("route_plan")
    if not plan:
        st.info(
            "👆 Upload your Excel file above and click **Balance & Optimize 7-Day Routes** to begin."
        )
        return

    depot_addr = plan["depot_address"]
    depot_coord = tuple(plan["depot_coord"])
    day_routes = plan["day_routes"]
    day_stats = plan["day_stats"]

    st.subheader("Weekly Workload Summary")
    st.caption(f"Start & End Depot: **{depot_addr}**")

    sum_df = pd.DataFrame(
        [
            {
                "Day": d,
                "Stops": day_stats[d]["stops"],
                "On-Site Time": f"{int(day_stats[d]['svc_min'] // 60)}h {int(day_stats[d]['svc_min'] % 60):02d}m",
                "Est. Drive": f"{int(day_stats[d]['drv_min'] // 60)}h {int(day_stats[d]['drv_min'] % 60):02d}m",
                "Total Shift": f"{int(day_stats[d]['tot_min'] // 60)}h {int(day_stats[d]['tot_min'] % 60):02d}m",
                "Est. Miles": f"{day_stats[d]['miles']:.1f} mi",
            }
            for d in DAYS
        ]
    )
    st.dataframe(sum_df, use_container_width=True, hide_index=True)

    st.divider()
    selected_day = st.selectbox("📅 Select Day to View Route, Map & Share:", DAYS, index=0)

    stops = day_routes.get(selected_day, [])
    stats = day_stats.get(selected_day, {})

    if not stops:
        st.info(f"No stops scheduled for {selected_day}.")
        return

    routexl_url = build_routexl_url(depot_addr, depot_coord, stops)
    sms_text = build_sms_text(selected_day, depot_addr, depot_coord, stops, stats)
    sms_href = f"sms:?&body={urllib.parse.quote(sms_text)}"

    b1, b2 = st.columns(2)
    with b1:
        st.link_button(
            f"🚀 Open {selected_day} in RouteXL",
            routexl_url,
            use_container_width=True,
            type="primary",
        )
    with b2:
        st.link_button(
            f"💬 Share {selected_day} via Text Message (SMS)",
            sms_href,
            use_container_width=True,
        )

    with st.expander("📋 Copy Route Text or RouteXL Link Manually"):
        st.text_area("Formatted Route Text (tap & copy):", value=sms_text, height=220)

    df_day = pd.DataFrame(
        [
            {
                "Stop #": idx,
                "Store": s["store"],
                "Address": s["address"],
                "Service Line": s["service_line"],
                "Min": int(s["duration"]),
                "Schedule Type": s["visit_type"],
            }
            for idx, s in enumerate(stops, start=1)
        ]
    )
    st.dataframe(df_day, use_container_width=True, hide_index=True)

    # Interactive Folium Map
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
    st_folium(m, height=430, use_container_width=True, key=f"map_{selected_day}")


if __name__ == "__main__":
    main()
