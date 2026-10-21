import json
import math
from pathlib import Path


class Config:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent
        data_dir = self.base_dir / "data"

        with open(data_dir / "network.json") as f:
            self.data = json.load(f)

        with open(data_dir / "routes.json") as f:
            self.route_data = json.load(f)

        self.validate_conditions()

    def validate_conditions(self):
        seen = set()
        for rule in self.data.get("conditions", []):
            source, destination = rule["from"], rule["to"]
            if destination not in self.get_neighbors(source):
                raise ValueError("Condition must target a configured directed link")
            if (source, destination) in seen:
                raise ValueError("Duplicate directed link condition")
            seen.add((source, destination))
            for key in ("loss", "delay_ms"):
                value = rule[key]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"{key} must be a finite number")
            if not 0 <= rule["loss"] <= 1 or rule["delay_ms"] < 0:
                raise ValueError("loss must be in [0, 1] and delay_ms nonnegative")
            if type(rule["seed"]) is not int:
                raise ValueError("seed must be an integer")

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
        return self.route_data[device_id]
