from dataclasses import dataclass, asdict
import json

VALID_PACKET_TYPES = {"DATA", "RESPONSE", "ACK", "FIN"}
MAX_SIZE = 1200
MAX_ID = 4294967295


class PacketError(Exception):
    pass


class PacketSizeError(PacketError):
    pass


@dataclass
class Packet:
    src: str
    dst: str
    ttl: int
    type: str
    id: int
    seq: int
    payload: str

    def validate(self):
        # Validate field types
        if not isinstance(self.src, str):
            raise PacketError("src must be a string")
        if not isinstance(self.dst, str):
            raise PacketError("dst must be a string")
        if type(self.ttl) is not int:
            raise PacketError("ttl must be an int")
        if not isinstance(self.type, str):
            raise PacketError("type must be a string")
        if type(self.id) is not int:
            raise PacketError("id must be an int")
        if type(self.seq) is not int:
            raise PacketError("seq must be an int")
        if not isinstance(self.payload, str):
            raise PacketError("payload must be a string")

        # Validate field values
        if self.type not in VALID_PACKET_TYPES:
            raise PacketError("Invalid type")
        if not 1 <= self.ttl <= 8:
            raise PacketError("ttl must be between 1-8")
        if not 0 <= self.id <= MAX_ID:
            raise PacketError("Invalid id")
        if not 0 <= self.seq <= MAX_ID:
            raise PacketError("Invalid seq")
        if not self.src.strip():
            raise PacketError("src cannot be empty")
        if not self.dst.strip():
            raise PacketError("dst cannot be empty")

        # ACK packets do not carry application data
        if self.type == "ACK" and self.payload != "":
            raise PacketError("ACK payload must be empty")

    def to_bytes(self):
        self.validate()

        json_string = json.dumps(asdict(self))
        json_bytes = json_string.encode("utf-8")

        if len(json_bytes) > MAX_SIZE:
            raise PacketSizeError("Packet size exceeded")

        return json_bytes

    @classmethod
    def from_bytes(cls, json_bytes):
        if not isinstance(json_bytes, bytes):
            raise PacketError("Packet data must be bytes")

        if len(json_bytes) > MAX_SIZE:
            raise PacketSizeError("Packet size exceeded")

        try:
            json_string = json_bytes.decode("utf-8")
            packet_dict = json.loads(json_string)

            if not isinstance(packet_dict, dict):
                raise PacketError("Packet data must be a JSON object")

            # Reject missing or unexpected fields
            expected_fields = {
                "src",
                "dst",
                "ttl",
                "type",
                "id",
                "seq",
                "payload",
            }

            if set(packet_dict.keys()) != expected_fields:
                raise PacketError("Missing or unexpected packet fields")

            packet = cls(**packet_dict)

        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise PacketError("Invalid packet data") from None

        packet.validate()
        return packet
