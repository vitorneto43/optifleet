from dataclasses import dataclass

@dataclass
class RoutingPolicy:
    allow_trucks_on_restricted: bool = False
    avoid_zones: list[str] = None          # IDs de zonas proibidas
    include_tolls: bool = True
    objective: str = "min_cost"            # min_time|min_distance|min_cost
