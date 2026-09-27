import json
from pathlib import Path


class Config:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent

        network_path = self.base_dir / "data" / "network.json"

        with open(network_path) as f:
            self.data = json.load(f)

    def get_device(self, device_id):
        return self.data["devices"][device_id]

    def get_address(self, device_id):
        return self.data["addresses"][device_id]

    def get_neighbors(self, device_id):
        return [
            link["b"] if link["a"] == device_id else link["a"]
            for link in self.data["links"]
            if device_id in (link["a"], link["b"])
        ]

    def get_routes(self, device_id):
        routes_path = self.base_dir / "data" / "routes.json"

        with open(routes_path) as f:
            route_data = json.load(f)

        return route_data[device_id]
