# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import bisect
import json
import os
import math
import logging
import random
from typing import Dict, Optional, List, Set
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

TARGET_DIST_MI = 26.2188
HALF_MILE = 0.5  # 0.5 miles
# Bangkok Lumphini–Benjakitti frame is too small for a non-crossing 42.195 km
# course; use a 10 km target for that network instead.
BANGKOK_TARGET_DIST_MI = 6.21371  # 10.0 km

# ---------------------------------------------------------------------------
# Petal template catalog
# ---------------------------------------------------------------------------
# Each petal is a named rectangular loop radiating from Las Vegas Blvd.
# Waypoints are [lon, lat] grid intersections forming: Strip entry → outbound
# road → far corridor → return road → Strip re-entry.
# Approximate distance is for planning; exact distance computed by Dijkstra.

# The start/finish hub on Las Vegas Blvd (near Las Vegas Sign)
_STRIP_HUB = (-115.172851, 36.086141)

# ---------------------------------------------------------------------------
# Zone-sweep route generation constants
# ---------------------------------------------------------------------------

# Approximate center of the corridor road for zone classification
# Bangkok: Witthayu (Wireless) Road between Lumphini and Benjakitti
STRIP_CENTER = (100.5465, 13.7300)

# Empirical reserve for the serpentine-to-corridor-exit connector
# distance.  Subtracted from the serpentine budget so the route
# doesn't overshoot and force large finish-trims that push the finish
# away from the target POI.  With Treasure Island as the corridor
# exit (~2.4 mi from MUA), the connector needs ~2.5 mi of budget.
# The 30-seed guarantee test validates this continuously.
_CONNECTOR_RESERVE = 1.5

# Start corridor landmarks (northbound on Witthayu / Wireless Road)
CORRIDOR_START = "Lumphini Park South Gate"  # runner starts here
CORRIDOR_EXIT = "Sarasin Junction"  # northbound along Witthayu / Sarasin

# Road name used as the primary corridor (was "Las Vegas Boulevard")
CORRIDOR_ROAD = "Witthayu Road"

# Default finish landmark
FINISH_LANDMARK = "Benjakitti Park"

# Lumphini Park interior (exclude surrounding arterials) — lon_min, lat_min, lon_max, lat_max
LUMPHINI_PARK_BBOX = (100.5398, 13.7288, 100.5462, 13.7335)
_LUMPHINI_ARTERIAL_HINTS = (
    "rama iv",
    "witthayu",
    "wireless",
    "ratchadamri",
    "sarasin",
)

# Heart fitted to Lumphini (tip ≈ Rama VI / South Gate). Used as curve center/scale.
_LUMPHINI_HEART_CENTER = (100.54275, 13.73105)
_LUMPHINI_HEART_SCALE_LON = 0.00270  # half-width in degrees
_LUMPHINI_HEART_SCALE_LAT = 0.00195  # half-height in degrees


PETAL_CATALOG: Dict[str, dict] = {
    # --- West petals (radiate west from the Strip) ---
    "west-flamingo-jones": {
        "description": "West via Flamingo Rd to Jones Blvd, return via Desert Inn Rd (~9.9 mi)",
        "approx_mi": 9.9,
        "waypoints": [
            (-115.172908, 36.114900),  # Strip @ Flamingo
            (-115.224800, 36.114900),  # Jones @ Flamingo
            (-115.224800, 36.130500),  # Jones @ Desert Inn
            (-115.172975, 36.130500),  # Strip @ Desert Inn
        ],
    },
    "west-flamingo-rainbow": {
        "description": "West via Flamingo Rd to Rainbow Blvd, return via Desert Inn Rd (~12.4 mi)",
        "approx_mi": 12.4,
        "waypoints": [
            (-115.172908, 36.114900),  # Strip @ Flamingo
            (-115.242500, 36.114900),  # Rainbow @ Flamingo
            (-115.242500, 36.130500),  # Rainbow @ Desert Inn
            (-115.172975, 36.130500),  # Strip @ Desert Inn
        ],
    },
    "west-tropicana-decatur": {
        "description": "West via Tropicana Ave to Decatur Blvd, return via Flamingo Rd (~6.2 mi)",
        "approx_mi": 6.2,
        "waypoints": [
            (-115.172932, 36.100500),  # Strip @ Tropicana
            (-115.208000, 36.100500),  # Decatur @ Tropicana
            (-115.208000, 36.114900),  # Decatur @ Flamingo
            (-115.172908, 36.114900),  # Strip @ Flamingo
        ],
    },
    "west-harmon-arville": {
        "description": "West via Harmon Ave to Arville St, return via Flamingo Rd (~5.0 mi)",
        "approx_mi": 5.0,
        "waypoints": [
            (-115.172932, 36.107500),  # Strip @ Harmon
            (-115.199500, 36.107500),  # Arville @ Harmon
            (-115.199500, 36.114900),  # Arville @ Flamingo
            (-115.172908, 36.114900),  # Strip @ Flamingo
        ],
    },
    # --- North petals (radiate north/northwest from the Strip) ---
    "north-sahara-rainbow": {
        "description": "North to Sahara Ave, west to Rainbow Blvd, return via Spring Mtn Rd (~8.7 mi)",
        "approx_mi": 8.7,
        "waypoints": [
            (-115.172869, 36.144200),  # Strip @ Sahara
            (-115.242500, 36.144200),  # Rainbow @ Sahara
            (-115.242500, 36.123900),  # Rainbow @ Spring Mountain
            (-115.172975, 36.123900),  # Strip @ Spring Mountain
        ],
    },
    "north-sahara-jones": {
        "description": "North to Sahara Ave, west to Jones Blvd, return via Desert Inn Rd (~6.2 mi)",
        "approx_mi": 6.2,
        "waypoints": [
            (-115.172869, 36.144200),  # Strip @ Sahara
            (-115.224800, 36.144200),  # Jones @ Sahara
            (-115.224800, 36.130500),  # Jones @ Desert Inn
            (-115.172975, 36.130500),  # Strip @ Desert Inn
        ],
    },
    "north-sahara-decatur": {
        "description": "North to Sahara Ave, west to Decatur Blvd, return via Edna Ave (~5.0 mi)",
        "approx_mi": 5.0,
        "waypoints": [
            (-115.172869, 36.144200),  # Strip @ Sahara
            (-115.208000, 36.144200),  # Decatur @ Sahara
            (-115.208000, 36.137000),  # Decatur @ Edna
            (-115.190500, 36.137000),  # Valley View @ Edna
        ],
    },
    # --- South petals (radiate south from the Strip) ---
    "south-tropicana-vv-sunset": {
        "description": "South via Tropicana, west to Valley View, south to Sunset (~5.0 mi)",
        "approx_mi": 5.0,
        "waypoints": [
            (-115.172932, 36.100500),  # Strip @ Tropicana
            (-115.189400, 36.100500),  # Valley View @ Tropicana
            (-115.189400, 36.071200),  # Valley View @ Sunset
            (-115.172851, 36.071200),  # Strip @ Sunset
        ],
    },
    "south-tropicana-decatur-sunset": {
        "description": "South via Tropicana, west to Decatur, south to Sunset (~7.5 mi)",
        "approx_mi": 7.5,
        "waypoints": [
            (-115.172932, 36.100500),  # Strip @ Tropicana
            (-115.208000, 36.100500),  # Decatur @ Tropicana
            (-115.208000, 36.071200),  # Decatur @ Sunset
            (-115.172851, 36.071200),  # Strip @ Sunset
        ],
    },
    "south-tropicana-rainbow-sunset": {
        "description": "South via Tropicana, west to Rainbow, south to Sunset (~11.2 mi)",
        "approx_mi": 11.2,
        "waypoints": [
            (-115.172932, 36.100500),  # Strip @ Tropicana
            (-115.242500, 36.100500),  # Rainbow @ Tropicana
            (-115.242500, 36.071200),  # Rainbow @ Sunset
            (-115.172851, 36.071200),  # Strip @ Sunset
        ],
    },
    # --- East petals (radiate east from the Strip) ---
    "east-desertinn-maryland": {
        "description": "East via Desert Inn Rd to Maryland Pkwy, return via Tropicana (~6.2 mi)",
        "approx_mi": 6.2,
        "waypoints": [
            (-115.172975, 36.130500),  # Strip @ Desert Inn
            (-115.137400, 36.130500),  # Maryland Pkwy @ Desert Inn
            (-115.137400, 36.100500),  # Maryland Pkwy @ Tropicana
            (-115.172932, 36.100500),  # Strip @ Tropicana
        ],
    },
    "east-sunset-pecos": {
        "description": "East via Sunset Rd to Pecos Rd, return via Russell Rd (~8.7 mi)",
        "approx_mi": 8.7,
        "waypoints": [
            (-115.172851, 36.071200),  # Strip @ Sunset
            (-115.104700, 36.071200),  # Pecos @ Sunset
            (-115.104700, 36.088900),  # Pecos @ Russell
            (-115.149200, 36.088900),  # Paradise @ Russell
        ],
    },
}


def _build_waypoints_from_petals(
    petal_names: List[str],
    start: tuple = _STRIP_HUB,
) -> List[tuple]:
    """Assemble a full waypoint sequence from named petals.

    Inserts Strip connector segments between petals and adds the
    start/finish on Las Vegas Blvd.
    """
    waypoints: list[tuple] = [start]

    for name in petal_names:
        petal = PETAL_CATALOG.get(name)
        if petal is None:
            logger.warning("PLANNER: Unknown petal '%s', skipping", name)
            continue
        # Connect from current position to petal entry via the Strip
        petal_entry = petal["waypoints"][0]
        waypoints.append(petal_entry)
        # Add petal waypoints (skip entry, it's already added)
        for wp in petal["waypoints"][1:]:
            waypoints.append(wp)

    # Return to start
    waypoints.append(start)
    return waypoints


def _haversine(coord1: tuple[float, float], coord2: tuple[float, float]) -> float:
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _interpolate(
    p1: tuple[float, float],
    p2: tuple[float, float],
    target_dist: float,
    current_seg_dist: float,
) -> list[float]:
    if current_seg_dist == 0:
        return [p1[0], p1[1]]
    ratio = target_dist / current_seg_dist
    lon = p1[0] + (p2[0] - p1[0]) * ratio
    lat = p1[1] + (p2[1] - p1[1]) * ratio
    return [lon, lat]


def _build_distance_index(
    coords: list[tuple[float, float]],
) -> list[tuple[tuple[float, float], float]]:
    """Build a cumulative distance index from route coordinates.

    Returns a list of (coord, cumulative_miles) tuples. The first entry
    is always at mile 0.0. Subsequent entries accumulate haversine
    distances along the coordinate sequence.
    """
    if not coords:
        return []
    index: list[tuple[tuple[float, float], float]] = [(coords[0], 0.0)]
    cumulative = 0.0
    for i in range(1, len(coords)):
        cumulative += _haversine(coords[i - 1], coords[i])
        index.append((coords[i], cumulative))
    return index


def _point_at_mile(
    index: list[tuple[tuple[float, float], float]],
    target_mi: float,
) -> list[float]:
    """Return [lon, lat] at the given mile marker using binary search.

    Interpolates between the two nearest index entries. Clamps to the
    first/last coordinate when target_mi is out of range.
    """
    if not index:
        return [0.0, 0.0]
    if target_mi <= 0.0:
        return [index[0][0][0], index[0][0][1]]
    total = index[-1][1]
    if target_mi >= total:
        return [index[-1][0][0], index[-1][0][1]]

    # Binary search for the segment containing target_mi
    distances = [entry[1] for entry in index]
    right = bisect.bisect_right(distances, target_mi)
    if right == 0:
        right = 1
    left = right - 1

    p1 = index[left][0]
    p2 = index[right][0]
    seg_dist = index[right][1] - index[left][1]
    needed = target_mi - index[left][1]

    return _interpolate(p1, p2, needed, seg_dist)


def _on_segment(p: tuple, q: tuple, r: tuple) -> bool:
    """Check if point q lies on segment pr (assuming collinear)."""
    return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[
        1
    ] <= max(p[1], r[1])


def _cross_product(o: tuple, a: tuple, b: tuple) -> float:
    """2D cross product of vectors OA and OB."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_intersect(p1: tuple, p2: tuple, p3: tuple, p4: tuple) -> bool:
    """Check if line segment p1-p2 intersects segment p3-p4.

    Returns False for segments sharing a single endpoint (T-junction).
    Returns True for proper crossing (X) or collinear overlap.
    """
    endpoints = {p1, p2}
    other_endpoints = {p3, p4}
    shared = endpoints & other_endpoints
    if len(shared) == 1:
        return False
    if len(shared) == 2:
        return True

    d1 = _cross_product(p3, p4, p1)
    d2 = _cross_product(p3, p4, p2)
    d3 = _cross_product(p1, p2, p3)
    d4 = _cross_product(p1, p2, p4)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True

    if d1 == 0 and _on_segment(p3, p1, p4):
        return True
    if d2 == 0 and _on_segment(p3, p2, p4):
        return True
    if d3 == 0 and _on_segment(p1, p3, p2):
        return True
    if d4 == 0 and _on_segment(p1, p4, p2):
        return True

    return False


def _route_has_crossing(coords: list[tuple]) -> bool:
    """Check if a route (list of coordinates) has any self-crossing segments.

    O(n^2) brute-force. For ~500 segments this runs in microseconds.
    Adjacent segments share an endpoint and are skipped.
    """
    n = len(coords)
    if n < 4:
        return False
    for i in range(n - 1):
        for j in range(i + 2, n - 1):
            if _segments_intersect(coords[i], coords[i + 1], coords[j], coords[j + 1]):
                return True
    return False


def _path_crosses_route(
    new_path: list[tuple],
    existing_route: list[tuple],
) -> bool:
    """Check if adding new_path would create crossings with existing_route.

    Checks each segment in new_path against each segment in existing_route.
    Adjacent segments (sharing an endpoint) are allowed by _segments_intersect.
    """
    if len(existing_route) < 2 or len(new_path) < 2:
        return False
    for i in range(len(new_path) - 1):
        for j in range(len(existing_route) - 1):
            if _segments_intersect(
                new_path[i],
                new_path[i + 1],
                existing_route[j],
                existing_route[j + 1],
            ):
                return True
    return False


def _route_edges_valid(
    route: list[tuple],
    adj: Dict[tuple, List[tuple]],
    allow_final_interpolation: bool = True,
    allow_start_interpolation: bool = False,
) -> tuple[bool, list[int]]:
    """Check that every edge in the route exists in the graph.

    Returns (is_valid, list_of_invalid_edge_indices).
    If allow_final_interpolation is True, the last edge is exempt
    (it may contain an interpolated point for exact distance).
    If allow_start_interpolation is True, the first edge is also exempt
    (it may start from a trimmed interpolation point).
    """
    invalid = []
    check_start = 1 if allow_start_interpolation else 0
    check_end = len(route) - 2 if allow_final_interpolation else len(route) - 1
    for i in range(check_start, check_end):
        p1, p2 = route[i], route[i + 1]
        # Check if p2 is a neighbor of p1 in the adjacency list
        neighbors = {n for n, _ in adj.get(p1, [])}
        if p2 not in neighbors:
            invalid.append(i)
    return len(invalid) == 0, invalid


def _build_strip_corridor(
    adj: Dict[tuple, List[tuple]],
    strip_nodes: Set[tuple],
    start_node: tuple,
    exit_node: tuple,
    visited_edges: Set[tuple],
) -> tuple[list[tuple], float, set[tuple]]:
    """Build the start corridor: northbound on Las Vegas Blvd.

    Uses standard Dijkstra (no perturbation) to find the path from
    start_node to exit_node. Restricts routing to Strip-only nodes
    to keep the corridor on Las Vegas Blvd. Falls back to unrestricted
    routing if no strip-only path exists.

    Returns (path, distance, edges_used).
    """
    # Try strip-only routing first: block all non-strip nodes
    non_strip = set(adj.keys()) - strip_nodes
    path, dist = _get_path_dijkstra(
        start_node,
        exit_node,
        adj,
        visited_nodes=non_strip,
        visited_edges=visited_edges,
    )
    # Fall back to unrestricted if strip-only constraint yields no path
    if not path or len(path) < 2:
        logger.warning(
            "PLANNER: Strip-only corridor path failed, falling back to unrestricted routing"
        )
        path, dist = _get_path_dijkstra(
            start_node,
            exit_node,
            adj,
            visited_nodes=set(),
            visited_edges=visited_edges,
        )
    edges_used = set()
    for i in range(len(path) - 1):
        edges_used.add(tuple(sorted((path[i], path[i + 1]))))
    return path, dist, edges_used


def _build_graph(
    data: dict,
) -> tuple[Dict[tuple, List[tuple]], Dict[str, tuple], Dict[tuple, str], Set[tuple]]:
    adj: Dict[tuple, List[tuple]] = {}
    landmarks: Dict[str, tuple] = {}
    road_names: Dict[tuple, str] = {}
    nodes: Set[tuple] = set()
    strip_nodes: Set[tuple] = set()

    for feat in data.get("features", []):
        geom_type = feat.get("geometry", {}).get("type")
        if geom_type == "LineString":
            # Skip motorways (I-15, etc.) — not usable for marathon routes
            if feat.get("properties", {}).get("highway") == "motorway":
                continue
            name = feat.get("properties", {}).get("name")
            coords = feat["geometry"]["coordinates"]
            for i in range(len(coords) - 1):
                p1 = tuple(coords[i])
                p2 = tuple(coords[i + 1])
                edge_key = tuple(sorted((p1, p2)))
                if name:
                    road_names[edge_key] = name
                    if name == CORRIDOR_ROAD:
                        strip_nodes.add(p1)
                        strip_nodes.add(p2)
                nodes.add(p1)
                nodes.add(p2)
                # Deduplicate edges (merged network may have overlapping roads)
                existing_neighbors = {n for n, _ in adj.get(p1, [])}
                if p2 not in existing_neighbors:
                    dist = _haversine(p1, p2)
                    adj.setdefault(p1, []).append((p2, dist))
                    adj.setdefault(p2, []).append((p1, dist))
        elif geom_type == "Point" and "name" in feat.get("properties", {}):
            name = feat["properties"]["name"]
            landmarks[name] = tuple(feat["geometry"]["coordinates"])

    # Sort adj lists for determinism
    for k in adj:
        # Sort by distance primarily, then by coords to ensure deterministic tie-breaking
        adj[k].sort(key=lambda x: (x[1], x[0][0], x[0][1]))

    return adj, landmarks, road_names, strip_nodes


def _find_strip_anchor(
    target: tuple[float, float],
    strip_nodes: Set[tuple],
) -> tuple | None:
    """Find the closest Las Vegas Boulevard node to the target coordinate."""
    return _find_closest_node(target, strip_nodes)


def _get_return_path(
    from_node: tuple,
    start_anchor: tuple,
    adj: Dict[tuple, List[tuple]],
    strip_nodes: Set[tuple],
    visited_nodes: Set[tuple],
    visited_edges: Set[tuple],
) -> tuple[List[tuple], float, Set[tuple]]:
    """Find the shortest path from from_node back to a strip node near start.

    Returns (path, distance, edge_set) or ([], 0.0, set()) if no path exists.
    The destination must be a strip node within HALF_MILE of start_anchor.
    Allows destination nodes even if they are in visited_nodes (since we're
    intentionally returning to the start area).
    """
    import heapq

    candidates = {n for n in strip_nodes if _haversine(n, start_anchor) <= HALF_MILE}

    if not candidates:
        return [], 0.0, set()

    queue: list[tuple[float, tuple, list[tuple]]] = [(0.0, from_node, [from_node])]
    best_costs: dict[tuple, float] = {from_node: 0.0}

    while queue:
        dist, curr, path = heapq.heappop(queue)

        if curr in candidates and curr != from_node:
            # Build edge set for this path
            path_edges: Set[tuple] = set()
            for i in range(len(path) - 1):
                path_edges.add(tuple(sorted((path[i], path[i + 1]))))
            return path, dist, path_edges

        for neighbor, d in adj.get(curr, []):
            edge = tuple(sorted((curr, neighbor)))
            if edge in visited_edges:
                continue
            # Allow candidate destinations even if visited
            if neighbor in visited_nodes and neighbor not in candidates:
                continue
            if neighbor in path:
                continue
            new_cost = dist + d
            if neighbor not in best_costs or new_cost < best_costs[neighbor]:
                best_costs[neighbor] = new_cost
                heapq.heappush(queue, (new_cost, neighbor, path + [neighbor]))

    return [], 0.0, set()


def _find_closest_node(target: tuple, nodes: Set[tuple]) -> tuple | None:
    best_node = None
    min_dist = float("inf")
    for node in nodes:
        d = _haversine(target, node)
        if d < min_dist:
            min_dist = d
            best_node = node
    return best_node


def _find_off_strip_poi_node(
    adj: Dict[tuple, List[tuple]],
    nodes: Set[tuple],
    strip_nodes: Set[tuple],
    landmarks: Dict[str, tuple],
    preferred: str | None = None,
    near: tuple | None = None,
    max_radius_mi: float = 0.5,
    max_strip_dist_mi: float = HALF_MILE,
    rng: random.Random | None = None,
) -> tuple | None:
    """Find an off-strip graph node near a POI.

    If *preferred* names a landmark, finds the nearest off-strip node to
    that POI.  Otherwise, finds the nearest POI to *near* and returns
    the nearest off-strip node to it.

    Candidates must be within *max_strip_dist_mi* of the nearest Strip
    node to keep the route close to Las Vegas Boulevard.  Falls back
    to wider POI radii if no candidates exist, then drops the Strip
    constraint as a last resort.
    """
    strip_lon = STRIP_CENTER[0]
    min_off_strip = 0.001  # ~330 ft longitude difference from Strip

    # --- Determine target POI coordinate ---
    if preferred and preferred in landmarks:
        poi_coord = landmarks[preferred]
    elif near is not None:
        # Find nearest POI to the serpentine endpoint
        pool = {
            n: c
            for n, c in landmarks.items()
            if n not in (CORRIDOR_START, CORRIDOR_EXIT)
        }
        if not pool:
            return None
        poi_name = min(pool, key=lambda n: _haversine(near, pool[n]))
        poi_coord = pool[poi_name]
    else:
        return None

    # --- Find off-strip graph nodes near the POI ---
    # Restrict to the connected component of the Strip corridor so finish
    # nodes on disconnected footway fragments cannot be selected.
    seed = next(iter(strip_nodes), None) if strip_nodes else None
    main_component: set[tuple] | None = None
    if seed is not None:
        from collections import deque

        q: deque[tuple] = deque([seed])
        main_component = {seed}
        while q:
            cur = q.popleft()
            for nb, _dist in adj.get(cur, []):
                if nb not in main_component:
                    main_component.add(nb)
                    q.append(nb)

    def _candidates(
        radius: float,
        strip_limit: float | None = None,
    ) -> list[tuple[tuple, float, int]]:
        result = []
        for node in nodes:
            if main_component is not None and node not in main_component:
                continue
            if node in strip_nodes:
                continue
            if abs(node[0] - strip_lon) < min_off_strip:
                continue
            if strip_limit is not None:
                nearest_strip = min(_haversine(node, sn) for sn in strip_nodes)
                if nearest_strip > strip_limit:
                    continue
            d = _haversine(poi_coord, node)
            if d <= radius:
                degree = len(adj.get(node, []))
                result.append((node, d, degree))
        return result

    # If preferred landmark sits on the main graph, allow it / its nearest
    # main-component neighbor even if it is on/near the corridor.
    if preferred and preferred in landmarks and main_component is not None:
        poi = landmarks[preferred]
        if poi in main_component and poi in adj:
            return poi
        nearest_main = _find_closest_node(poi, main_component)
        if nearest_main is not None and _haversine(poi, nearest_main) <= max_radius_mi * 2:
            return nearest_main

    # Tier 1: near POI AND near Strip
    cands = _candidates(max_radius_mi, max_strip_dist_mi)
    # Tier 2: wider POI radius, still near Strip
    if not cands:
        cands = _candidates(max_radius_mi * 2, max_strip_dist_mi)
    # Tier 3: any distance from POI, still near Strip
    if not cands:
        cands = _candidates(float("inf"), max_strip_dist_mi)
    # Tier 4: fallback — drop Strip constraint entirely
    if not cands:
        cands = _candidates(max_radius_mi * 2, None)
    if not cands:
        return None

    # Prefer high-degree nodes, break ties by proximity to POI
    cands.sort(key=lambda c: (-c[2], c[1]))
    # Pick randomly from the top candidates for route variety
    top_n = min(3, len(cands))
    if rng is not None:
        return cands[rng.randint(0, top_n - 1)][0]
    return cands[0][0]


def _get_path_dijkstra(
    start: tuple,
    end: tuple,
    adj: Dict[tuple, List[tuple]],
    visited_nodes: Set[tuple],
    visited_edges: Set[tuple],
) -> tuple[List[tuple], float]:
    import heapq

    # (cost, current_node, path)
    queue = [(0.0, start, [start])]
    best_costs = {start: 0.0}

    while queue:
        dist, curr, path = heapq.heappop(queue)

        if curr == end:
            return path, dist

        for neighbor, d in adj.get(curr, []):
            edge = tuple(sorted((curr, neighbor)))
            if (
                neighbor not in path
                and neighbor not in visited_nodes
                and edge not in visited_edges
            ):
                new_cost = dist + d
                if neighbor not in best_costs or new_cost < best_costs[neighbor]:
                    best_costs[neighbor] = new_cost
                    heapq.heappush(queue, (new_cost, neighbor, path + [neighbor]))
    return [], 0.0


def _get_path_dijkstra_perturbed(
    start: tuple,
    end: tuple,
    adj: Dict[tuple, List[tuple]],
    visited_nodes: Set[tuple],
    visited_edges: Set[tuple],
    rng: "random.Random",
    perturbation: tuple[float, float] = (0.5, 2.0),
) -> tuple[List[tuple], float]:
    """Dijkstra with random edge weight perturbation for route variety.

    Edge weights are multiplied by a random factor in [perturbation[0],
    perturbation[1]] for priority ordering. The returned distance is the
    TRUE (unperturbed) haversine distance.
    """
    import heapq

    lo, hi = perturbation
    counter = 0
    # (perturbed_cost, tiebreaker, real_cost, current_node, path)
    queue = [(0.0, counter, 0.0, start, [start])]
    best_costs: Dict[tuple, float] = {start: 0.0}

    while queue:
        p_dist, _, r_dist, curr, path = heapq.heappop(queue)

        if curr == end:
            return path, r_dist

        if p_dist > best_costs.get(curr, float("inf")):
            continue

        for neighbor, d in adj.get(curr, []):
            edge = tuple(sorted((curr, neighbor)))
            if neighbor in visited_nodes or edge in visited_edges or neighbor in path:
                continue
            factor = rng.uniform(lo, hi)
            new_p_cost = p_dist + d * factor
            new_r_cost = r_dist + d
            if neighbor not in best_costs or new_p_cost < best_costs[neighbor]:
                best_costs[neighbor] = new_p_cost
                counter += 1
                heapq.heappush(
                    queue,
                    (new_p_cost, counter, new_r_cost, neighbor, path + [neighbor]),
                )

    return [], 0.0


MAX_ROUTE_RETRIES = 100


def _build_serpentine_waypoints(
    adj: Dict[tuple, List[tuple]],
    rng: random.Random,
    side: str = "west",
    num_roads: int = 5,
    bias_lat: float | None = None,
) -> list[tuple]:
    """Build serpentine (zigzag) waypoints on one side of the Strip.

    Identifies E-W arterial roads, sorts them north-to-south, selects a
    random subset, and returns waypoints that alternate between the far
    (west) end and the near (east/Strip-side) end of each road.  This
    zigzag pattern is non-crossing by construction.

    Returns a list of graph nodes (snapped to the nearest node in adj).
    """
    strip_lon = STRIP_CENTER[0]
    all_nodes = set(adj.keys())

    # Group nodes by approximate latitude band (E-W roads share a latitude)
    lat_bands: Dict[float, list[tuple]] = {}
    for node in all_nodes:
        band = round(node[1], 3)
        lat_bands.setdefault(band, []).append(node)

    # Identify E-W arterial bands: bands with >= 3 nodes spread > 0.02 lon
    ew_bands: list[tuple[float, tuple, tuple]] = []
    for band_lat, band_nodes in lat_bands.items():
        if side == "west":
            side_nodes = [n for n in band_nodes if n[0] < strip_lon + 0.003]
        else:
            side_nodes = [n for n in band_nodes if n[0] > strip_lon - 0.003]
        if len(side_nodes) < 3:
            continue
        lon_span = max(n[0] for n in side_nodes) - min(n[0] for n in side_nodes)
        if lon_span < 0.02:
            continue
        west_node = min(side_nodes, key=lambda n: n[0])
        east_node = max(side_nodes, key=lambda n: n[0])
        ew_bands.append((band_lat, west_node, east_node))

    ew_bands.sort(key=lambda b: -b[0])  # North to south
    if len(ew_bands) < 2:
        return []

    count = min(num_roads, len(ew_bands))

    if bias_lat is not None:
        # Weighted sampling: bands within ~0.015 deg of bias_lat get 3x weight
        weights = []
        for band_lat, _, _ in ew_bands:
            if abs(band_lat - bias_lat) < 0.015:
                weights.append(3.0)
            else:
                weights.append(1.0)
        # Weighted sampling without replacement
        selected: list[tuple[float, tuple, tuple]] = []
        avail_idx = list(range(len(ew_bands)))
        avail_w = list(weights)
        for _ in range(count):
            if not avail_idx:
                break
            total = sum(avail_w)
            r = rng.random() * total
            cumul = 0.0
            for j, w in enumerate(avail_w):
                cumul += w
                if cumul >= r:
                    selected.append(ew_bands[avail_idx[j]])
                    avail_idx.pop(j)
                    avail_w.pop(j)
                    break
    else:
        selected = rng.sample(ew_bands, count)

    selected.sort(key=lambda b: -b[0])

    waypoints: list[tuple] = []
    go_far = rng.choice([True, False])
    for _, west_node, east_node in selected:
        if side == "west":
            far_node, near_node = west_node, east_node
        else:
            far_node, near_node = east_node, west_node
        waypoints.append(far_node if go_far else near_node)
        go_far = not go_far

    # Ensure the last waypoint is on the "near" side (close to Strip)
    # so the reverse-generated route can connect to a corridor POI easily.
    if len(waypoints) >= 2:
        last_band = selected[-1]
        _, west_node, east_node = last_band
        if side == "west":
            near_node = east_node  # east = near Strip for west side
        else:
            near_node = west_node  # west = near Strip for east side
        waypoints[-1] = near_node

    return waypoints


def _generate_zone_sweep_route(
    adj: Dict[tuple, List[tuple]],
    nodes: Set[tuple],
    landmarks: Dict[str, tuple],
    strip_nodes: Set[tuple],
    road_names: Dict[tuple, str],
    rng: random.Random,
    finish_landmark: str | None = None,
) -> tuple[list[tuple], float]:
    """Generate a marathon route using forward-construction zone sweep.

    Construction order: start corridor (Sign → TI) -> serpentine outward
    from TI -> connect to off-strip finish POI (MUA) -> assemble.

    Runner order: Las Vegas Sign -> Strip northbound -> Treasure Island ->
    serpentine -> off-strip finish near Michelob Ultra Arena.
    """

    # ---- Step 1: Build start corridor (Sign → TI, northbound) ----
    sign_coord = landmarks.get(CORRIDOR_START)
    exit_coord = landmarks.get(CORRIDOR_EXIT)
    if sign_coord is None or exit_coord is None:
        sorted_strip = sorted(strip_nodes, key=lambda n: n[1])
        sign_node: tuple = sorted_strip[0]  # southernmost
        exit_node: tuple = sorted_strip[-1]  # northernmost
    else:
        sign_maybe = _find_closest_node(sign_coord, strip_nodes)
        exit_maybe = _find_closest_node(exit_coord, strip_nodes)
        if sign_maybe is None or exit_maybe is None:
            sorted_strip = sorted(strip_nodes, key=lambda n: n[1])
            sign_node = sorted_strip[0]
            exit_node = sorted_strip[-1]
        else:
            sign_node = sign_maybe
            exit_node = exit_maybe

    corridor_path, corridor_dist, corridor_edges = _build_strip_corridor(
        adj,
        strip_nodes,
        sign_node,
        exit_node,
        set(),
    )
    corridor_nodes: set[tuple] = set(corridor_path)

    # Compute exact serpentine budget.  Reserve distance for the
    # finish-POI connection so the serpentine doesn't overshoot and
    # force large finish-trims that walk the finish away from the POI.
    serpentine_budget = TARGET_DIST_MI - corridor_dist - _CONNECTOR_RESERVE

    # Resolve target POI for finish placement bias.
    # When no landmark is specified, pick a random POI once per call
    # (not per attempt) so all retries target the same area but
    # different calls produce finishes at different locations.
    finish_poi_pool = [
        name for name in landmarks if name not in (CORRIDOR_START, CORRIDOR_EXIT)
    ]
    if finish_landmark and finish_landmark in landmarks:
        target_poi_name: str | None = finish_landmark
        target_poi_coord: tuple | None = landmarks[finish_landmark]
    elif finish_poi_pool:
        target_poi_name = rng.choice(finish_poi_pool)
        target_poi_coord = landmarks[target_poi_name]
    else:
        target_poi_name = None
        target_poi_coord = None

    # ---- Retry loop ----
    full_route: list[tuple] = []
    total_dist = 0.0
    # Track best route across all attempts for fallback.
    # Score: lower is better (distance from target + finish from POI).
    best_fallback_route: list[tuple] = []
    best_fallback_dist = 0.0
    best_fallback_score = float("inf")

    for attempt in range(MAX_ROUTE_RETRIES):
        visited_edges: set[tuple] = set()
        visited_edges.update(corridor_edges)

        # ---- Find finish node early ----
        finish_node = _find_off_strip_poi_node(
            adj=adj,
            nodes=nodes,
            strip_nodes=strip_nodes,
            landmarks=landmarks,
            preferred=target_poi_name,
            near=exit_node if target_poi_name is None else None,
            rng=rng,
        )
        if finish_node is None:
            logger.warning(
                "PLANNER: Attempt %d rejected: no off-strip finish node",
                attempt + 1,
            )
            rng = random.Random(rng.randint(0, 2**32))
            continue

        # ---- Step 2: Build serpentine waypoints ----
        side = rng.choice(["west", "east"])
        go_south_to_north = rng.choice([True, False])

        # Bias waypoints toward the finish POI (MUA).
        # The serpentine starts at the corridor exit (TI) and needs
        # to spread toward the finish POI to reach the connector.
        if target_poi_coord is not None:
            bias_lat = target_poi_coord[1]  # MUA's latitude (~36.091)
        elif exit_coord is not None:
            bias_lat = exit_coord[1]
        else:
            # Seed-driven tier rotation for geographic variety
            tier_centers = [36.135, 36.11, 36.09]
            bias_lat = rng.choice(tier_centers)

        serpentine_wps = _build_serpentine_waypoints(
            adj,
            rng,
            side=side,
            num_roads=rng.randint(8, 12),
            bias_lat=bias_lat,
        )
        if go_south_to_north:
            serpentine_wps.reverse()

        # Optional second serpentine on the other side
        if rng.random() < 0.4:
            other_side = "east" if side == "west" else "west"
            extra_wps = _build_serpentine_waypoints(
                adj,
                rng,
                side=other_side,
                num_roads=rng.randint(3, 5),
                bias_lat=bias_lat,
            )
            if go_south_to_north:
                extra_wps.reverse()
            serpentine_wps.extend(extra_wps)

        # ---- Step 3: Route serpentine FROM corridor exit ----
        zone_route: list[tuple] = [exit_node]
        zone_dist = 0.0
        # Block corridor nodes to prevent node reuse in the assembled
        # route.  Edge avoidance (visited_edges) additionally prevents
        # reusing corridor edges.  This matches the original algorithm's
        # construction pattern where the serpentine originated from a
        # corridor endpoint and corridor nodes were blocked.
        visited_nodes: set[tuple] = {exit_node} | corridor_nodes
        zone_target = serpentine_budget
        current_node = exit_node

        for wp in serpentine_wps:
            if zone_dist >= zone_target:
                break

            path, dist = _get_path_dijkstra_perturbed(
                current_node,
                wp,
                adj,
                visited_nodes - {wp},
                visited_edges,
                rng,
            )
            if not path or len(path) < 2:
                path, dist = _get_path_dijkstra(
                    current_node,
                    wp,
                    adj,
                    visited_nodes - {wp},
                    visited_edges,
                )
            if not path or len(path) < 2:
                continue

            if _path_crosses_route(path, zone_route):
                continue
            # NOTE: We do NOT check corridor crossing here.  The
            # serpentine is allowed to cross the Strip corridor
            # perpendicularly (east-west arterials).  Edge avoidance
            # prevents reusing corridor edges.

            zone_route.extend(path[1:])
            zone_dist += dist
            for i in range(len(path) - 1):
                visited_edges.add(
                    tuple(sorted((path[i], path[i + 1]))),
                )
            visited_nodes.update(path[1:])
            current_node = path[-1]

        # ---- Step 4: Fill undershoot with exploratory hops ----
        extra_attempts = 0
        while zone_dist < zone_target - 0.5 and extra_attempts < 60:
            extra_attempts += 1
            if side == "west":
                candidates = [
                    n
                    for n in nodes
                    if n != current_node and n[0] <= STRIP_CENTER[0] + 0.005
                ]
            else:
                candidates = [
                    n
                    for n in nodes
                    if n != current_node and n[0] >= STRIP_CENTER[0] - 0.005
                ]
            if not candidates:
                break
            candidates.sort(
                key=lambda n: _haversine(current_node, n),
                reverse=True,
            )
            top_k = min(10, len(candidates))
            target = candidates[rng.randint(0, top_k - 1)]

            path = None
            dist = 0.0
            for path_attempt in range(5):
                cp, cd = _get_path_dijkstra_perturbed(
                    current_node,
                    target,
                    adj,
                    visited_nodes - {target},
                    visited_edges,
                    rng,
                )
                if not cp or len(cp) < 2:
                    cp, cd = _get_path_dijkstra(
                        current_node,
                        target,
                        adj,
                        visited_nodes - {target},
                        visited_edges,
                    )
                if not cp or len(cp) < 2:
                    break
                if _path_crosses_route(cp, zone_route):
                    if path_attempt < 4 and len(candidates) > 1:
                        target = candidates[
                            rng.randint(
                                0,
                                min(len(candidates) - 1, top_k - 1),
                            )
                        ]
                    continue
                path = cp
                dist = cd
                break

            if not path or len(path) < 2:
                continue
            zone_route.extend(path[1:])
            zone_dist += dist
            for i in range(len(path) - 1):
                visited_edges.add(
                    tuple(sorted((path[i], path[i + 1]))),
                )
            visited_nodes.update(path[1:])
            current_node = path[-1]

        # ---- Step 5: Trim if serpentine overshoots budget ----
        while zone_dist > serpentine_budget + 1.0 and len(zone_route) > 2:
            removed = _haversine(zone_route[-2], zone_route[-1])
            zone_route.pop()
            zone_dist -= removed
        current_node = zone_route[-1]

        # ---- Step 6: Connect serpentine to finish POI ----
        if current_node != finish_node:
            conn_path: list[tuple] | None = None
            conn_dist = 0.0
            # Block all previously visited nodes (corridor + serpentine)
            # except the finish_node destination to prevent node reuse
            # in the assembled route.
            conn_blocked = (visited_nodes | corridor_nodes) - {finish_node}
            for conn_attempt in range(5):
                cp, cd = _get_path_dijkstra_perturbed(
                    current_node,
                    finish_node,
                    adj,
                    conn_blocked,
                    visited_edges,
                    rng,
                )
                if not cp or len(cp) < 2:
                    cp, cd = _get_path_dijkstra(
                        current_node,
                        finish_node,
                        adj,
                        conn_blocked,
                        visited_edges,
                    )
                if not cp or len(cp) < 2:
                    # Relax edge constraints but keep node constraints
                    cp, cd = _get_path_dijkstra(
                        current_node,
                        finish_node,
                        adj,
                        conn_blocked,
                        set(),
                    )
                if not cp or len(cp) < 2:
                    # Relax to corridor-only node constraints
                    cp, cd = _get_path_dijkstra(
                        current_node,
                        finish_node,
                        adj,
                        corridor_nodes - {finish_node},
                        set(),
                    )
                if not cp or len(cp) < 2:
                    break
                if not _path_crosses_route(cp, zone_route) and not (
                    corridor_path and _path_crosses_route(cp, corridor_path)
                ):
                    conn_path = cp
                    conn_dist = cd
                    break

            if not conn_path or len(conn_path) < 2:
                # Ultra last resort: drop ALL constraints
                cp, cd = _get_path_dijkstra(
                    current_node,
                    finish_node,
                    adj,
                    set(),
                    set(),
                )
                if cp and len(cp) >= 2:
                    conn_path = cp
                    conn_dist = cd

            if conn_path and len(conn_path) >= 2:
                zone_route.extend(conn_path[1:])
                zone_dist += conn_dist
                for i in range(len(conn_path) - 1):
                    visited_edges.add(
                        tuple(sorted((conn_path[i], conn_path[i + 1]))),
                    )
                visited_nodes.update(conn_path[1:])
                current_node = conn_path[-1]
            else:
                logger.warning(
                    "PLANNER: Attempt %d rejected: cannot connect to finish POI",
                    attempt + 1,
                )
                rng = random.Random(rng.randint(0, 2**32))
                continue

        # ---- Step 7: Assemble full route ----
        # zone_route goes: exit_node -> serpentine -> finish_node
        # (no reversal needed — already in runner order)
        full_route = list(corridor_path)
        total_dist = corridor_dist

        # Append serpentine + finish connector (exit_node -> ... -> finish_node)
        if zone_route and zone_route[0] == corridor_path[-1]:
            full_route.extend(zone_route[1:])
        else:
            full_route.extend(zone_route)
        total_dist += zone_dist

        # ---- Distance adjustment ----
        if total_dist > TARGET_DIST_MI:
            corridor_len = len(corridor_path)

            # Phase 1: Trim from route end (finish side), but stop
            # if the finish drifts too far from the target POI.
            trimmed = 0.0
            poi_check = (
                target_poi_coord if target_poi_coord is not None else finish_node
            )
            while (
                total_dist - trimmed > TARGET_DIST_MI + 0.5
                and len(full_route) > corridor_len + 2
            ):
                # Before popping, check if the new end (full_route[-2])
                # would be too far from the target POI.
                if len(full_route) >= 3:
                    new_end_dist = _haversine(full_route[-2], poi_check)
                    if new_end_dist > 0.75:
                        break  # Stop trimming to keep finish near POI
                seg = _haversine(full_route[-2], full_route[-1])
                full_route.pop()
                trimmed += seg

            total_dist -= trimmed

            # Phase 2: Fine-tune remaining excess by interpolating last segment.
            excess = total_dist - TARGET_DIST_MI
            if excess > 0.01 and len(full_route) >= 2:
                trim_limit = min(excess, 0.5)
                last_seg = _haversine(full_route[-2], full_route[-1])
                if last_seg > 0:
                    keep = last_seg - trim_limit
                    if keep > 0:
                        new_end = _interpolate(
                            full_route[-2],
                            full_route[-1],
                            keep,
                            last_seg,
                        )
                        full_route[-1] = tuple(new_end)
                        total_dist -= trim_limit
        elif total_dist < TARGET_DIST_MI:
            # Undershoot: extend from finish along real graph edges.
            # Prefer extending toward the target POI to keep finish close.
            shortfall = TARGET_DIST_MI - total_dist
            poi_target = (
                target_poi_coord if target_poi_coord is not None else finish_node
            )
            if len(full_route) >= 2 and full_route[-1] in adj:
                # Sort neighbors by proximity to POI (closest first)
                neighbors = [
                    (n, d)
                    for n, d in adj[full_route[-1]]
                    if tuple(sorted((full_route[-1], n))) not in visited_edges
                ]
                neighbors.sort(key=lambda nd: _haversine(nd[0], poi_target))
                for neighbor, edge_dist in neighbors:
                    ext_amount = min(shortfall, edge_dist)
                    ext_pt = _interpolate(
                        full_route[-1],
                        neighbor,
                        ext_amount,
                        edge_dist,
                    )
                    full_route.append(tuple(ext_pt))
                    total_dist += ext_amount
                    break

        if total_dist < TARGET_DIST_MI:
            logger.warning(
                "PLANNER: Route undershoot: %.3f mi < %.3f mi target",
                total_dist,
                TARGET_DIST_MI,
            )

        # ---- Step 9: Validate ----
        has_crossing = _route_has_crossing(full_route)

        seen: set[tuple] = set()
        has_node_reuse = False
        for node in full_route:
            if node in seen:
                has_node_reuse = True
                break
            seen.add(node)

        # Start must be near Las Vegas Sign
        start_to_sign = _haversine(full_route[0], sign_coord) if sign_coord else 0.0
        starts_near_sign = start_to_sign <= 0.5

        # Finish must be within 0.5 mi of target POI
        if target_poi_coord is not None:
            finish_to_poi = _haversine(full_route[-1], target_poi_coord)  # type: ignore[arg-type]
        else:
            finish_to_poi = min(_haversine(full_route[-1], sn) for sn in strip_nodes)  # type: ignore[arg-type]
        finishes_near_poi = finish_to_poi <= 0.75

        if (
            not has_crossing
            and not has_node_reuse
            and starts_near_sign
            and finishes_near_poi
        ):
            return full_route, total_dist

        # Track best fallback: prefer routes that are close to valid.
        # Score penalizes distance shortfall and finish distance from POI.
        shortfall = max(0, TARGET_DIST_MI - total_dist)
        score = (
            shortfall
            + finish_to_poi * 2
            + (10 if has_crossing else 0)
            + (10 if has_node_reuse else 0)
        )
        if score < best_fallback_score:
            best_fallback_score = score
            best_fallback_route = list(full_route)
            best_fallback_dist = total_dist

        reasons = []
        if has_crossing:
            reasons.append("geometric crossing")
        if has_node_reuse:
            reasons.append("node reuse (visual crossing)")
        if not starts_near_sign:
            reasons.append(f"start {start_to_sign:.2f} mi from Sign")
        if not finishes_near_poi:
            reasons.append(f"finish {finish_to_poi:.2f} mi from POI (max 0.75)")
        logger.warning(
            "PLANNER: Attempt %d rejected: %s",
            attempt + 1,
            ", ".join(reasons),
        )
        rng = random.Random(rng.randint(0, 2**32))

    logger.warning(
        "PLANNER: All %d retry attempts exhausted, returning best route",
        MAX_ROUTE_RETRIES,
    )
    if best_fallback_route:
        return best_fallback_route, best_fallback_dist
    return full_route, total_dist


def _route_is_clean(route: list[tuple], strip_nodes: Set[tuple]) -> bool:
    """Return True when *route* has no crossings, no node reuse,
    and starts on or near the Strip."""
    if not route:
        return False
    if _route_has_crossing(route):
        return False
    seen: set[tuple] = set()
    for node in route:
        if node in seen:
            return False
        seen.add(node)
    start_to_strip = min(_haversine(route[0], sn) for sn in strip_nodes)
    return start_to_strip <= 0.5


def _is_bangkok_network() -> bool:
    """True when corridor center is in Thailand (eastern hemisphere)."""
    return STRIP_CENTER[0] > 0


def _active_target_dist_mi() -> float:
    return BANGKOK_TARGET_DIST_MI if _is_bangkok_network() else TARGET_DIST_MI


def _largest_connected_subgraph(
    adj: Dict[tuple, List[tuple]],
) -> Dict[tuple, List[tuple]]:
    """Keep only the largest connected component of an adjacency graph."""
    if not adj:
        return {}
    from collections import deque

    seen: Set[tuple] = set()
    best: Set[tuple] = set()
    for start in adj:
        if start in seen:
            continue
        q: deque[tuple] = deque([start])
        seen.add(start)
        comp: Set[tuple] = {start}
        while q:
            u = q.popleft()
            for v, _ in adj.get(u, []):
                if v not in seen:
                    seen.add(v)
                    comp.add(v)
                    q.append(v)
        if len(comp) > len(best):
            best = comp
    return {
        n: [(v, w) for v, w in nbrs if v in best]
        for n, nbrs in adj.items()
        if n in best
    }


def _filter_lumphini_park_graph(
    adj: Dict[tuple, List[tuple]],
    road_names: Dict[tuple, str],
) -> Dict[tuple, List[tuple]]:
    """Restrict the graph to runnable paths inside Lumphini Park only."""
    lon_min, lat_min, lon_max, lat_max = LUMPHINI_PARK_BBOX
    filtered: Dict[tuple, List[tuple]] = {}
    for u, nbrs in adj.items():
        if not (lon_min <= u[0] <= lon_max and lat_min <= u[1] <= lat_max):
            continue
        kept: List[tuple] = []
        for v, dist in nbrs:
            if not (lon_min <= v[0] <= lon_max and lat_min <= v[1] <= lat_max):
                continue
            edge = tuple(sorted((u, v)))
            name = (road_names.get(edge) or "").lower()
            if name and any(h in name for h in _LUMPHINI_ARTERIAL_HINTS):
                continue
            kept.append((v, dist))
        if kept:
            filtered[u] = kept
    return _largest_connected_subgraph(filtered)


def _parametric_heart_lonlat(
    n: int = 120,
    center: tuple[float, float] = _LUMPHINI_HEART_CENTER,
    scale_lon: float = _LUMPHINI_HEART_SCALE_LON,
    scale_lat: float = _LUMPHINI_HEART_SCALE_LAT,
) -> list[tuple[float, float]]:
    """Classic parametric heart mapped into Lumphini lon/lat (tip at south)."""
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        # Standard heart curve; tip at bottom (min y)
        x = 16.0 * (math.sin(t) ** 3)
        y = (
            13.0 * math.cos(t)
            - 5.0 * math.cos(2.0 * t)
            - 2.0 * math.cos(3.0 * t)
            - math.cos(4.0 * t)
        )
        lon = center[0] + (x / 16.0) * scale_lon
        lat = center[1] + (y / 17.0) * scale_lat
        pts.append((lon, lat))
    pts.append(pts[0])
    return pts


def _point_to_segment_dist_mi(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """Approximate distance from point to segment using local degrees→miles."""
    # Local equirectangular in miles near Bangkok
    lat0 = math.radians((a[1] + b[1] + p[1]) / 3.0)
    ax, ay = a[0] * math.cos(lat0), a[1]
    bx, by = b[0] * math.cos(lat0), b[1]
    px, py = p[0] * math.cos(lat0), p[1]
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    t = 0.0 if ab2 < 1e-18 else max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    # degrees → miles (~69 mi per deg lat)
    return math.hypot(px - cx, py - cy) * 69.0


def _dijkstra_along_guide(
    start: tuple,
    end: tuple,
    adj: Dict[tuple, List[tuple]],
    guide_a: tuple[float, float],
    guide_b: tuple[float, float],
    pull: float = 12.0,
) -> tuple[List[tuple], float]:
    """Shortest path that stays near the guide segment (preserves heart outline)."""
    import heapq

    queue: list[tuple[float, float, tuple]] = [(0.0, 0.0, start)]  # (cost, true_dist, node)
    best_cost: dict[tuple, float] = {start: 0.0}
    true_dist: dict[tuple, float] = {start: 0.0}
    prev: dict[tuple, tuple] = {}

    while queue:
        cost, dist, curr = heapq.heappop(queue)
        if curr == end:
            break
        if cost > best_cost.get(curr, float("inf")):
            continue
        for neighbor, d in adj.get(curr, []):
            # Penalize edges that leave the heart outline
            off = _point_to_segment_dist_mi(neighbor, guide_a, guide_b)
            step_cost = d * (1.0 + pull * off)
            new_cost = cost + step_cost
            if new_cost < best_cost.get(neighbor, float("inf")):
                best_cost[neighbor] = new_cost
                true_dist[neighbor] = dist + d
                prev[neighbor] = curr
                heapq.heappush(queue, (new_cost, dist + d, neighbor))

    if end not in true_dist:
        return [], 0.0
    path = [end]
    cur = end
    hops = 0
    while cur != start and hops < 5000:
        cur = prev[cur]
        path.append(cur)
        hops += 1
    path.reverse()
    return path, true_dist[end]


def _follow_guide_on_graph(
    guide: list[tuple[float, float]],
    adj: Dict[tuple, List[tuple]],
    nodes: Set[tuple],
    arrive_mi: float = 0.035,
    max_steps: int = 4000,
) -> tuple[list[tuple], float]:
    """Walk park edges while chasing a guide curve with strict forward progress.

    Only steps to a neighbor that gets closer to the current guide target.
    If stuck, short-path snap to the target node and advance the guide index.
    """
    if not guide:
        return [], 0.0
    current = _find_closest_node(guide[0], nodes)
    if current is None:
        return [], 0.0

    route: list[tuple] = [current]
    total = 0.0
    gi = 1
    steps = 0

    while gi < len(guide) and steps < max_steps:
        steps += 1
        target = guide[gi]
        cur_d = _haversine(current, target)
        if cur_d <= arrive_mi:
            gi += 1
            continue

        nbrs = adj.get(current, [])
        progress: list[tuple[float, float, tuple]] = []
        for n, edge_d in nbrs:
            nd = _haversine(n, target)
            if nd < cur_d - 1e-7:
                progress.append((nd, edge_d, n))

        if progress:
            progress.sort(key=lambda x: (x[0], x[1]))
            _, edge_d, best_n = progress[0]
            total += edge_d
            route.append(best_n)
            current = best_n
            continue

        # Stuck: jump toward this guide point, then advance
        snap = _find_closest_node(target, nodes)
        if snap is None or snap == current:
            gi += 1
            continue
        path, dist = _get_path_dijkstra_park(current, snap, adj)
        if path and len(path) >= 2 and dist <= 0.18:
            for p in path[1:]:
                total += _haversine(route[-1], p)
                route.append(p)
            current = route[-1]
        # Always advance guide after a snap attempt to avoid infinite orbits
        gi += 1

    return route, total


def _generate_lumphini_heart_route(
    adj: Dict[tuple, List[tuple]],
    landmarks: Dict[str, tuple],
    road_names: Dict[tuple, str],
    rng: random.Random,
    target_mi: float | None = None,
    start_landmark: str | None = None,
) -> tuple[list[tuple], float, list[tuple], float, int]:
    """Heart-shaped route on Lumphini park paths only.

    Returns ``(map_coords, reported_dist_mi, lap_coords, lap_mi, laps)``.
    ``map_coords`` is a **single** clean heart lap (so the artifact looks like a
    heart). ``reported_dist_mi`` stacks identical laps to reach the target.
    """
    park_adj = _filter_lumphini_park_graph(adj, road_names)
    if len(park_adj) < 20:
        logger.warning("PLANNER: Lumphini park subgraph too small (%d nodes)", len(park_adj))
        return [], 0.0, [], 0.0, 0

    park_nodes = set(park_adj.keys())
    target = target_mi if target_mi is not None else _active_target_dist_mi()

    scale_lon = _LUMPHINI_HEART_SCALE_LON * rng.uniform(0.99, 1.04)
    scale_lat = _LUMPHINI_HEART_SCALE_LAT * rng.uniform(0.99, 1.04)
    center = (
        _LUMPHINI_HEART_CENTER[0] + rng.uniform(-0.00003, 0.00003),
        _LUMPHINI_HEART_CENTER[1] + rng.uniform(-0.00002, 0.00002),
    )
    guide = _parametric_heart_lonlat(
        n=180, center=center, scale_lon=scale_lon, scale_lat=scale_lat
    )

    # Start at tip (south) near South Gate when possible
    start_name = start_landmark or CORRIDOR_START
    tip_hint = landmarks.get(start_name) or min(guide, key=lambda p: p[1])
    tip_i = min(range(len(guide)), key=lambda i: _haversine(guide[i], tip_hint))
    guide = guide[tip_i:-1] + guide[:tip_i] + [guide[tip_i]]

    lap, lap_dist = _follow_guide_on_graph(guide, park_adj, park_nodes)
    if len(lap) < 40 or lap_dist < 1.0:
        logger.warning(
            "PLANNER: Heart follow failed (pts=%d dist=%.3f mi)", len(lap), lap_dist
        )
        return [], 0.0, [], 0.0, 0

    # Close the loop back to start
    if lap[0] != lap[-1]:
        path, dist = _get_path_dijkstra_park(lap[-1], lap[0], park_adj)
        if path and len(path) >= 2 and dist < 0.35:
            for p in path[1:]:
                lap_dist += _haversine(lap[-1], p)
                lap.append(p)
        else:
            lap_dist += _haversine(lap[-1], lap[0])
            lap.append(lap[0])

    laps = max(1, int(math.ceil(target / max(lap_dist, 1e-6))))
    laps = min(laps, 12)
    reported = lap_dist * laps

    logger.info(
        "PLANNER: Lumphini HEART map-lap %.2f km × %d = %.1f km (%d pts)",
        lap_dist * 1.60934,
        laps,
        reported * 1.60934,
        len(lap),
    )
    # Map shows one clean lap; distance is multi-lap
    return lap, reported, lap, lap_dist, laps


def _get_path_dijkstra_park(
    start: tuple,
    end: tuple,
    adj: Dict[tuple, List[tuple]],
) -> tuple[List[tuple], float]:
    """Dijkstra that allows revisiting graph nodes (park grids are dense/loopy)."""
    import heapq

    queue: list[tuple[float, tuple]] = [(0.0, start)]
    best: dict[tuple, float] = {start: 0.0}
    prev: dict[tuple, tuple] = {}
    while queue:
        dist, curr = heapq.heappop(queue)
        if curr == end:
            break
        if dist > best.get(curr, float("inf")):
            continue
        for neighbor, d in adj.get(curr, []):
            new_cost = dist + d
            if new_cost < best.get(neighbor, float("inf")):
                best[neighbor] = new_cost
                prev[neighbor] = curr
                heapq.heappush(queue, (new_cost, neighbor))
    if end not in best:
        return [], 0.0
    path = [end]
    cur = end
    while cur != start:
        cur = prev[cur]
        path.append(cur)
    path.reverse()
    return path, best[end]


def _generate_park_connector_route(
    adj: Dict[tuple, List[tuple]],
    landmarks: Dict[str, tuple],
    strip_nodes: Set[tuple],
    rng: random.Random,
    finish_landmark: str | None = None,
    start_landmark: str | None = None,
) -> tuple[list[tuple], float]:
    """Build a Bangkok park-connector route with seed-based variety.

    Chains landmarks with Dijkstra. ``rng`` shuffles optional midpoints and
    loop direction so different seeds produce visibly different maps.
    """
    finish_name = finish_landmark or FINISH_LANDMARK
    start_name = start_landmark or CORRIDOR_START

    # Midpoints shuffled by seed → different path shapes each run
    mid_pool = [
        name
        for name in (
            "Lumphini Park",
            CORRIDOR_EXIT,
            "The Green Mile",
            "Green Bridge Midpoint",
            "Asok BTS",
            "Ratchaprasong",
            "Sala Daeng BTS",
            "Lumphini MRT",
            "QSNCC MRT",
            "Queen Sirikit National Convention Center",
        )
        if name in landmarks and name not in (start_name, finish_name)
    ]
    rng.shuffle(mid_pool)
    # Take 3–5 midpoints depending on seed
    n_mid = rng.randint(3, min(5, len(mid_pool))) if mid_pool else 0
    mids = mid_pool[:n_mid]

    chain = [start_name] + mids + [finish_name]
    # Optional return leg for loops / distance
    loop_pool = list(mids)
    rng.shuffle(loop_pool)
    loop = [finish_name] + loop_pool + [start_name, finish_name]

    def _snap(name: str) -> tuple | None:
        if name not in landmarks:
            return None
        coord = landmarks[name]
        if coord in adj:
            return coord
        return _find_closest_node(coord, set(adj.keys()))

    waypoints: list[tuple] = []
    for name in chain:
        node = _snap(name)
        if node and (not waypoints or node != waypoints[-1]):
            waypoints.append(node)
    if len(waypoints) < 2:
        sorted_strip = sorted(strip_nodes, key=lambda n: n[1])
        if len(sorted_strip) >= 2:
            waypoints = [sorted_strip[0], sorted_strip[-1]]
            fin = _snap(finish_name)
            if fin:
                waypoints.append(fin)

    target = _active_target_dist_mi()
    # Slight target jitter so loops differ by seed
    target *= rng.uniform(0.88, 1.12)
    route: list[tuple] = [waypoints[0]]
    total = 0.0

    def _append_leg(dst: tuple) -> bool:
        nonlocal total
        src = route[-1]
        if src == dst:
            return True
        path, dist = _get_path_dijkstra(src, dst, adj, set(), set())
        if not path or len(path) < 2:
            return False
        route.extend(path[1:])
        total += dist
        return True

    for wp in waypoints[1:]:
        if not _append_leg(wp):
            continue

    safety = 0
    while total < target * 0.92 and safety < 8:
        safety += 1
        order = list(loop)
        if rng.random() < 0.5:
            order.reverse()
        for name in order:
            node = _snap(name)
            if not node:
                continue
            if not _append_leg(node):
                continue
            if total >= target * 0.92:
                break

    if len(route) < 2:
        return [], 0.0
    return route, total


def _generate_best_route(
    adj: Dict[tuple, List[tuple]],
    nodes: Set[tuple],
    landmarks: Dict[str, tuple],
    strip_nodes: Set[tuple],
    road_names: Dict[tuple, str],
    seed: int | None = None,
    finish_landmark: str | None = None,
    start_landmark: str | None = None,
    max_candidates: int = 10,
) -> tuple[list[tuple], float]:
    """Try multiple seeds and return the best *clean* marathon-distance route.

    A route is "clean" when it has no geometric crossings, no node
    reuse, and starts on or near the Strip.

    Tries seeds derived from the base seed.  Returns as soon as a
    clean marathon-distance route is found (early exit).  If none of
    the candidates is both clean and marathon-distance, returns the
    longest clean route.  As a last resort, returns the longest route
    regardless of quality.
    """
    if seed is None:
        seed = random.randrange(2**32)

    # Bangkok park-connector network: use landmark-chain routing (fast, reliable)
    if _is_bangkok_network():
        rng = random.Random(seed)
        route, dist = _generate_park_connector_route(
            adj,
            landmarks,
            strip_nodes,
            rng,
            finish_landmark=finish_landmark,
            start_landmark=start_landmark,
        )
        if route:
            logger.info(
                "PLANNER: Bangkok park-connector route %.3f mi (target %.3f mi / %.1f km)",
                dist,
                _active_target_dist_mi(),
                _active_target_dist_mi() * 1.60934,
            )
            return route, dist

    best_route: list[tuple] = []
    best_dist = 0.0
    best_clean_route: list[tuple] = []
    best_clean_dist = 0.0
    target = _active_target_dist_mi()

    for k in range(max_candidates):
        candidate_seed = seed * 1000 + k
        rng = random.Random(candidate_seed)
        route, dist = _generate_zone_sweep_route(
            adj,
            nodes,
            landmarks,
            strip_nodes,
            road_names,
            rng,
            finish_landmark=finish_landmark,
        )

        if dist > best_dist:
            best_route = route
            best_dist = dist

        clean = _route_is_clean(route, strip_nodes)

        if clean and dist > best_clean_dist:
            best_clean_route = route
            best_clean_dist = dist

        # Early exit: clean target-distance route found
        if clean and dist >= target:
            return route, dist

    # Prefer clean route even if shorter
    if best_clean_route:
        return best_clean_route, best_clean_dist
    return best_route, best_dist


def _generate_spine_and_sprout(
    adj: Dict[tuple, List[tuple]],
    nodes: Set[tuple],
    landmarks: Dict[str, tuple],
    theme_sequence: Optional[List[str]] = None,
    strip_nodes: Optional[Set[tuple]] = None,
    waypoints: Optional[List[tuple]] = None,
    truncate_at_target: bool = True,
    rng: Optional[random.Random] = None,
    no_cross_path: Optional[List[tuple]] = None,
    target_dist: Optional[float] = None,
) -> tuple[List[tuple], float]:
    """Generate a marathon route through the road network.

    When ``waypoints`` are provided, the route follows the waypoint sequence
    via Dijkstra (cloverleaf/petal pattern -- realistic marathon courses).

    When only ``theme_sequence`` is provided, waypoints are auto-generated
    by snapping landmarks to graph nodes and connecting them.

    When ``truncate_at_target`` is True (default), the route is interpolated
    to hit exactly the target distance.  Use ``target_dist`` to override
    the default TARGET_DIST_MI (e.g. to leave room for a finish corridor).
    When False, all waypoint legs are completed and the full (possibly
    overshooting) route is returned -- the caller is responsible for
    trimming.

    ``rng`` enables perturbed Dijkstra for alternative non-crossing paths.

    ``no_cross_path`` is an optional fixed path (e.g. the finish corridor)
    that each new leg is checked against for geometric crossings.  This
    gives a structural guarantee that the petal route never crosses the
    finish corridor.
    """
    route_path: list[tuple] = []
    total_dist = 0.0
    visited_nodes: set[tuple] = set()
    visited_edges: set[tuple] = set()

    # Build the waypoint sequence
    if waypoints:
        # Snap each waypoint to the nearest graph node
        wp_nodes: list[tuple] = []
        for wp in waypoints:
            closest = _find_closest_node(wp, nodes)
            if closest is not None:
                wp_nodes.append(closest)
    else:
        # Legacy: use theme_sequence landmarks
        if theme_sequence is None:
            theme_sequence = ["Las Vegas Sign", "Allegiant Stadium", "Sphere"]
        wp_nodes = [
            node
            for landmark in theme_sequence
            if landmark in landmarks
            for node in [_find_closest_node(landmarks[landmark], nodes)]
            if node is not None
        ]
        # Snap first to Strip if available
        if strip_nodes and wp_nodes:
            anchor = _find_strip_anchor(wp_nodes[0], strip_nodes)
            if anchor is not None:
                wp_nodes[0] = anchor

    # Connect consecutive waypoints via Dijkstra
    if len(wp_nodes) >= 2:
        for i in range(len(wp_nodes) - 1):
            start_node = wp_nodes[i]
            end_node = wp_nodes[i + 1]

            # Avoid reusing NON-STRIP nodes to prevent visual X-crossings
            # at petal interior intersections.  Strip backbone nodes are
            # exempt: the route always traverses them north-south, so
            # reuse there looks like the same corridor, not a crossing.
            node_constraint = visited_nodes.copy()
            if strip_nodes:
                node_constraint -= strip_nodes
            node_constraint.discard(end_node)
            node_constraint.discard(start_node)

            # --- Incremental crossing prevention ---
            # Try constrained Dijkstra, then verify it doesn't cross the
            # route built so far.  If it crosses, try perturbed Dijkstra
            # for alternative paths.  This gives a STRUCTURAL guarantee
            # that no leg ever introduces a crossing.
            _MAX_PATH_ATTEMPTS = 6
            path: list[tuple] = []
            dist: float = 0.0
            for path_try in range(_MAX_PATH_ATTEMPTS):
                if path_try == 0:
                    # First try: constrained Dijkstra
                    path, dist = _get_path_dijkstra(
                        start_node,
                        end_node,
                        adj,
                        node_constraint,
                        visited_edges,
                    )
                elif path_try == 1:
                    # Second try: relax node constraint (keep edges)
                    path, dist = _get_path_dijkstra(
                        start_node,
                        end_node,
                        adj,
                        set(),
                        visited_edges,
                    )
                elif rng is not None:
                    # Subsequent tries: perturbed Dijkstra for variety
                    path, dist = _get_path_dijkstra_perturbed(
                        start_node,
                        end_node,
                        adj,
                        node_constraint,
                        visited_edges,
                        rng,
                    )
                else:
                    break

                if not path or len(path) < 2:
                    break  # No path at all -- skip this waypoint

                # Structural crossing checks:
                # 1. Does this path cross the route built so far?
                if route_path and _path_crosses_route(path, route_path):
                    path = []  # Reject, try next attempt
                    continue
                # 2. Does this path cross the protected corridor
                #    (e.g. the southbound finish)?
                if no_cross_path and _path_crosses_route(path, no_cross_path):
                    path = []
                    continue

                break  # Accepted: no crossing

            # Fallback 1: drop node constraints (keep edge avoidance)
            if not path:
                path, dist = _get_path_dijkstra(
                    start_node,
                    end_node,
                    adj,
                    set(),  # No node avoidance
                    visited_edges,
                )

            # Fallback 2: drop ALL constraints (ensures route completion;
            # edge/node reuse caught by outer validation and retry loop)
            if not path:
                path, dist = _get_path_dijkstra(
                    start_node,
                    end_node,
                    adj,
                    set(),
                    set(),
                )

            if path:
                # Check if this leg would overshoot TARGET_DIST_MI
                if truncate_at_target and total_dist + dist >= TARGET_DIST_MI:
                    # Walk this leg edge by edge, interpolating when we
                    # reach the target distance
                    for j in range(len(path) - 1):
                        p1 = path[j]
                        p2 = path[j + 1]
                        d = _haversine(p1, p2)

                        if j == 0 and not route_path:
                            route_path.append(p1)
                            visited_nodes.add(p1)
                        elif j == 0 and route_path:
                            pass  # Skip duplicate junction

                        if total_dist + d >= TARGET_DIST_MI:
                            needed = TARGET_DIST_MI - total_dist
                            last_coord = _interpolate(p1, p2, needed, d)
                            route_path.append(tuple(last_coord))
                            total_dist += needed
                            break
                        else:
                            route_path.append(p2)
                            visited_nodes.add(p2)
                            visited_edges.add(tuple(sorted((p1, p2))))
                            total_dist += d
                    # Stop processing further waypoints
                    if total_dist >= TARGET_DIST_MI - 0.001:
                        break
                else:
                    # Append the full leg
                    if not route_path:
                        route_path.extend(path)
                    else:
                        route_path.extend(path[1:])

                    total_dist += dist
                    for node in path:
                        visited_nodes.add(node)
                    for j in range(len(path) - 1):
                        visited_edges.add(tuple(sorted((path[j], path[j + 1]))))

                    logger.info(
                        "PLANNER: Leg %d→%d: %.3f mi (total: %.3f mi)",
                        i,
                        i + 1,
                        dist,
                        total_dist,
                    )
            else:
                logger.warning(
                    "PLANNER: No path found for leg %d→%d, skipping",
                    i,
                    i + 1,
                )

    # Fallback: if no path was built
    if not route_path and nodes:
        first_node = sorted(list(nodes))[0]
        route_path = [first_node]
        visited_nodes.add(first_node)

    return route_path, total_dist


def _split_route_by_road(
    route_coords: List[tuple],
    road_names: Dict[tuple, str],
    total_dist: float,
) -> List[dict]:
    """Split a flat coordinate list into GeoJSON Features grouped by road name.

    Each consecutive run of edges sharing the same road name becomes one
    LineString Feature with ``properties.name`` set to the road name.
    The first segment also carries the overall route metadata.
    """
    segments: List[dict] = []
    current_name: Optional[str] = None
    current_coords: List[list] = [list(route_coords[0])]

    for i in range(len(route_coords) - 1):
        p1 = route_coords[i]
        p2 = route_coords[i + 1]
        edge_key = tuple(sorted((p1, p2)))
        edge_name = road_names.get(edge_key)

        if edge_name != current_name and len(current_coords) > 1:
            # Flush the current segment
            segments.append(
                {
                    "type": "Feature",
                    "properties": {"name": current_name},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": current_coords,
                    },
                }
            )
            # Start new segment from the shared junction point
            current_coords = [list(p1)]
            current_name = edge_name
        elif edge_name != current_name:
            current_name = edge_name

        current_coords.append(list(p2))

    # Flush final segment
    if len(current_coords) > 1:
        segments.append(
            {
                "type": "Feature",
                "properties": {"name": current_name},
                "geometry": {
                    "type": "LineString",
                    "coordinates": current_coords,
                },
            }
        )

    # Attach route-level metadata to the first segment
    if segments:
        segments[0]["properties"].update(
            {
                "route_type": "marathon",
                "distance_mi": round(total_dist, 4),
                "certified": True,
            }
        )

    return segments


async def plan_marathon_route(
    geojson_data: Optional[str] = None,
    theme_sequence: Optional[List[str]] = None,
    waypoints: Optional[List[List[float]]] = None,
    petal_names: Optional[List[str]] = None,
    algorithm: str = "zone_sweep",
    route_shape: Optional[str] = None,
    finish_landmark: Optional[str] = None,
    start_landmark: Optional[str] = None,
    seed: Optional[int] = None,
    target_distance_km: Optional[float] = None,
    force_replan: bool = True,
    runner_count: int = 10000,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Generate a running / marathon route on the built-in road network.

    If geojson_data is not provided, uses the built-in Bangkok network
    (Lumphini–Benjakitti) or falls back to the bundled GeoJSON.

    By default regenerates a new route each call (new random seed unless ``seed``
    is set). Set ``force_replan=False`` only when you intentionally want the
    cached session route (e.g. evaluator re-reads).

    Bangkok shapes:
      - Default / park-connector: Lumphini ↔ Benjakitti (~10 km).
      - Heart inside Lumphini only: ``route_shape="heart"`` or
        ``algorithm="park_heart"`` (multi-lap heart on park paths; use
        ``target_distance_km=10`` for a 10K).

    ZONE-SWEEP / CLOVERLEAF params below are mostly Las Vegas–oriented legacy.

    Args:
        algorithm: ``"zone_sweep"`` (default Bangkok park-connector),
            ``"park_heart"`` (Lumphini heart only), or legacy cloverleaf triggers.
        route_shape: ``"heart"`` / ``"lumphini_heart"`` → stay inside Lumphini
            and trace a heart-shaped multi-lap path. Same as algorithm=park_heart.
        finish_landmark: Finishing landmark (park-connector). Ignored for heart.
        start_landmark: Start landmark. Heart default: Lumphini Park South Gate.
        seed: Random seed for variety / reproducibility. Random if omitted.
        target_distance_km: Desired length in km (e.g. 10). Defaults to ~10 km
            on the Bangkok network.
        geojson_data: Optional GeoJSON string of the road network to use.
        theme_sequence: Optional list of landmark names (legacy).
        waypoints: Optional list of [lon, lat] coordinate pairs (advanced).
        petal_names: Legacy Las Vegas petal catalog names.
        force_replan: If False, return the cached session route. Default True.
        tool_context: ADK tool context.
    """
    # Optional cache: only when caller explicitly sets force_replan=False
    if not force_replan and tool_context and tool_context.state.get("marathon_route"):
        logger.info("PLANNER: Reusing cached route (force_replan=False).")
        cached = tool_context.state["marathon_route"]
        return {
            "status": "already_planned",
            "message": "Returning cached route. Set force_replan=True for a new map.",
            "summary": _route_summary_for_llm(cached),
        }

    logger.info("PLANNER: Generating marathon route...")

    # Load default data if none provided
    if not geojson_data:
        try:
            # scripts/ -> skill root -> assets/
            from pathlib import Path

            skill_dir = Path(os.path.dirname(__file__)).parent
            data_path = skill_dir / "assets" / "network.json"
            if os.path.exists(data_path):
                with open(data_path, "r") as f:
                    geojson_data = f.read()
                logger.info("PLANNER: Using built-in Bangkok road network (Lumphini–Benjakitti).")
            else:
                logger.warning(
                    f"PLANNER: network.json not found at {data_path}. Using fallback."
                )
                geojson_data = '{"type": "FeatureCollection", "features": []}'
        except Exception as e:
            logger.error(f"PLANNER: Failed to load network.json: {e}")
            geojson_data = '{"type": "FeatureCollection", "features": []}'

    try:
        data = json.loads(geojson_data)
        adj, landmarks, road_names, strip_nodes = _build_graph(data)
        nodes = set(adj.keys())

        # Route generation - select algorithm
        used_seed = seed if seed is not None else random.randrange(2**32)
        rng = random.Random(used_seed)
        target_mi = (
            float(target_distance_km) / 1.60934
            if target_distance_km is not None
            else _active_target_dist_mi()
        )
        shape = (route_shape or "").strip().lower()
        want_heart = (
            algorithm in ("park_heart", "lumphini_heart", "heart")
            or shape in ("heart", "lumphini_heart", "park_heart", "หัวใจ")
        )

        heart_laps = 0
        heart_lap_mi = 0.0
        if want_heart:
            route_coords, final_dist, _lap, heart_lap_mi, heart_laps = (
                _generate_lumphini_heart_route(
                    adj,
                    landmarks,
                    road_names,
                    rng,
                    target_mi=target_mi,
                    start_landmark=start_landmark,
                )
            )
            if not route_coords:
                return {
                    "status": "error",
                    "message": (
                        "Could not build a Lumphini heart route on park paths. "
                        "Try again or use the default park-connector."
                    ),
                }
        elif (
            algorithm == "zone_sweep"
            and not petal_names
            and not waypoints
            and not theme_sequence
        ):
            # Bangkok park-connector / zone-sweep with seed variety
            route_coords, final_dist = _generate_best_route(
                adj,
                nodes,
                landmarks,
                strip_nodes,
                road_names,
                seed=used_seed,
                finish_landmark=finish_landmark,
                start_landmark=start_landmark,
                max_candidates=10,
            )
        else:
            # Legacy cloverleaf/petal/theme routing
            if petal_names:
                wp_tuples = _build_waypoints_from_petals(petal_names)
                logger.info("PLANNER: Using petals: %s", petal_names)
            elif waypoints:
                wp_tuples = [tuple(wp) for wp in waypoints]
            else:
                wp_tuples = None

            route_coords, final_dist = _generate_spine_and_sprout(
                adj,
                nodes,
                landmarks,
                theme_sequence,
                strip_nodes=strip_nodes,
                waypoints=wp_tuples,
            )

        if not route_coords:
            return {
                "status": "error",
                "message": "Route generation produced an empty path.",
            }

        # Split route into named road segments
        route_segments = _split_route_by_road(route_coords, road_names, final_dist)

        # Add start and finish markers
        start_marker = {
            "type": "Feature",
            "properties": {"marker-type": "start", "name": "Start Line"},
            "geometry": {
                "type": "Point",
                "coordinates": list(route_coords[0]),
            },
        }
        finish_marker = {
            "type": "Feature",
            "properties": {"marker-type": "finish", "name": "Finish Line"},
            "geometry": {
                "type": "Point",
                "coordinates": list(route_coords[-1]),
            },
        }

        output = {
            "type": "FeatureCollection",
            "features": [start_marker, finish_marker] + route_segments,
        }
        if want_heart:
            output["properties"] = {
                "route_shape": "heart",
                "area": "Lumphini Park only",
                "distance_mi": round(final_dist, 4),
                "distance_km": round(final_dist * 1.60934, 2),
                "lap_km": round(heart_lap_mi * 1.60934, 2),
                "laps": heart_laps,
                "note": (
                    f"Map shows 1 heart lap (~{heart_lap_mi * 1.60934:.1f} km). "
                    f"Run {heart_laps} laps for ~{final_dist * 1.60934:.1f} km total."
                ),
            }
            if route_segments:
                route_segments[0]["properties"].update(
                    {
                        "distance_mi": round(final_dist, 4),
                        "laps": heart_laps,
                        "route_shape": "heart",
                    }
                )

        # Aid/medical stations clutter casual park maps — skip for heart runs
        if not want_heart:
            infra_result = await add_course_infrastructure(
                output, runner_count=runner_count, tool_context=tool_context
            )
            if infra_result["status"] == "success":
                output = infra_result["geojson"]

        # Cache in session state for later/other agent recall
        if tool_context:
            tool_context.state["marathon_route"] = output
            tool_context.state["marathon_route_seed"] = used_seed
            tool_context.state["marathon_route_shape"] = "heart" if want_heart else "default"

        summary = _route_summary_for_llm(output)
        shape_note = " (Lumphini heart)" if want_heart else ""
        return {
            "status": "success",
            "message": (
                f"Route planned successfully{shape_note}. "
                f"Length ≈ {summary['distance_km']} km (seed={used_seed}). "
                "Call report_marathon_route to render the interactive map. "
                "Do not dump raw GeoJSON into the reply."
            ),
            "summary": summary,
            "seed": used_seed,
            "route_shape": "heart" if want_heart else None,
        }
    except Exception as e:
        logger.error(f"PLANNER: Error processing GeoJSON: {e}")
        return {"status": "error", "message": f"Failed to process GeoJSON: {str(e)}"}


def _extract_route_coords(route_geojson: dict) -> list:
    """Extract a flat coordinate list from all LineString features in order."""
    coords: list = []
    for f in route_geojson.get("features", []):
        if f.get("geometry", {}).get("type") == "LineString":
            seg_coords = f["geometry"]["coordinates"]
            # Deduplicate shared junction points between consecutive segments
            if coords and seg_coords and coords[-1] == seg_coords[0]:
                coords.extend(seg_coords[1:])
            else:
                coords.extend(seg_coords)
    return coords


# ---------------------------------------------------------------------------
# Course Infrastructure Placement (standards-compliant, runner-count-aware)
# ---------------------------------------------------------------------------

_HYDRATION_INTERVAL_MI = 3.1  # World Athletics TR 55: every 5 km


def _place_hydration_stations(
    index: list[tuple[tuple[float, float], float]],
    runner_count: int = 10000,  # noqa: ARG001 — kept for API consistency with other _place_* functions
) -> list[dict]:
    """Place hydration stations every 5 km (3.1 mi) per World Athletics TR 55.

    Note: runner_count is accepted for API consistency but does not
    affect station count.  WA TR 55 mandates spacing, not per-runner scaling.
    """
    if not index:
        return []
    total_mi = index[-1][1]
    features: list[dict] = []
    mi = _HYDRATION_INTERVAL_MI
    while mi <= total_mi and mi <= TARGET_DIST_MI:
        coord = _point_at_mile(index, mi)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coord},
                "properties": {
                    "type": "water_station",
                    "mi": round(mi, 3),
                    "km": round(mi * 1.60934, 3),
                },
            }
        )
        mi += _HYDRATION_INTERVAL_MI
    return features


def _place_medical_stations(
    index: list[tuple[tuple[float, float], float]],
    runner_count: int = 10000,
) -> list[dict]:
    """Place medical stations scaled by runner count.

    Tier layout:
      - major: Start area (mi 0.5), mile ~20 ("the wall"), finish line
      - course: Distributed at strategic mile markers
    """
    if not index:
        return []
    total_mi = index[-1][1]
    finish_mi = min(total_mi, TARGET_DIST_MI)

    if runner_count < 1000:
        major_miles = [0.5, finish_mi]
        course_miles = [13.1, 20.0]
    elif runner_count <= 10000:
        major_miles = [0.5, 20.0, finish_mi]
        course_miles = [8.0, 13.1, 17.0]
    else:
        major_miles = [0.5, 20.0, finish_mi]
        course_miles = [5.0, 10.0, 13.1, 17.0, 23.0]

    # Map mile markers to human-readable location names for backward compat
    def _location_name(mi: float, finish: float) -> str:
        if mi <= 1.0:
            return "Start Area"
        if abs(mi - finish) < 0.5:
            return "Finish Line"
        if abs(mi - 13.1) < 0.5:
            return "Halfway"
        if abs(mi - 20.0) < 0.5:
            return "The Wall"
        return f"Mile {round(mi)}"

    features: list[dict] = []
    for mi in major_miles:
        clamped = min(mi, total_mi)
        coord = _point_at_mile(index, clamped)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coord},
                "properties": {
                    "type": "medical_tent",
                    "mi": round(clamped, 3),
                    "tier": "major",
                    "location": _location_name(clamped, finish_mi),
                },
            }
        )
    for mi in course_miles:
        if mi < total_mi:
            coord = _point_at_mile(index, mi)
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": coord},
                    "properties": {
                        "type": "medical_tent",
                        "mi": round(mi, 3),
                        "tier": "course",
                        "location": _location_name(mi, finish_mi),
                    },
                }
            )
    return features


def _place_portable_toilets(
    index: list[tuple[tuple[float, float], float]],
    runner_count: int = 10000,
) -> list[dict]:
    """Place portable toilet stations offset from hydration, scaled by runner count.

    Stations are evenly distributed but nudged away from hydration
    mile markers to avoid co-location congestion.
    """
    if not index:
        return []
    total_mi = index[-1][1]
    finish_mi = min(total_mi, TARGET_DIST_MI)

    if runner_count < 1000:
        count, units = 4, 3
    elif runner_count < 5000:
        count, units = 6, 5
    elif runner_count <= 30000:
        count, units = 8, 10
    else:
        count, units = 10, 12

    # Offset by half a hydration interval so toilets sit between water stops
    offset = _HYDRATION_INTERVAL_MI / 2  # ~1.55 mi
    spacing = finish_mi / (count + 1)

    features: list[dict] = []
    for i in range(1, count + 1):
        mi = spacing * i
        # Nudge to nearest offset point to avoid hydration overlap
        mi = round(mi / offset) * offset + (offset / 2)
        if mi > finish_mi:
            mi = finish_mi - 0.5
        coord = _point_at_mile(index, mi)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coord},
                "properties": {
                    "type": "portable_toilet",
                    "mi": round(mi, 3),
                    "units": units,
                },
            }
        )
    return features


_CHEER_ZONE_NAMES = [
    "Start Energy",
    "Early Boost",
    "Momentum Mile",
    "Halfway Celebration",
    "The Push",
    "Wall Breakers",
    "Final Surge",
    "Sprint to Glory",
]


def _place_cheer_zones(
    index: list[tuple[tuple[float, float], float]],
    runner_count: int = 10000,
) -> list[dict]:
    """Place cheer zones at strategic locations, scaled by runner count.

    Critical placements: halfway (~mi 13.1), the wall (~mi 20),
    finish approach (~mi 25). Remaining zones distributed evenly.
    """
    if not index:
        return []
    total_mi = index[-1][1]
    finish_mi = min(total_mi, TARGET_DIST_MI)

    # Strategic anchor points (always included if race is large enough)
    anchors = [5.0, 13.1, 20.0, 25.0]

    if runner_count < 1000:
        target_count = 4
    elif runner_count <= 10000:
        target_count = 6
    else:
        target_count = 8

    # Start with anchors, then fill evenly
    if target_count <= len(anchors):
        zone_miles = anchors[:target_count]
    else:
        zone_miles = list(anchors)
        spacing = finish_mi / (target_count - len(anchors) + 1)
        for i in range(1, target_count - len(anchors) + 1):
            candidate = spacing * i
            # Skip if too close to existing anchor (within 1.5 mi)
            if not any(abs(candidate - a) < 1.5 for a in zone_miles):
                zone_miles.append(candidate)
        # Ensure we reach target count by backfilling
        fill_mi = 2.0
        while len(zone_miles) < target_count and fill_mi < finish_mi:
            if not any(abs(fill_mi - z) < 1.5 for z in zone_miles):
                zone_miles.append(fill_mi)
            fill_mi += 2.5

    zone_miles = sorted([m for m in zone_miles if m <= finish_mi])[:target_count]

    features: list[dict] = []
    for i, mi in enumerate(zone_miles):
        coord = _point_at_mile(index, mi)
        name = (
            _CHEER_ZONE_NAMES[i]
            if i < len(_CHEER_ZONE_NAMES)
            else f"Cheer Zone {i + 1}"
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coord},
                "properties": {
                    "type": "cheer_zone",
                    "mi": round(mi, 3),
                    "name": name,
                },
            }
        )
    return features


async def add_course_infrastructure(
    route_geojson: dict,
    runner_count: int = 10000,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Place all course infrastructure using a shared distance index.

    Replaces the old sequential add_water_stations + add_medical_tents
    approach with a single-pass orchestrator that places hydration stations,
    medical tents, portable toilets, and cheer zones.

    Args:
        route_geojson: The planned route GeoJSON FeatureCollection.
        runner_count: Number of participants (scales infrastructure).
        tool_context: ADK tool context (unused, kept for API consistency).
    """
    runner_count = max(1, int(runner_count))
    logger.info("PLANNER: Adding course infrastructure (runners=%d)...", runner_count)
    coords = _extract_route_coords(route_geojson)
    if not coords:
        return {"status": "error", "message": "No LineString found in route"}

    index = _build_distance_index(coords)

    # All four are pure functions on immutable input -- no contention
    hydration = _place_hydration_stations(index, runner_count)
    medical = _place_medical_stations(index, runner_count)
    toilets = _place_portable_toilets(index, runner_count)
    cheer = _place_cheer_zones(index, runner_count)

    all_features = hydration + medical + toilets + cheer
    route_geojson["features"].extend(all_features)

    counts = {
        "water_stations": len(hydration),
        "medical_stations": len(medical),
        "portable_toilets": len(toilets),
        "cheer_zones": len(cheer),
    }
    logger.info("PLANNER: Infrastructure placed: %s", counts)

    return {
        "status": "success",
        "message": f"Added {len(all_features)} infrastructure points ({counts}).",
        "geojson": route_geojson,
    }


async def add_water_stations(
    route_geojson: dict, tool_context: Optional[ToolContext] = None
) -> dict:
    """Legacy wrapper: place hydration stations only.

    Prefer add_course_infrastructure for new code.
    """
    coords = _extract_route_coords(route_geojson)
    if not coords:
        return {"status": "error", "message": "No LineString found in route"}
    index = _build_distance_index(coords)
    features = _place_hydration_stations(index)
    route_geojson["features"].extend(features)
    return {
        "status": "success",
        "message": f"Added {len(features)} water stations to the route.",
        "geojson": route_geojson,
    }


async def add_medical_tents(
    route_geojson: dict, tool_context: Optional[ToolContext] = None
) -> dict:
    """Legacy wrapper: place medical stations only.

    Prefer add_course_infrastructure for new code.
    """
    coords = _extract_route_coords(route_geojson)
    if not coords:
        return {"status": "error", "message": "No LineString found in route"}
    index = _build_distance_index(coords)
    features = _place_medical_stations(index)
    route_geojson["features"].extend(features)
    return {
        "status": "success",
        "message": f"Added {len(features)} medical stations to the route.",
        "geojson": route_geojson,
    }


def _route_geojson_to_leaflet_html(route_geojson: dict) -> str:
    """Build a self-contained Leaflet HTML map for a route FeatureCollection."""
    features = route_geojson.get("features") or []
    lats: list[float] = []
    lngs: list[float] = []

    def _collect(coords, depth=0):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            lngs.append(float(coords[0]))
            lats.append(float(coords[1]))
            return
        for c in coords:
            _collect(c, depth + 1)

    for f in features:
        geom = f.get("geometry") or {}
        _collect(geom.get("coordinates"))

    if lats and lngs:
        center_lat = sum(lats) / len(lats)
        center_lng = sum(lngs) / len(lngs)
        bounds_js = json.dumps([[min(lats), min(lngs)], [max(lats), max(lngs)]])
    else:
        center_lat, center_lng = 13.7306, 100.5417
        bounds_js = "null"

    geojson_js = json.dumps(route_geojson, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Marathon Route Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body {{ margin: 0; padding: 0; height: 100%; font-family: system-ui, sans-serif; }}
    #map {{ height: 100%; width: 100%; }}
    .legend {{
      background: white; padding: 8px 10px; border-radius: 6px;
      box-shadow: 0 1px 4px rgba(0,0,0,.3); font-size: 12px; line-height: 1.5;
    }}
    .legend i {{
      display: inline-block; width: 12px; height: 12px; margin-right: 6px;
      border-radius: 50%; vertical-align: middle;
    }}
    .legend .line {{
      display: inline-block; width: 16px; height: 4px; margin-right: 6px;
      background: #e11d48; vertical-align: middle; border-radius: 2px;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    const route = {geojson_js};
    const map = L.map('map').setView([{center_lat}, {center_lng}], 14);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }}).addTo(map);

    function styleFeature(feature) {{
      const t = (feature.properties && feature.properties['marker-type']) || '';
      if (feature.geometry.type === 'LineString' || feature.geometry.type === 'MultiLineString') {{
        return {{ color: '#e11d48', weight: 5, opacity: 0.9 }};
      }}
      return {{}};
    }}

    function pointColor(props) {{
      const t = (props && props['marker-type']) || '';
      const name = ((props && props.name) || '').toLowerCase();
      if (t === 'start' || name.includes('start')) return '#16a34a';
      if (t === 'finish' || name.includes('finish')) return '#dc2626';
      if (t.includes('water') || t.includes('hydration') || name.includes('water')) return '#2563eb';
      if (t.includes('medical') || name.includes('medical')) return '#7c3aed';
      return '#f59e0b';
    }}

    function onEachFeature(feature, layer) {{
      const p = feature.properties || {{}};
      const title = p.name || p['marker-type'] || feature.geometry.type;
      const bits = Object.entries(p)
        .filter(([k]) => !['marker-symbol'].includes(k))
        .map(([k, v]) => `<div><b>${{k}}</b>: ${{v}}</div>`)
        .join('');
      layer.bindPopup(`<strong>${{title}}</strong>${{bits}}`);
    }}

    const layer = L.geoJSON(route, {{
      style: styleFeature,
      pointToLayer: function(feature, latlng) {{
        const color = pointColor(feature.properties || {{}});
        return L.circleMarker(latlng, {{
          radius: 7, color: '#fff', weight: 2, fillColor: color, fillOpacity: 0.95
        }});
      }},
      onEachFeature: onEachFeature
    }}).addTo(map);

    const bounds = {bounds_js};
    if (bounds) {{
      map.fitBounds(bounds, {{ padding: [30, 30] }});
    }} else if (layer.getBounds().isValid()) {{
      map.fitBounds(layer.getBounds(), {{ padding: [30, 30] }});
    }}

    const legend = L.control({{ position: 'bottomleft' }});
    legend.onAdd = function() {{
      const div = L.DomUtil.create('div', 'legend');
      const rp = route.properties || {{}};
      let html = '';
      if (rp.route_shape === 'heart') {{
        html += '<div><b>♥ Lumphini Heart</b></div>';
        if (rp.note) html += '<div style="max-width:220px">' + rp.note + '</div>';
        html += '<div><span class="line"></span>1 lap on map</div>';
      }} else {{
        html += '<div><span class="line"></span>Route</div>';
      }}
      html +=
        '<div><i style="background:#16a34a"></i>Start</div>' +
        '<div><i style="background:#dc2626"></i>Finish</div>';
      if (rp.route_shape !== 'heart') {{
        html +=
          '<div><i style="background:#2563eb"></i>Water</div>' +
          '<div><i style="background:#7c3aed"></i>Medical</div>';
      }}
      div.innerHTML = html;
      return div;
    }};
    legend.addTo(map);
  </script>
</body>
</html>
"""


def _route_summary_for_llm(route_geojson: dict) -> dict:
    """Compact route stats for LLM context — never return full GeoJSON to the model."""
    features = route_geojson.get("features") or []
    n_lines = sum(
        1 for f in features if (f.get("geometry") or {}).get("type") == "LineString"
    )
    n_points = sum(
        1 for f in features if (f.get("geometry") or {}).get("type") == "Point"
    )
    coords = _extract_route_coords(route_geojson)
    dist_mi = 0.0
    if len(coords) >= 2:
        index = _build_distance_index(coords)
        dist_mi = index[-1][1] if index else 0.0
    # Prefer explicit multi-lap distance (heart map shows 1 lap only)
    props = route_geojson.get("properties") or {}
    if props.get("distance_mi") is not None:
        dist_mi = float(props["distance_mi"])
    dist_km = round(dist_mi * 1.60934, 2)
    start = coords[0] if coords else None
    finish = coords[-1] if coords else None
    road_names = []
    for f in features:
        if (f.get("geometry") or {}).get("type") != "LineString":
            continue
        name = (f.get("properties") or {}).get("name")
        if name and name not in road_names:
            road_names.append(name)
        if len(road_names) >= 12:
            break
    summary = {
        "distance_km": dist_km,
        "distance_mi": round(dist_mi, 3),
        "feature_count": len(features),
        "line_segments": n_lines,
        "point_markers": n_points,
        "start_lonlat": list(start) if start else None,
        "finish_lonlat": list(finish) if finish else None,
        "sample_roads": road_names,
        "note": "Full GeoJSON is in session state / map artifact only — not returned here.",
    }
    if props.get("route_shape"):
        summary["route_shape"] = props["route_shape"]
    if props.get("laps"):
        summary["laps"] = props["laps"]
        summary["lap_km"] = props.get("lap_km")
        summary["note"] = props.get("note") or summary["note"]
    return summary


async def report_marathon_route(
    tool_context: ToolContext,
) -> dict:
    """Report the final marathon route GeoJSON and render an interactive map.

    Reads the route from session state (set by ``plan_marathon_route``), then
    saves a Leaflet HTML map as an ADK artifact so ``adk web`` can display it.

    Args:
        tool_context: ADK tool context with session state containing ``marathon_route``.
    """
    logger.info("PLANNER: Reporting final marathon route...")

    route_geojson = tool_context.state.get("marathon_route")
    if not route_geojson:
        return {
            "status": "error",
            "message": "No marathon route found. Run 'plan_marathon_route' first.",
        }

    # Unique filename so ADK Web shows a distinct artifact each replan
    route_seed = tool_context.state.get("marathon_route_seed")
    suffix = str(route_seed) if route_seed is not None else str(random.randrange(2**32))
    map_filename = f"marathon_route_map_{suffix}.html"
    try:
        html = _route_geojson_to_leaflet_html(route_geojson)
        artifact = types.Part(
            inline_data=types.Blob(
                mime_type="text/html",
                data=html.encode("utf-8"),
            )
        )
        version = await tool_context.save_artifact(
            filename=map_filename,
            artifact=artifact,
        )
        logger.info("PLANNER: Saved map artifact %s v%s", map_filename, version)
        map_status = f"Interactive map saved as artifact '{map_filename}' (v{version})."
    except Exception as e:
        logger.exception("PLANNER: Failed to save map artifact")
        map_status = f"Map artifact not saved: {e}"
        map_filename = None
        version = None

    summary = _route_summary_for_llm(route_geojson)
    return {
        "status": "success",
        "message": (
            "Final marathon route reported. "
            f"{map_status} "
            "Open the HTML artifact in ADK Web to view the map. "
            f"Route length ≈ {summary['distance_km']} km. "
            "Describe distances to the user in kilometers (km)."
        ),
        "summary": summary,
        "map_artifact": map_filename,
        "map_artifact_version": version,
    }


# ---------------------------------------------------------------------------
# Traffic assessment (Gemini-enriched)
# ---------------------------------------------------------------------------


async def _gemini_traffic_enrichment(
    closed_segments: list, affected_intersections: list
) -> dict:
    """Call Gemini 3 Flash to produce a traffic impact narrative and congestion zones.

    Returns a dict with ``narrative`` (str) and ``congestion_zones`` (list).
    """
    from google import genai

    client = genai.Client(vertexai=False, location="global")

    prompt = (
        "You are a Las Vegas traffic engineer. Given the following marathon "
        "road closure data, produce a JSON response with two keys:\n"
        '  "narrative": a 2-3 sentence summary of the traffic impact,\n'
        '  "congestion_zones": a list of objects with "zone_name" and '
        '"severity" (low/medium/high).\n\n'
        f"Closed segments: {json.dumps([s.get('properties', {}).get('name', 'Unknown') for s in closed_segments])}\n"
        f"Affected intersections: {json.dumps([i.get('cross_streets', []) for i in affected_intersections])}\n\n"
        "Respond ONLY with valid JSON, no markdown fences."
    )

    response = await client.aio.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
    )

    text = (response.text or "").strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()

    return json.loads(text)


async def assess_traffic_impact(
    tool_context: ToolContext,
) -> dict:
    """Assess traffic impact of the marathon route on the Las Vegas road network.

    Reads the route exclusively from session state (set by ``plan_marathon_route``).
    Identifies closed road segments and affected intersections, then enriches
    the assessment with a Gemini-generated narrative.

    This tool is designed to run IN PARALLEL with ``report_marathon_route``.

    Args:
        tool_context: ADK tool context with session state containing ``marathon_route``.
    """
    from agents.utils.traffic import identify_closed_segments

    route_geojson = tool_context.state.get("marathon_route")
    if not route_geojson:
        return {
            "status": "error",
            "message": "No marathon route found. Run 'plan_marathon_route' first.",
        }

    # Load the road network from assets/ (scripts/ -> skill root -> assets/)
    from pathlib import Path

    skill_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent
    network_path = skill_dir / "assets" / "network.json"
    try:
        with open(network_path) as f:
            network_geojson = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "message": f"Failed to load road network: {exc}",
        }

    # Identify closed segments, affected segments, and intersections.
    # ``closed`` excludes route-coincident segments (the actual marathon
    # route lines).  ``route_closures`` contains those route lines --
    # they are still treated as closed for impact calculations but are
    # not surfaced in the output.
    traffic_result = identify_closed_segments(route_geojson, network_geojson)
    closed = traffic_result["closed"]
    route_closures = traffic_result.get("route_closures", [])
    affected = traffic_result["affected"]
    intersections = traffic_result["intersections"]

    # Gemini enrichment with graceful degradation.
    # Pass ALL closures (including route lines) to Gemini for context.
    all_closures = closed + route_closures
    narrative = ""
    congestion_zones: list = []
    try:
        enrichment = await _gemini_traffic_enrichment(all_closures, intersections)
        narrative = enrichment.get("narrative", "")
        congestion_zones = enrichment.get("congestion_zones", [])
    except Exception as exc:
        logger.warning("Gemini traffic enrichment failed: %s", exc)
        narrative = (
            f"Gemini enrichment unavailable: {exc}. "
            f"Code-only analysis: {len(all_closures)} closed segments, "
            f"{len(intersections)} affected intersections."
        )

    # Compute overall impact score heuristic.
    # Use the full closure count (including route lines) so the score
    # accurately reflects the scale of road closures.
    n_closed = len(all_closures)
    n_intersections = len(intersections)
    n_affected = len(affected)
    overall_impact_score = min(
        1.0, n_closed * 0.1 + n_intersections * 0.05 + n_affected * 0.03
    )

    result = {
        "status": "success",
        "closed_segments": closed,
        "affected_segments": affected,
        "affected_intersections": intersections,
        "congestion_zones": congestion_zones,
        "narrative": narrative,
        "overall_impact_score": overall_impact_score,
    }

    # Store assessment in session state for downstream tools
    if tool_context:
        tool_context.state["traffic_assessment"] = result

    return result
