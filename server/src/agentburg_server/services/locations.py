"""Ashenmere — world locations with coordinates and lore."""

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

LOCATION_DESCRIPTIONS: dict[str, str] = {
    "town_center": "The cracked stone plaza around the old Consortium bell tower. Deals are announced, disputes aired, reputations made or destroyed.",
    "market": "Roofed arcade of salvaged timber stalls. Prices chalked on slate. Haggling at volume — silence from a trader is a threat.",
    "bank": "Only stone building that survived the collapse. Iron vault door with Consortium seal. All debts posted on the chalkboard for everyone to see.",
    "courthouse": "Squat basalt hall where the original ledger sits chained to a podium. Every deal over 5 gold recorded here. The ledger is closer to truth than memory.",
    "tavern": "The Sulphur Lantern. Mineral lamps glow yellow-green. Ale brewed from lake-grain with a metallic bite. More deals collapse here than are made.",
    "workshop": "Repurposed mine-head building. Anvils ring from dawn as scrap iron becomes tools. Quality varies with the smith's mood and sobriety.",
    "farm": "Terraced hillside plots. Volcanic soil yields dense wheat — unless the sulfur rises and burns the crop overnight.",
    "dock": "Weathered pier into Ashenmere Lake. Flat-bottomed boats bring trade from across the water. On still mornings the thermal vents make it steam like a cauldron.",
    "residential_north": "Uphill quarter from Consortium manager housing. Better walls, glass windows. Living here signals old money or new ambition.",
    "residential_south": "Downhill worker cottages near the sealed mine shaft. Patched with mine timber and reed thatch. Cheaper, louder, closer to truth.",
}

TIME_OF_DAY_FLAVOR: dict[str, str] = {
    "morning": "Mist rolls off the warm lake. Early risers stake claims — best stall, first forge slot, freshest dock arrivals. Last night's debts are honored or denied.",
    "afternoon": "Fog burns off. Hammers ring, market shouts, chalk scratches on the bank's debt board. Fortunes shift — a good trade, a bad harvest report, a rumor from across the lake.",
    "evening": "The Sulphur Lantern fills. Farmers descend with dirt under their nails. Everyone watches everyone to gauge who won and who lost. The lake begins to glow.",
    "night": "Quiet but not still. The lake paints the fog sickly green. Sounds carry strangely. Those moving through streets at this hour are guarding something or stealing it.",
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
