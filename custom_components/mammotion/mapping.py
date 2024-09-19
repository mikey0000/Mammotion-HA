"""Mapping and zone management for the Mammotion integration."""

from typing import Any, Dict, List

class Zone:
    def __init__(self, zone_id: str, name: str, coordinates: List[Dict[str, Any]]):
        self.zone_id = zone_id
        self.name = name
        self.coordinates = coordinates

class MappingManager:
    def __init__(self):
        self.zones: Dict[str, Zone] = {}

    def create_zone(self, zone_id: str, name: str, coordinates: List[Dict[str, Any]]):
        if zone_id in self.zones:
            raise ValueError(f"Zone with ID {zone_id} already exists.")
        self.zones[zone_id] = Zone(zone_id, name, coordinates)

    def update_zone(self, zone_id: str, name: str = None, coordinates: List[Dict[str, Any]] = None):
        if zone_id not in self.zones:
            raise ValueError(f"Zone with ID {zone_id} does not exist.")
        if name:
            self.zones[zone_id].name = name
        if coordinates:
            self.zones[zone_id].coordinates = coordinates

    def delete_zone(self, zone_id: str):
        if zone_id not in self.zones:
            raise ValueError(f"Zone with ID {zone_id} does not exist.")
        del self.zones[zone_id]

    def get_zone(self, zone_id: str) -> Zone:
        if zone_id not in self.zones:
            raise ValueError(f"Zone with ID {zone_id} does not exist.")
        return self.zones[zone_id]

    def list_zones(self) -> List[Zone]:
        return list(self.zones.values())
