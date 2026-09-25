import json

MAX_SIZE = 1200


def make_packet(src, dst, msg_type, msg_id, payload, ttl=8):
    pkt = {"src": src, "dst": dst, "type": msg_type, "id": msg_id, "ttl": ttl, "payload": payload}
    return json.dumps(pkt).encode()


def parse_packet(data):
    """Returns the packet as a dict, or None if it is invalid."""
    if len(data) > MAX_SIZE:
        return None
    try:
        pkt = json.loads(data)
        pkt["src"], pkt["dst"], pkt["type"], pkt["id"], pkt["ttl"], pkt["payload"]
        return pkt
    except (ValueError, KeyError, TypeError):
        return None
