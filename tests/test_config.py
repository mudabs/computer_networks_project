import unittest

from config import Config


class TestConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config()
        cls.devices = cls.config.data["devices"]
        cls.addresses = cls.config.data["addresses"]
        cls.links = cls.config.data["links"]
        cls.routes = cls.config.route_data

    def test_devices_have_valid_types(self):
        valid_types = {"host", "router"}

        for device_id, device in self.devices.items():
            with self.subTest(device=device_id):
                self.assertIsInstance(device, dict)
                self.assertIn(device.get("type"), valid_types)

    def test_every_device_has_an_address(self):
        for device_id in self.devices:
            with self.subTest(device=device_id):
                self.assertIn(device_id, self.addresses)

    def test_addresses_reference_known_devices(self):
        for device_id in self.addresses:
            with self.subTest(device=device_id):
                self.assertIn(device_id, self.devices)

    def test_addresses_have_valid_ip_and_port(self):
        for device_id, address in self.addresses.items():
            with self.subTest(device=device_id):
                self.assertIsInstance(address, dict)

                ip = address.get("ip")
                port = address.get("port")

                self.assertIsInstance(ip, str)
                self.assertTrue(ip.strip())
                self.assertIs(type(port), int)
                self.assertGreaterEqual(port, 1)
                self.assertLessEqual(port, 65535)

    def test_links_have_valid_endpoints(self):
        for link in self.links:
            with self.subTest(link=link):
                self.assertIsInstance(link, dict)
                self.assertIn("a", link)
                self.assertIn("b", link)

                a = link["a"]
                b = link["b"]

                self.assertIn(a, self.devices)
                self.assertIn(b, self.devices)
                self.assertNotEqual(a, b)

    def test_no_duplicate_links(self):
        seen_links = set()

        for link in self.links:
            link_key = frozenset((link["a"], link["b"]))

            with self.subTest(link=link):
                self.assertNotIn(link_key, seen_links)
                seen_links.add(link_key)

    def test_each_host_has_exactly_one_neighbor(self):
        for device_id, device in self.devices.items():
            if device["type"] != "host":
                continue

            with self.subTest(host=device_id):
                self.assertEqual(
                    len(self.config.get_neighbors(device_id)),
                    1,
                )

    def test_every_router_has_a_forwarding_table(self):
        for device_id, device in self.devices.items():
            if device["type"] != "router":
                continue

            with self.subTest(router=device_id):
                self.assertIn(device_id, self.routes)
                self.assertIsInstance(self.routes[device_id], dict)

    def test_routes_have_valid_destinations_and_next_hops(self):
        for router_id, table in self.routes.items():
            with self.subTest(router=router_id):
                self.assertIn(router_id, self.devices)
                self.assertEqual(
                    self.devices[router_id]["type"],
                    "router",
                )

                neighbors = self.config.get_neighbors(router_id)

                for destination, next_hop in table.items():
                    with self.subTest(
                        router=router_id,
                        destination=destination,
                    ):
                        self.assertIn(destination, self.devices)
                        self.assertIn(next_hop, self.devices)
                        self.assertIn(next_hop, neighbors)


if __name__ == "__main__":
    unittest.main()
