#!/usr/bin/env python3
"""
Hospital Reach Map - data builder. Run on YOUR laptop (needs internet; AWS creds for real isolines).

  pip install -r scripts/requirements.txt
  python scripts/build_data.py --city karachi --region ap-south-1   # full real build (slow, calls AWS)
  python scripts/build_data.py --city karachi --refresh             # FAST: reuse existing zones, re-do places + coverage
  python scripts/build_data.py --city karachi --demo                # fake data, to preview the UI

Output goes to frontend/data/:
  hospitals.geojson, ambulance.geojson, isolines.geojson, places.geojson, coverage.json
"""
import argparse, datetime, json, math, os, random, time
from shapely import affinity
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import unary_union

THRESHOLDS = [600, 1200, 1800]                      # seconds = 10/20/30 min
FALLBACK_KM = {600: 4.2, 1200: 8.3, 1800: 12.5}     # ~25 km/h urban average (used only if isolines fail)
GRID_STEP = 0.01                                    # degrees (~1.1 km)
REACH_BUFFER_DEG = 0.009                            # ~1 km: a spot within 1 km of a reachable road counts as reachable
POP_RADIUS_KM = 2.5                                 # a grid cell counts as "populated" if a named place or hospital is this close
CITIES = {  # lon_min, lat_min, lon_max, lat_max (urban area)
    "karachi":   (66.85, 24.80, 67.30, 25.10),
    "lahore":    (74.18, 31.38, 74.50, 31.65),
    "islamabad": (72.85, 33.50, 73.25, 33.80),
}
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HERE = os.path.dirname(os.path.abspath(__file__))


def hav_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = p2 - p1, math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def feature(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


# ---------------- 1. OpenStreetMap ----------------
def overpass(query):
    import requests
    headers = {"User-Agent": "hospital-reach-map/1.0 (AWS Builder challenge; student project)",
               "Accept": "application/json"}
    last = None
    for attempt in range(3):
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": query}, headers=headers, timeout=120)
                r.raise_for_status()
                return r.json()["elements"]
            except Exception as ex:
                last = ex
                print("Overpass failed:", url, ex)
        print("Waiting 20s before retry...")
        time.sleep(20)
    raise SystemExit(f"Could not reach Overpass: {last}")


def fetch_osm(bbox):
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    q = ("[out:json][timeout:90];(" +
         f'nwr["amenity"="hospital"]{box};nwr["healthcare"="hospital"]{box};' +
         f'nwr["amenity"="ambulance_station"]{box};nwr["emergency"="ambulance_station"]{box};' +
         ");out center tags;")
    return overpass(q)


def fetch_places(bbox):
    """Named areas (suburbs, towns, neighbourhoods) so people can tell WHERE things are."""
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    q = ('[out:json][timeout:90];node["place"~"^(city|town|suburb|city_district|neighbourhood|quarter|village)$"]["name"]'
         + box + ";out tags;")
    out, seen = [], set()
    for el in overpass(q):
        t = el.get("tags", {})
        name = t.get("name:en") or t.get("name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(feature(el["lon"], el["lat"], name=name, place=t.get("place", "suburb")))
    return out


def get_places(bbox, offline):
    """OSM place names; falls back to scripts/manual_places.json if Overpass is down (or --offline-places)."""
    places = []
    if not offline:
        try:
            places = fetch_places(bbox)
        except SystemExit:
            print("WARNING: Overpass unreachable for place names -> using manual_places.json instead")
    manual = os.path.join(HERE, "manual_places.json")
    if (offline or not places) and os.path.exists(manual):
        w, s, e, n = bbox
        for m in json.load(open(manual)):
            if w <= m["lon"] <= e and s <= m["lat"] <= n:
                places.append(feature(m["lon"], m["lat"], name=m["name"], place=m.get("place", "suburb")))
    return places


def classify(elements):
    hospitals, ambulance, seen = [], [], set()
    for el in elements:
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        t = el.get("tags", {})
        is_amb = "ambulance_station" in (t.get("amenity"), t.get("emergency"))
        is_h = "hospital" in (t.get("amenity"), t.get("healthcare"))
        if not (is_amb or is_h):
            continue
        kind = "ambulance" if (is_amb and not is_h) else "hospital"
        key = (kind, round(lat, 4), round(lon, 4))
        if key in seen:
            continue
        seen.add(key)
        name = t.get("name") or t.get("name:en") or ("Unnamed hospital" if kind == "hospital" else "Ambulance station")
        f = feature(lon, lat, name=name, kind=kind, er=(t.get("emergency") == "yes"))
        (hospitals if kind == "hospital" else ambulance).append(f)
    return hospitals, ambulance


def demo_points(bbox):
    rnd = random.Random(7)
    w, s, e, n = bbox
    def pt():
        return (rnd.uniform(w + 0.25 * (e - w), e - 0.2 * (e - w)), rnd.uniform(s + 0.25 * (n - s), n - 0.15 * (n - s)))
    hospitals = [feature(*pt(), name=f"Demo Hospital {i+1}", kind="hospital", er=(i % 3 == 0)) for i in range(22)]
    ambulance = [feature(*pt(), name=f"Demo Ambulance Station {i+1}", kind="ambulance", er=False) for i in range(7)]
    places = [feature(*pt(), name=f"Demo Area {i+1}", place="suburb") for i in range(12)]
    return hospitals, ambulance, places


# ---------------- 2. Drive-time zones ----------------
def isolines_for(client, lon, lat):
    resp = client.calculate_isolines(Origin=[lon, lat], Thresholds={"Time": THRESHOLDS},
                                     TravelMode="Car", IsolineGeometryFormat="Simple")
    out = {}
    for iso in resp["Isolines"]:
        polys = []
        for g in iso["Geometries"]:
            rings = g.get("Polygon")
            if rings:
                polys.append(Polygon(rings[0], rings[1:]).buffer(0))
        out[iso["TimeThreshold"]] = polys
    if not any(out.values()):
        raise RuntimeError("No polygons parsed. Geometry keys: " + str(list(resp["Isolines"][0]["Geometries"][0].keys())))
    return out


def radius_zone(lon, lat, km):
    circle = Point(lon, lat).buffer(1, 32)
    return affinity.scale(circle, xfact=km / (111.32 * math.cos(math.radians(lat))), yfact=km / 110.57)


def zones_fallback(hospitals):
    zones = {t: [] for t in THRESHOLDS}
    for f in hospitals:
        lon, lat = f["geometry"]["coordinates"]
        for t in THRESHOLDS:
            zones[t].append(radius_zone(lon, lat, FALLBACK_KM[t]))
    return zones, "estimated-radius"


def build_zones(hospitals, region, force_fallback):
    if force_fallback:
        return zones_fallback(hospitals)
    import boto3
    client = boto3.client("geo-routes", region_name=region)
    zones = {t: [] for t in THRESHOLDS}
    for i, f in enumerate(hospitals):
        lon, lat = f["geometry"]["coordinates"]
        try:
            for t, polys in isolines_for(client, lon, lat).items():
                zones[t].extend(polys)
        except Exception as ex:
            print("\nIsoline call failed:", ex)
            print("-> using estimated radius zones for ALL hospitals (the UI labels this)\n")
            return zones_fallback(hospitals)
        time.sleep(0.15)
        print(f"  isolines {i+1}/{len(hospitals)}")
    return zones, "drive-time"


# ---------------- 3. Coverage grid + output ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="karachi")
    ap.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max (overrides city preset)")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--max-hospitals", type=int, default=80)
    ap.add_argument("--demo", action="store_true", help="fake data + radius zones, no network/AWS")
    ap.add_argument("--no-isolines", action="store_true", help="skip AWS, use estimated radius zones")
    ap.add_argument("--refresh", action="store_true",
                    help="reuse existing hospitals/zones in the output folder; only re-fetch places and recompute coverage")
    ap.add_argument("--offline-places", action="store_true", help="skip Overpass for place names; use scripts/manual_places.json")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "frontend", "data"))
    a = ap.parse_args()

    bbox = tuple(float(x) for x in a.bbox.split(",")) if a.bbox else CITIES[a.city.lower()]
    os.makedirs(a.out, exist_ok=True)
    load = lambda name: json.load(open(os.path.join(a.out, name)))

    if a.refresh:
        hospitals = load("hospitals.geojson")["features"]
        ambulance = load("ambulance.geojson")["features"]
        iso_fc = load("isolines.geojson")
        unions = {f["properties"]["minutes"] * 60: shape(f["geometry"]) for f in iso_fc["features"]}
        method = load("coverage.json").get("method", "drive-time")
        places = get_places(bbox, a.offline_places)
        print(f"refresh: reusing {len(hospitals)} hospitals and existing zones ({method})")
    else:
        if a.demo:
            hospitals, ambulance, places = demo_points(bbox)
        else:
            hospitals, ambulance = classify(fetch_osm(bbox))
            manual = os.path.join(HERE, "manual_ambulance.json")
            if os.path.exists(manual):
                for m in json.load(open(manual)):
                    ambulance.append(feature(m["lon"], m["lat"], name=m["name"], kind="ambulance", er=False))
            places = get_places(bbox, a.offline_places)
        hospitals = hospitals[: a.max_hospitals]
        if not hospitals:
            raise SystemExit("No hospitals found. Check --bbox / --city.")
        zones, method = build_zones(hospitals, a.region, a.demo or a.no_isolines)
        unions = {t: unary_union(p) for t, p in zones.items()}
        iso_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": mapping(unions[t]), "properties": {"minutes": t // 60}}
            for t in sorted(THRESHOLDS, reverse=True)]}
    print(f"{len(hospitals)} hospitals, {len(ambulance)} ambulance stations, {len(places)} named places; zones: {method}")

    # ---- display bands: NON-overlapping rings (0-10, 10-20, 20-30) so colours do not blend
    bands_fc, prev = {"type": "FeatureCollection", "features": []}, None
    for t in sorted(unions):
        g = unions[t] if prev is None else unions[t].difference(prev).buffer(0)
        bands_fc["features"].append({"type": "Feature", "geometry": mapping(g), "properties": {"minutes": t // 60}})
        prev = unions[t]
    bands_fc["features"].reverse()

    # ---- reach = drive-time zone + ~1 km buffer (spots slightly off the road network are still reachable)
    reach = {t: u.buffer(REACH_BUFFER_DEG).simplify(0.0003) for t, u in unions.items()}
    reach_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(reach[t]), "properties": {"minutes": t // 60}}
        for t in sorted(reach, reverse=True)]}

    # ---- grid: only POPULATED cells count (near a named place or a hospital), so sea / empty land is ignored
    w, s, e, n = bbox
    hpos = [f["geometry"]["coordinates"] for f in hospitals]
    ppos = [f["geometry"]["coordinates"] for f in places]
    anchors = hpos + ppos
    cells, y = [], s
    while y < n:
        x = w
        while x < e:
            cx, cy = x + GRID_STEP / 2, y + GRID_STEP / 2
            if min(hav_km((cx, cy), p) for p in anchors) <= POP_RADIUS_KM:
                p = Point(cx, cy)
                band = next((t // 60 for t in sorted(THRESHOLDS) if reach[t].contains(p)), None)
                cells.append({"lon": round(cx, 4), "lat": round(cy, 4), "minutes": band,
                              "km": min(hav_km((cx, cy), h) for h in hpos)})
            x += GRID_STEP
        y += GRID_STEP

    total = len(cells)
    share = lambda m: round(100 * sum(1 for c in cells if c["minutes"] is not None and c["minutes"] <= m) / total, 1)
    far = max(cells, key=lambda c: c["km"])
    coverage = {
        "city": a.city.title(), "method": method, "demo": a.demo, "bbox": bbox, "grid_step": GRID_STEP,
        "area_basis": f"populated cells (within {POP_RADIUS_KM} km of a named place or hospital)",
        "populated_cells": total, "reach_buffer_km": 1.0,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "hospitals": len(hospitals), "hospitals_er": sum(1 for f in hospitals if f["properties"]["er"]),
        "ambulance_stations": len(ambulance), "places": len(places),
        "area_within_10_min_pct": share(10), "area_within_20_min_pct": share(20), "area_within_30_min_pct": share(30),
        "desert_pct": round(100 * sum(1 for c in cells if c["minutes"] is None) / total, 1),
        "most_isolated": {"lon": far["lon"], "lat": far["lat"], "km": round(far["km"], 1)},
        "desert_cells": [{"lon": c["lon"], "lat": c["lat"]} for c in cells if c["minutes"] is None],
    }

    def dump(name, obj):
        with open(os.path.join(a.out, name), "w") as fh:
            json.dump(obj, fh)
    dump("hospitals.geojson", {"type": "FeatureCollection", "features": hospitals})
    dump("ambulance.geojson", {"type": "FeatureCollection", "features": ambulance})
    dump("isolines.geojson", iso_fc)
    dump("reach.geojson", reach_fc)
    dump("bands.geojson", bands_fc)
    dump("places.geojson", {"type": "FeatureCollection", "features": places})
    dump("coverage.json", coverage)
    print({k: v for k, v in coverage.items() if k != "desert_cells"})
    print("Done ->", os.path.abspath(a.out))


if __name__ == "__main__":
    main()