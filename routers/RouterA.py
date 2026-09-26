import json
import socket

from packet import MAX_SIZE, parse_packet

NAME = "A"

config = json.load(open("config.json"))
table = config[NAME]["table"]   # destination -> next hop


def address_of(device):
    return ("127.0.0.1", config[device]["port"])


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(address_of(NAME))
print(f"Router {NAME} running on port {config[NAME]['port']}")

while True:
    try:
        data, sender = sock.recvfrom(MAX_SIZE + 1)
    except ConnectionResetError:   # Windows: a neighbor is not running yet
        continue

    pkt = parse_packet(data)
    if pkt is None:
        print(f"[{NAME}] DROP invalid packet")
        continue

    print(f"[{NAME}] RECV id={pkt['id']} {pkt['src']}->{pkt['dst']} ttl={pkt['ttl']}")

    if pkt["ttl"] <= 1:
        print(f"[{NAME}] DROP id={pkt['id']} ttl expired")
        continue

    next_hop = table.get(pkt["dst"])
    if next_hop is None:
        print(f"[{NAME}] DROP id={pkt['id']} unknown destination {pkt['dst']}")
        continue

    pkt["ttl"] -= 1
    sock.sendto(json.dumps(pkt).encode(), address_of(next_hop))
    print(f"[{NAME}] FORWARD id={pkt['id']} to {next_hop} ttl={pkt['ttl']}")
