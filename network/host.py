from config import Config


class Host:
    def __init__(self, host_id, config):
        if config.get_device(host_id)["type"] != "host":
            raise ValueError(f"'{host_id}' is not a host")

        self.id = host_id
        self.address = config.get_address(self.id)
        self.neighbors = config.get_neighbors(self.id)
