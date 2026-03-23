"""World events system — random events that create economic drama and disruption.

Events fire probabilistically each macro tick, modifying production rates,
market prices, and agent incomes. They force agents to adapt their strategies,
creating emergent narrative and economic turbulence.

Design principles:
- Probability budget: ~21% per tick with category-blocking (event every ~5 ticks)
- Mix of positive, negative, and mixed-impact events
- Location-specific effects create winners and losers simultaneously
- Rare events are memorable and reshape the economy for multiple ticks
- All modifiers are multiplicative against base values
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WorldEvent:
    """A random event that can fire during a macro tick."""

    name: str
    description: str
    probability: float          # chance per macro tick (0.01 - 0.15)
    duration: int               # ticks the effect persists
    effects: dict[str, Any]     # modifier payload
    announcement: str           # broadcast text when the event fires
    category: str               # weather | economic | social | rare


# ---------------------------------------------------------------------------
# Event registry — 14 events across 4 categories
# ---------------------------------------------------------------------------
# Probability budget (tuned via 100k-tick Monte Carlo simulation):
#   Weather  (4): 0.030 + 0.025 + 0.030 + 0.020 = 0.105
#   Economic (4): 0.025 + 0.015 + 0.020 + 0.015 = 0.075
#   Social   (4): 0.030 + 0.010 + 0.020 + 0.015 = 0.075
#   Rare     (2): 0.008 + 0.003                  = 0.011
#   --------------------------------------------------
#   Raw sum: 0.266 => raw P(>=1) = 23.6%
#   With category-blocking: effective ~20.8% per tick (~1 event per 4.8 ticks)
#   Active effects coverage: ~53% of all ticks
# ---------------------------------------------------------------------------

WORLD_EVENTS: list[WorldEvent] = [
    # ===================================================================
    # WEATHER (4 events)
    # ===================================================================
    WorldEvent(
        name="Torrential Storm",
        description=(
            "Black clouds roll in without warning. Torrential rain floods the "
            "fields and washes out the dock roads, crippling food production "
            "and halting fishing operations."
        ),
        probability=0.03,
        duration=3,
        effects={
            "production_modifier": {
                "wheat": 0.3,
                "meat": 0.5,
                "fish": 0.2,
                "bread": 0.6,
            },
            "price_modifier": {
                "wheat": 1.8,
                "fish": 2.0,
                "bread": 1.5,
                "meat": 1.6,
            },
            "specific_locations": ["farm", "dock"],
        },
        announcement=(
            "A violent storm batters the region! Farmlands are flooded and "
            "the docks are impassable. Food prices skyrocket as supply dwindles."
        ),
        category="weather",
    ),

    WorldEvent(
        name="Drought",
        description=(
            "Weeks without rain have parched the soil. Crops wither in the fields "
            "and livestock grow thin, while the workshop forges burn hotter than ever."
        ),
        probability=0.025,
        duration=4,
        effects={
            "production_modifier": {
                "wheat": 0.4,
                "meat": 0.6,
                "wool": 0.5,
                "ale": 0.7,     # less grain for brewing
            },
            "price_modifier": {
                "wheat": 1.6,
                "meat": 1.4,
                "ale": 1.3,
                "iron": 0.9,    # forges unaffected, slight oversupply
                "tools": 0.9,
            },
            "specific_locations": ["farm", "residential_north"],
        },
        announcement=(
            "A punishing drought grips the land! Wells run low and crops "
            "fail. Farmers stare at cracked earth while the town scrambles "
            "for grain reserves."
        ),
        category="weather",
    ),

    WorldEvent(
        name="Bountiful Harvest",
        description=(
            "Perfect weather and fertile soil produce a harvest beyond anyone's "
            "memory. Barns overflow with grain and livestock fatten on lush pastures."
        ),
        probability=0.03,
        duration=3,
        effects={
            "production_modifier": {
                "wheat": 2.0,
                "meat": 1.8,
                "bread": 1.5,
                "wool": 1.4,
            },
            "price_modifier": {
                "wheat": 0.6,
                "bread": 0.7,
                "meat": 0.7,
            },
            "income_modifier": 1.1,     # general prosperity
            "specific_locations": ["farm"],
        },
        announcement=(
            "A magnificent harvest! Golden fields stretch to the horizon and "
            "every barn is bursting at the seams. Food is plentiful and spirits "
            "are high across the land."
        ),
        category="weather",
    ),

    WorldEvent(
        name="Fog-Bound Coast",
        description=(
            "An impenetrable fog blankets the coastline. Fishing boats cannot "
            "navigate and trade ships anchor offshore, waiting for clear skies."
        ),
        probability=0.02,
        duration=2,
        effects={
            "production_modifier": {
                "fish": 0.1,
                "spices": 0.3,    # imported via sea
            },
            "price_modifier": {
                "fish": 2.2,
                "spices": 1.8,
            },
            "specific_locations": ["dock"],
        },
        announcement=(
            "A thick, eerie fog swallows the coastline! The docks fall "
            "silent as no ship dares sail. Fish mongers have nothing to sell "
            "and spice traders wring their hands."
        ),
        category="weather",
    ),

    # ===================================================================
    # ECONOMIC (4 events)
    # ===================================================================
    WorldEvent(
        name="Merchant Caravan Arrives",
        description=(
            "A grand caravan from distant lands rolls into the market square, "
            "wagons groaning under exotic goods. Competition drives local prices "
            "down but opens new trade opportunities."
        ),
        probability=0.025,
        duration=2,
        effects={
            "production_modifier": {
                "spices": 2.5,
                "cloth": 2.0,
                "gold": 1.5,
                "medicine": 2.0,
            },
            "price_modifier": {
                "spices": 0.5,
                "cloth": 0.6,
                "medicine": 0.6,
                "gold": 0.8,
            },
            "income_modifier": 1.15,    # trade activity boost
            "specific_locations": ["market"],
        },
        announcement=(
            "A merchant caravan from the eastern kingdoms has arrived! "
            "Exotic spices, fine cloth, and rare medicines flood the market. "
            "Shrewd traders see opportunity; local merchants see ruin."
        ),
        category="economic",
    ),

    WorldEvent(
        name="Market Panic",
        description=(
            "Rumors of a distant kingdom's financial collapse spread through "
            "the taverns. Traders rush to sell before prices crater further, "
            "creating a self-fulfilling prophecy."
        ),
        probability=0.015,
        duration=3,
        effects={
            "price_modifier": {
                "gold": 0.5,
                "cloth": 0.6,
                "spices": 0.5,
                "iron": 0.7,
                "tools": 0.7,
                "leather": 0.6,
            },
            "income_modifier": 0.7,     # economic contraction
        },
        announcement=(
            "PANIC IN THE MARKETS! Rumors of a foreign collapse send traders "
            "into a selling frenzy. Prices plummet across the board as fear "
            "grips every merchant's heart."
        ),
        category="economic",
    ),

    WorldEvent(
        name="Gold Rush",
        description=(
            "A prospector strikes a rich vein near the old mine shaft. Word "
            "spreads fast and fortune-seekers flood into town, spending freely "
            "but driving up the cost of everything."
        ),
        probability=0.02,
        duration=3,
        effects={
            "production_modifier": {
                "gold": 3.0,
                "iron": 1.5,     # miners find iron too
                "stone": 1.5,
            },
            "price_modifier": {
                "gold": 0.6,     # supply glut
                "tools": 1.8,    # miners need tools
                "ale": 1.4,      # miners drink
                "bread": 1.3,    # more mouths to feed
                "meat": 1.3,
            },
            "income_modifier": 1.2,     # boom economy
            "specific_locations": ["bank", "workshop"],
        },
        announcement=(
            "GOLD! A massive vein has been discovered! Fortune-seekers pour "
            "into town with wild eyes and empty pockets. Tool prices soar, "
            "taverns overflow, and the bank can barely keep up."
        ),
        category="economic",
    ),

    WorldEvent(
        name="Trade Embargo",
        description=(
            "The neighboring kingdom imposes a trade embargo over a diplomatic "
            "dispute. Imported goods vanish from shelves and local producers "
            "struggle to fill the gap."
        ),
        probability=0.015,
        duration=4,
        effects={
            "production_modifier": {
                "spices": 0.1,
                "cloth": 0.5,
                "medicine": 0.3,
            },
            "price_modifier": {
                "spices": 2.5,
                "cloth": 1.8,
                "medicine": 2.0,
                "fish": 1.3,     # local food demand rises
                "wheat": 1.2,
                "leather": 1.4,  # domestic substitute for cloth
            },
            "income_modifier": 0.85,
            "specific_locations": ["dock", "market"],
        },
        announcement=(
            "The Kingdom of Aldenmere has declared a TRADE EMBARGO! No "
            "foreign goods shall pass the border. Spice traders weep, "
            "medicine runs scarce, and the people brace for hardship."
        ),
        category="economic",
    ),

    # ===================================================================
    # SOCIAL (4 events)
    # ===================================================================
    WorldEvent(
        name="Grand Festival",
        description=(
            "The annual Festival of the Twin Moons draws revelers from across "
            "the region. Ale flows freely, merchants hawk their finest wares, "
            "and everyone opens their purse strings a little wider."
        ),
        probability=0.03,
        duration=2,
        effects={
            "production_modifier": {
                "ale": 1.8,
                "bread": 1.5,
                "meat": 1.3,
            },
            "price_modifier": {
                "ale": 1.5,
                "bread": 1.3,
                "cloth": 1.4,    # festival clothes
                "spices": 1.3,   # feasting demand
                "gold": 1.2,     # gift-giving
            },
            "income_modifier": 1.25,    # celebration spending
            "specific_locations": ["tavern", "town_center"],
        },
        announcement=(
            "Let the Festival of the Twin Moons BEGIN! Music fills the "
            "streets, torches light up the night, and coin changes hands "
            "faster than ale fills a mug. Every merchant's dream!"
        ),
        category="social",
    ),

    WorldEvent(
        name="Plague Outbreak",
        description=(
            "A mysterious sickness spreads from the docks. Workers fall ill, "
            "production grinds to a halt, and the desperate scramble for "
            "medicine sends its price through the roof."
        ),
        probability=0.01,
        duration=5,
        effects={
            "production_modifier": {
                "wheat": 0.6,
                "meat": 0.5,
                "fish": 0.5,
                "iron": 0.6,
                "tools": 0.6,
                "ale": 0.7,
                "cloth": 0.7,
                "bread": 0.6,
            },
            "price_modifier": {
                "medicine": 3.0,
                "ale": 1.3,      # liquid courage
                "bread": 1.4,    # staple hoarding
                "spices": 1.5,   # folk remedies
            },
            "income_modifier": 0.6,     # severe economic hit
        },
        announcement=(
            "THE PLAGUE HAS COME! A terrible sickness spreads through the "
            "town. Workers collapse at their stations, shops close their "
            "doors, and the price of medicine becomes worth more than gold. "
            "May the healers work swiftly."
        ),
        category="social",
    ),

    WorldEvent(
        name="Crime Wave",
        description=(
            "Emboldened thieves and bandits terrorize the trade routes. "
            "Merchants hire guards at great expense, the workshop is raided, "
            "and trust between traders plummets."
        ),
        probability=0.02,
        duration=3,
        effects={
            "production_modifier": {
                "gold": 0.5,     # vault robberies
                "cloth": 0.7,    # highway raids
                "spices": 0.6,   # caravan attacks
            },
            "price_modifier": {
                "tools": 1.4,    # weapons demand
                "iron": 1.3,     # locks and bars
                "leather": 1.3,  # armor
                "gold": 1.5,     # scarcity
            },
            "income_modifier": 0.8,     # protection costs
            "specific_locations": ["market", "bank"],
        },
        announcement=(
            "A CRIME WAVE sweeps through the town! Bandits raid caravans "
            "on the north road, thieves hit the market stalls by night, "
            "and the bank doubles its guards. Nobody's coin purse is safe."
        ),
        category="social",
    ),

    WorldEvent(
        name="Tournament of Champions",
        description=(
            "Knights and warriors gather for a grand tournament. The wealthy "
            "place bets, smiths work day and night forging weapons, and the "
            "tavern has never been so profitable."
        ),
        probability=0.015,
        duration=2,
        effects={
            "production_modifier": {
                "iron": 1.8,
                "tools": 1.6,    # weapons are tools of war
                "leather": 1.5,  # armor
            },
            "price_modifier": {
                "iron": 1.5,
                "tools": 1.6,
                "leather": 1.5,
                "ale": 1.4,      # spectators drink
                "meat": 1.3,     # feasting
            },
            "income_modifier": 1.15,    # gambling and spectacle economy
            "specific_locations": ["workshop", "tavern"],
        },
        announcement=(
            "HEAR YE! The Tournament of Champions begins! Knights in "
            "gleaming armor clash for glory and gold. The smiths' hammers "
            "ring all night, the taverns overflow, and fortunes are won "
            "and lost on every bout."
        ),
        category="social",
    ),

    # ===================================================================
    # RARE (2 events) — low probability, dramatic impact
    # ===================================================================
    WorldEvent(
        name="Earthquake",
        description=(
            "The ground shakes violently. Buildings crack, the mine collapses, "
            "and the workshop's great forge splits in two. Recovery will take "
            "a long time and cost dearly."
        ),
        probability=0.008,
        duration=6,
        effects={
            "production_modifier": {
                "iron": 0.2,
                "tools": 0.2,
                "gold": 0.1,
                "stone": 0.3,
                "wheat": 0.7,    # fields survive mostly
                "fish": 0.8,     # sea doesn't care
            },
            "price_modifier": {
                "stone": 3.0,    # rebuilding material
                "wood": 2.5,     # rebuilding material
                "iron": 2.0,     # repair tools
                "tools": 2.5,    # desperately needed
                "bread": 1.5,    # supply disruption
                "medicine": 1.8, # injuries
            },
            "income_modifier": 0.5,     # devastating economic blow
        },
        announcement=(
            "THE EARTH TREMBLES! A devastating earthquake rocks the town "
            "to its foundations! The workshop forge is destroyed, the mine "
            "has collapsed, and half the buildings in town show cracks. "
            "Stone and wood prices explode as the long road to recovery begins."
        ),
        category="rare",
    ),

    WorldEvent(
        name="Dragon Sighting",
        description=(
            "A massive winged shadow passes over the town at dawn. Livestock "
            "stampede, traders flee the roads, and the bravest warriors reach "
            "for their swords. But the dragon also dropped something glittering..."
        ),
        probability=0.003,
        duration=4,
        effects={
            "production_modifier": {
                "meat": 0.3,     # livestock scattered
                "wool": 0.3,     # sheep fled
                "wheat": 0.5,    # fields trampled in panic
                "gold": 2.0,     # dragon hoard fragments found
                "stone": 1.5,    # dragon-scorched rare minerals
            },
            "price_modifier": {
                "gold": 0.7,     # hoard fragments flood market
                "iron": 1.8,     # arm yourselves!
                "tools": 1.6,    # weapons needed
                "leather": 1.8,  # dragon-proof armor
                "meat": 2.0,     # livestock decimated
                "wool": 1.8,     # sheep are gone
                "ale": 1.5,      # liquid courage required
                "medicine": 1.4, # burn treatment
            },
            "income_modifier": 0.7,     # fear suppresses commerce
        },
        announcement=(
            "BY THE GODS — A DRAGON! A colossal wyrm darkens the skies "
            "over the town! Livestock scatter in terror, warriors draw steel, "
            "and the bravest (or most foolish) scramble to collect glittering "
            "fragments from the beast's hoard. The world will never be the same."
        ),
        category="rare",
    ),
]


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

EVENTS_BY_CATEGORY: dict[str, list[WorldEvent]] = {}
for _evt in WORLD_EVENTS:
    EVENTS_BY_CATEGORY.setdefault(_evt.category, []).append(_evt)

EVENTS_BY_NAME: dict[str, WorldEvent] = {e.name: e for e in WORLD_EVENTS}


# ---------------------------------------------------------------------------
# Event rolling logic
# ---------------------------------------------------------------------------

@dataclass
class ActiveEvent:
    """An event that is currently in effect."""

    event: WorldEvent
    started_tick: int
    expires_tick: int


class WorldEventEngine:
    """Manages rolling for new events and tracking active effects.

    Usage in the tick loop:
        engine = WorldEventEngine()
        new_events = engine.roll_events(current_tick)
        modifiers = engine.get_active_modifiers(current_tick)
    """

    def __init__(self) -> None:
        self.active_events: list[ActiveEvent] = []
        self.event_history: list[dict] = []

    def roll_events(self, tick: int) -> list[WorldEvent]:
        """Roll for each event independently. Returns list of newly fired events.

        Events of the same category cannot stack — if a weather event is already
        active, no new weather events will fire. This prevents absurd compound
        effects (e.g., drought + storm simultaneously).
        """
        # Determine which categories already have active events
        active_categories = {
            ae.event.category
            for ae in self.active_events
            if ae.expires_tick > tick
        }

        fired: list[WorldEvent] = []

        for event in WORLD_EVENTS:
            # Skip if this category is already active
            if event.category in active_categories:
                continue

            if random.random() < event.probability:
                active = ActiveEvent(
                    event=event,
                    started_tick=tick,
                    expires_tick=tick + event.duration,
                )
                self.active_events.append(active)
                active_categories.add(event.category)  # block same-category stacking
                fired.append(event)

                self.event_history.append({
                    "name": event.name,
                    "category": event.category,
                    "started_tick": tick,
                    "duration": event.duration,
                })

                logger.info(
                    "World event fired: [%s] %s (duration=%d ticks, expires tick %d)",
                    event.category.upper(),
                    event.name,
                    event.duration,
                    tick + event.duration,
                )

        return fired

    def get_active_modifiers(self, tick: int) -> dict[str, Any]:
        """Aggregate all active event modifiers into a single dict.

        Returns:
            {
                "production_modifier": {"wheat": 0.4, "iron": 1.5, ...},
                "price_modifier": {"gold": 0.8, ...},
                "income_modifier": 0.7,
                "active_events": ["Event Name", ...],
                "affected_locations": ["farm", "dock", ...],
            }

        When multiple events affect the same item, modifiers are multiplied
        together (e.g., 0.5 * 0.6 = 0.3 for compound scarcity).
        """
        # Prune expired events
        self.active_events = [
            ae for ae in self.active_events if ae.expires_tick > tick
        ]

        production: dict[str, float] = {}
        price: dict[str, float] = {}
        income: float = 1.0
        active_names: list[str] = []
        affected_locations: set[str] = set()

        for ae in self.active_events:
            effects = ae.event.effects
            active_names.append(ae.event.name)

            # Production modifiers — multiply
            for item, mod in effects.get("production_modifier", {}).items():
                production[item] = production.get(item, 1.0) * mod

            # Price modifiers — multiply
            for item, mod in effects.get("price_modifier", {}).items():
                price[item] = price.get(item, 1.0) * mod

            # Income modifier — multiply
            if "income_modifier" in effects:
                income *= effects["income_modifier"]

            # Affected locations
            for loc in effects.get("specific_locations", []):
                affected_locations.add(loc)

        return {
            "production_modifier": production,
            "price_modifier": price,
            "income_modifier": income,
            "active_events": active_names,
            "affected_locations": sorted(affected_locations),
        }

    def get_active_event_summaries(self, tick: int) -> list[dict]:
        """Return summaries of all currently active events for the dashboard."""
        return [
            {
                "name": ae.event.name,
                "category": ae.event.category,
                "description": ae.event.description,
                "announcement": ae.event.announcement,
                "remaining_ticks": ae.expires_tick - tick,
                "started_tick": ae.started_tick,
                "effects": ae.event.effects,
            }
            for ae in self.active_events
            if ae.expires_tick > tick
        ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
world_event_engine = WorldEventEngine()


# ---------------------------------------------------------------------------
# Export as raw dicts (for serialization, testing, seeding)
# ---------------------------------------------------------------------------

def events_as_dicts() -> list[dict]:
    """Export all world events as plain dictionaries."""
    return [
        {
            "name": e.name,
            "description": e.description,
            "probability": e.probability,
            "duration": e.duration,
            "effects": e.effects,
            "announcement": e.announcement,
            "category": e.category,
        }
        for e in WORLD_EVENTS
    ]
