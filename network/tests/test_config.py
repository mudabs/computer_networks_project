from config import Config

config = Config()
devices = config.data["devices"]
addresses = config.data["addresses"]
links = config.data["links"]

VALID_DEVICE_TYPES = {"host", "router"}

# Devices
for device_id, device_data in devices.items():
    assert "type" in device_data, f"Device '{device_id}' is missing a type"
    assert device_data["type"] in VALID_DEVICE_TYPES, (
        f"Device '{device_id}' has invalid type: " f"{device_data['type']}"
    )
    assert device_id in addresses, f"Device '{device_id}' has no configured address"


# Addresses
for device_id, address in addresses.items():
    assert device_id in devices, f"Address configured for unknown device '{device_id}'"
    assert "ip" in address, f"Address for '{device_id}' is missing an IP"
    assert "port" in address, f"Address for '{device_id}' is missing a port"


# Links
for link in links:
    assert isinstance(link, dict), "Each link must be an object"
    assert "a" in link and "b" in link, "Each link must have 'a' and 'b' endpoints"
    assert link["a"] in devices, f"Link references unknown device '{link['a']}'"
    assert link["b"] in devices, f"Link references unknown device '{link['b']}'"
    assert link["a"] != link["b"], f"Device '{link['a']}' cannot link to itself"

print("All configuration tests passed.")
