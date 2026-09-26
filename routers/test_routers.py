# Plays both hosts: sends a message from H1 and waits for it at H2, then replies.
# Start RouterA.py to RouterD.py first.
import json
import socket

from packet import make_packet, parse_packet

config = json.load(open("config.json"))


def host_socket(name):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", config[name]["port"]))
    s.settimeout(2)
    return s


def router_of(host):
    return ("127.0.0.1", config[config[host]["router"]]["port"])


h1, h2 = host_socket("H1"), host_socket("H2")

h1.sendto(make_packet("H1", "H2", "PROBE", 1, "hello"), router_of("H1"))
try:
    pkt = parse_packet(h2.recvfrom(1201)[0])
    print("H2 got:", pkt)
    h2.sendto(make_packet("H2", "H1", "ECHO", pkt["id"], pkt["payload"]), router_of("H2"))
    print("H1 got:", parse_packet(h1.recvfrom(1201)[0]))
except socket.timeout:
    print("Timed out: no reply")
