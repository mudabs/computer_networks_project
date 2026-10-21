import unittest
from unittest.mock import MagicMock, patch

from config import Config
from host import Host
from packet import Packet


class TestHostInitialization(unittest.TestCase):
    def setUp(self):
        self.config = Config()

    @patch("host.socket.socket")
    def test_host_initializes_correctly(self, mock_socket):
        mock_sock = mock_socket.return_value

        host = Host("H1", self.config)
        self.addCleanup(host.close)

        self.assertEqual(host.id, "H1")
        self.assertIs(host.config, self.config)
        self.assertEqual(host.address, self.config.get_address("H1"))
        self.assertEqual(host.neighbor, "A")
        self.assertEqual(host.packet_id, 0)

        mock_socket.assert_called_once()
        mock_sock.bind.assert_called_once_with(("127.0.0.1", 5001))
        mock_sock.close.assert_not_called()

    @patch("host.socket.socket")
    def test_router_cannot_be_initialized_as_host(self, mock_socket):
        with self.assertRaisesRegex(ValueError, "is not a host"):
            Host("A", self.config)

        mock_socket.assert_not_called()

    @patch("host.socket.socket")
    def test_host_without_neighbors_raises(self, mock_socket):
        invalid_config = MagicMock()
        invalid_config.get_device.return_value = {"type": "host"}
        invalid_config.get_address.return_value = {
            "ip": "127.0.0.1",
            "port": 5001,
        }
        invalid_config.get_neighbors.return_value = []

        with self.assertRaisesRegex(ValueError, "exactly one neighbor"):
            Host("H1", invalid_config)

        mock_socket.assert_not_called()

    @patch("host.socket.socket")
    def test_host_with_multiple_neighbors_raises(self, mock_socket):
        invalid_config = MagicMock()
        invalid_config.get_device.return_value = {"type": "host"}
        invalid_config.get_address.return_value = {
            "ip": "127.0.0.1",
            "port": 5001,
        }
        invalid_config.get_neighbors.return_value = ["A", "B"]

        with self.assertRaisesRegex(ValueError, "exactly one neighbor"):
            Host("H1", invalid_config)

        mock_socket.assert_not_called()

    @patch("host.socket.socket")
    def test_socket_is_closed_if_bind_fails(self, mock_socket):
        mock_sock = mock_socket.return_value
        mock_sock.bind.side_effect = OSError("Address already in use")

        with self.assertRaisesRegex(OSError, "Address already in use"):
            Host("H1", self.config)

        mock_sock.close.assert_called_once()


class TestHostCommunication(unittest.TestCase):
    def setUp(self):
        self.config = Config()

        patcher = patch("host.socket.socket")
        self.mock_socket = patcher.start()
        self.addCleanup(patcher.stop)

        self.mock_sock = self.mock_socket.return_value
        self.host = Host("H1", self.config)
        self.addCleanup(self.host.close)

    def test_send_sends_packet_to_immediate_neighbor(self):
        self.host.send("hello", "H2")

        self.mock_sock.sendto.assert_called_once()
        packet_bytes, address = self.mock_sock.sendto.call_args.args
        packet = Packet.from_bytes(packet_bytes)

        self.assertEqual(address, ("127.0.0.1", 6001))
        self.assertEqual(packet.src, "H1")
        self.assertEqual(packet.dst, "H2")
        self.assertEqual(packet.payload, "hello")
        self.assertEqual(packet.type, "DATA")
        self.assertEqual(packet.id, 0)
        self.assertEqual(packet.hop_limit, 8)
        self.assertEqual(self.host.packet_id, 1)

    def test_send_increments_id_for_each_message(self):
        self.host.send("first", "H2")
        self.host.send("second", "H2")

        self.assertEqual(self.host.packet_id, 2)
        self.assertEqual(self.mock_sock.sendto.call_count, 2)

        first_packet = Packet.from_bytes(
            self.mock_sock.sendto.call_args_list[0].args[0]
        )
        second_packet = Packet.from_bytes(
            self.mock_sock.sendto.call_args_list[1].args[0]
        )

        self.assertEqual(first_packet.id, 0)
        self.assertEqual(second_packet.id, 1)

    def test_receive_returns_packet_addressed_to_host(self):
        packet = Packet("H2", "H1", 8, "RESPONSE", 3, "hello")
        self.mock_sock.recvfrom.return_value = (
            packet.to_bytes(),
            ("127.0.0.1", 6001),
        )

        received = self.host.receive()

        self.assertIsInstance(received, Packet)
        self.assertEqual(received.src, "H2")
        self.assertEqual(received.dst, "H1")
        self.assertEqual(received.payload, "hello")
        self.assertEqual(received.type, "RESPONSE")
        self.assertEqual(received.id, 3)

    def test_receive_ignores_packet_for_another_host(self):
        packet = Packet("H2", "H3", 8, "RESPONSE", 3, "hello")
        self.mock_sock.recvfrom.return_value = (
            packet.to_bytes(),
            ("127.0.0.1", 6001),
        )

        self.assertIsNone(self.host.receive())

    def test_receive_ignores_invalid_packet(self):
        self.mock_sock.recvfrom.return_value = (
            b"not valid json",
            ("127.0.0.1", 6001),
        )

        self.assertIsNone(self.host.receive())


if __name__ == "__main__":
    unittest.main()
    