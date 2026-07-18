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
built from **OpenStreetMap** street geometries around Bangkok’s
**Lumphini Park ↔ Benjakitti Park** (including The Green Mile / สะพานเขียว).
A Las Vegas backup is at `assets/network-las-vegas.json`.

Key features in the Bangkok network:
- **Landmarks**: Point features snapped onto the road graph (e.g., Lumphini Park,
  Lumphini Park South Gate, Sarasin Junction, Benjakitti Park, Asok BTS,
  Queen Sirikit National Convention Center, The Green Mile, etc.).
- **Named Roads**: Real OSM LineStrings with `properties.name` (e.g.,
  Witthayu Road, Rama IV Road, Ratchadamri Road, Asok Montri Road,
  Sarasin Road, Sathon Tai Road, The Green Mile, park footways/cycleways).

## Algorithm

The default algorithm is **zone-sweep**: the route starts near
`Lumphini Park South Gate`, travels the Witthayu (Wireless) corridor toward
`Sarasin Junction`, sweeps city zones, and finishes at `Benjakitti Park`.
The algorithm handles route geometry automatically — you do not need to
select petals or manually sequence landmarks.

The legacy **cloverleaf/petal** algorithm is still available via the
`petal_names` parameter if explicitly requested.

## Instructions

1. **Just call it**: `plan_marathon_route()` with no arguments produces a valid
   42.195 km zone-sweep route. Use `start_landmark` and `seed` for variety.
2. **Precision**: The tool targets exactly 42.195 km (stored as 26.2188 mi internally).
3. **GeoJSON**: Input must be valid road network GeoJSON.
4. **Report in km**: Summaries, station spacing, and route length must use **km**.

## Tools

- `plan_marathon_route(algorithm: str = "zone_sweep", start_landmark: Optional[str] = None, seed: Optional[int] = None, petal_names: Optional[list[str]] = None, geojson_data: Optional[str] = None)`:
  Generate the exact 42.195 km path.
  - `algorithm`: `"zone_sweep"` (default) or `"cloverleaf"`.
  - `start_landmark`: Name of a landmark to start near (e.g., `"Benjakitti Park"`). Zone-sweep only.
  - `seed`: Integer seed for route variety. Different seeds produce different routes. Zone-sweep only.
  - `petal_names`: List of petal names. Cloverleaf algorithm only.
- `add_water_stations(route_geojson: dict)`: Append water station features (about every 5 km).
- `add_medical_tents(route_geojson: dict)`: Append medical tent features.
- `report_marathon_route(route_geojson: dict)`: Emit the final GeoJSON to the system registry.

### Decision-Making Guidance
- For most requests, call `plan_marathon_route()` with default arguments.
- Use `seed` to generate alternative routes when the user wants variety.
- Use `start_landmark` when the user specifies a preferred starting area.
- Only use `petal_names` if the user explicitly asks for the cloverleaf/petal approach.
- Always present distances to the user in **km**.
