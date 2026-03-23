"""World locations with coordinates for the 2D map."""

# Location name → (x, y) on a 1000x1000 grid
LOCATIONS: dict[str, tuple[int, int]] = {
    "town_center": (500, 500),
    "market": (300, 400),
    "bank": (700, 400),
    "residential_north": (500, 200),
    "residential_south": (500, 800),
    "tavern": (200, 600),
    "workshop": (800, 600),
    "courthouse": (500, 300),
    "farm": (150, 800),
    "dock": (850, 800),
}

LOCATION_NAMES = list(LOCATIONS.keys())


def get_location_coords(location: str) -> tuple[int, int]:
    """Get (x, y) coordinates for a named location."""
    return LOCATIONS.get(location, LOCATIONS["town_center"])


def nearest_location(x: int, y: int) -> str:
    """Find the nearest named location to given coordinates."""
    best = "town_center"
    best_dist = float("inf")
    for name, (lx, ly) in LOCATIONS.items():
        dist = (x - lx) ** 2 + (y - ly) ** 2
        if dist < best_dist:
            best_dist = dist
            best = name
    return best
