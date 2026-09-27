from dataclasses import dataclass, asdict
import json

VALID_TYPES = {"DATA", "RESPONSE"}

class PacketError(Exception):
    pass


@dataclass
class Packet:
    src: str
    dst: str
    hop_limit: int
    type: str
    id: int
    payload: str

    def validate(self):
        # Type check
        if not isinstance(self.src, str):
            raise PacketError("src must be a string")
        if not isinstance(self.dst, str):
            raise PacketError("dst must be a string")
        if type(self.hop_limit) is not int:
            raise PacketError("hop_limit must be an int")
        if not isinstance(self.type, str):
            raise PacketError("type must be a str")
        if type(self.id) is not int:
            raise PacketError("id must be an int")
        if not isinstance(self.payload, str):
            raise PacketError("payload must be a str")
        if self.type not in VALID_TYPES:
            raise PacketError("Invalid type")

        # Range check
        if not 1 <= self.hop_limit <= 8:
            raise PacketError("hop_limit must be between 1-8")
        if not 0 <= self.id <= 4294967295:
            raise PacketError("Invalid id")

        # Empty check
        if not self.src:
            raise PacketError("src cannot be empty")
        if not self.dst:
            raise PacketError("dst cannot be empty")

    def to_bytes(self):
        self.validate()
        packet_dict = asdict(self)
        json_string = json.dumps(packet_dict)
        json_bytes = json_string.encode("utf-8")

        if len(json_bytes) > 1200:
            raise PacketError("Packet size exceeded")

        return json_bytes

    @classmethod
    def from_bytes(cls, json_bytes):
        if len(json_bytes) > 1200:
            raise PacketError("Packet size exceeded")
        try:
            json_string = json_bytes.decode("utf-8")
            packet_dict = json.loads(json_string)
            packet = cls(**packet_dict)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            # Add from None to ignore original exception message
            raise PacketError("Invalid packet data")  # from None
        packet.validate()
        return packet