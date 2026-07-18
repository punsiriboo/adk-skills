---
name: gis-spatial-engineering
description:
  Expert GIS tools for generating mathematically perfect marathon routes of exactly
  42.195 km using road network data and zone-sweep decomposition. Always report
  distances in kilometers (km).
metadata:
  adk_additional_tools:
    - plan_marathon_route
    - report_marathon_route
    - assess_traffic_impact
---

# Route Planning

You use this skill to generate the physical path of the marathon.

## Units (important)

- **Always use kilometers (km)** when talking to the user — never miles.
- Official marathon distance: **42.195 km** (equivalent to 26.2 miles internally).
- Half marathon: **21.0975 km**.
- Hydration stations: about every **5 km**.
- When tools return `mi` / `distance_mi`, convert to km before answering:
  `km = mi × 1.60934`. Round to 1–2 decimal places (e.g. `42.20 km`).

## Geographic Context (Built-in Data)
You have access to a road network GeoJSON located at `assets/network.json`
clipped to **Lumphini Park ↔ Benjakitti Park** (Ratchaprasong / Nana / Rama IV frame),
from OpenStreetMap only — no paid Maps API. Las Vegas backup:
`assets/network-las-vegas.json`.

Key features:
- **Landmarks**: Lumphini Park, Lumphini Park South Gate, Sarasin Junction,
  Benjakitti Park, The Green Mile, Asok BTS, Ratchaprasong, QSNCC, etc.
- **Named Roads**: Witthayu Road, Rama IV Road, Sarasin Road, park paths,
  The Green Mile / สะพานเขียว.

## Algorithm

The default algorithm is **zone-sweep**: the route starts near
`Lumphini Park South Gate`, travels the Witthayu corridor toward
`Sarasin Junction`, and finishes at `Benjakitti Park`.
The algorithm handles route geometry automatically — you do not need to
select petals or manually sequence landmarks.

The legacy **cloverleaf/petal** algorithm is still available via the
`petal_names` parameter if explicitly requested.

## Instructions

1. **Just call it**: `plan_marathon_route()` with no arguments produces a valid
   Bangkok park-connector route (~**10 km** target for this clipped network).
   Use `start_landmark` and `seed` for variety.
2. **Precision**: For this Lumphini–Benjakitti frame the planner targets **10 km**
   (not a full 42.195 km marathon — the map area is too small for that without
   heavy backtracking). Report the actual `distance_km` from the tool summary.
3. **GeoJSON**: Input must be valid road network GeoJSON.
4. **Report in km**: Summaries, station spacing, and route length must use **km**.
5. After planning, **always** call `report_marathon_route` for the map artifact.
## Tools

- `plan_marathon_route(algorithm: str = "zone_sweep", start_landmark: Optional[str] = None, seed: Optional[int] = None, petal_names: Optional[list[str]] = None, geojson_data: Optional[str] = None)`:
  Generate the exact 42.195 km path.
  - `algorithm`: `"zone_sweep"` (default) or `"cloverleaf"`.
  - `start_landmark`: Name of a landmark to start near (e.g., `"Benjakitti Park"`). Zone-sweep only.
  - `seed`: Integer seed for route variety. Different seeds produce different routes. Zone-sweep only.
  - `petal_names`: List of petal names. Cloverleaf algorithm only.
- `add_water_stations(route_geojson: dict)`: Append water station features (about every 5 km).
- `add_medical_tents(route_geojson: dict)`: Append medical tent features.
- `report_marathon_route(route_geojson: dict)`: Emit the final GeoJSON **and**
  save an interactive Leaflet map as ADK HTML artifact `marathon_route_map.html`
  so it renders in `adk web`. Always call this after planning so the user can see the map.

### Decision-Making Guidance
- For most requests, call `plan_marathon_route()` with default arguments.
- Use `seed` to generate alternative routes when the user wants variety.
- Use `start_landmark` when the user specifies a preferred starting area.
- Only use `petal_names` if the user explicitly asks for the cloverleaf/petal approach.
- Always present distances to the user in **km**.
- After a successful plan, **always** call `report_marathon_route` so the map artifact appears in the UI.