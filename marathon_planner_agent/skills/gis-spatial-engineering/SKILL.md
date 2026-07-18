---
name: gis-spatial-engineering
description:
  Expert GIS tools for generating runnable routes (casual jog, training loop,
  5K/10K, Lumphini heart shape, park connector, or race course) on the Bangkok
  Lumphini↔Benjakitti road network. Always report distances in kilometers (km).
  Not limited to marathons.
metadata:
  adk_additional_tools:
    - plan_marathon_route
    - report_marathon_route
    - assess_traffic_impact
---

# Route Planning

You use this skill to generate a **physical running path** of any reasonable
shape for the built-in Bangkok park network — not only marathons.

## Units (important)

- **Always use kilometers (km)** when talking to the user — never miles.
- Common distances (examples, not requirements):
  - Easy park loop / connector: ~5–12 km (default planner target ≈ **10 km**)
  - 5K ≈ 5.0 km · 10K ≈ 10.0 km · half ≈ 21.1 km · marathon ≈ 42.195 km
- When tools return `mi` / `distance_mi`, convert: `km = mi × 1.60934`.

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

- **Default (Bangkok):** park-connector landmark chain
  (`Lumphini` → Witthayu / Sarasin → Green Mile → `Benjakitti`), with optional
  loops for distance. Vary with `seed` and `start_landmark`.
- **Heart inside Lumphini only:** `route_shape="heart"` (or `algorithm="park_heart"`).
  Stays on park footpaths (no Rama IV / Witthayu arterials), traces a heart
  loop (~2.5–3 km per lap) and repeats until `target_distance_km` (e.g. 10).
  Use this when the user asks for รูปหัวใจ / ภายในสวนลุมพินีเท่านั้น.

Legacy **zone-sweep** / **cloverleaf** params still exist on the tool but are
Las Vegas–oriented; prefer defaults or `route_shape="heart"` for Bangkok park runs.

## Instructions

1. Call `plan_marathon_route(...)` with the right shape:
   - Heart / Lumphini-only → `route_shape="heart", target_distance_km=10`
   - General park connector → defaults (`start_landmark` / `seed` optional)
2. Report the actual `distance_km` from the tool summary — do not invent 42.195 km
   unless the route really is that long.
3. After planning, **always** call `report_marathon_route` for the map artifact.
4. If the user only wants a route map (not a race), do not push event logistics.

## Tools

- `plan_marathon_route(route_shape: Optional[str] = None, target_distance_km: Optional[float] = None, start_landmark: Optional[str] = None, seed: Optional[int] = None, algorithm: str = "zone_sweep", ...)`:
  Generate a runnable path on the loaded network.
  - `route_shape="heart"`: multi-lap heart **inside Lumphini Park only**.
  - `target_distance_km`: e.g. `10` for a 10K (default ≈ 10 km on Bangkok net).
  - `start_landmark`: e.g. `"Lumphini Park South Gate"`.
  - `seed`: Integer for route variety.
- `report_marathon_route()`: Save interactive Leaflet map artifact
  `marathon_route_map_<seed>.html` for ADK Web (new file each replan).

### Decision-Making Guidance
- Casual run / training → plan + map + short summary.
- **Heart / ในสวนลุมเท่านั้น** → must use `route_shape="heart"` (do not use park-connector).
- Race event → plan + map, then race-director skill for logistics.
- Always present distances in **km**.
- Always call `report_marathon_route` after a successful plan.
