from dataclasses import dataclass
from typing import Optional, List

@dataclass
class Location:
    lat: float
    lon: float

@dataclass
class TimeWindow:
    start_min: int
    end_min: int

@dataclass
class Depot:
    loc: Location
    window: TimeWindow

@dataclass
class Vehicle:
    id: str
    capacity: int
    start_min: int
    end_min: int
    speed_factor: float = 1.0

@dataclass
class Stop:
    id: str
    loc: Location
    demand: int = 0
    service_min: int = 0
    window: Optional[TimeWindow] = None

@dataclass
class OptimizeRequest:
    depot: Depot
    vehicles: List[Vehicle]
    stops: List[Stop]
    objective: str = "min_cost"
    include_tolls: bool = True

def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(':')
    return int(h)*60 + int(m)

