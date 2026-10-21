# Mini Internet

CSCI 5500 Computer Networks, Fall 2026 — Group 1.
Python 3 is required; no third-party packages are needed.
Run commands from the repository root.

## Start the network

`data/network.json` defines devices, UDP addresses, bidirectional topology links,
and optional directed link conditions. `data/routes.json` defines static routes.
All configured devices run on localhost. The default path is H1–A–B–D–H2;
C supplies an alternate path. To use C, change A's H2 route and D's H1 route to C.
Restart devices after changing configuration.

```bash
python3 main.py --all --run-id baseline
```

This starts every configured device as an echo server. Ctrl+C stops the network.
For probes, start individual devices instead, because a probe needs to bind its
source host's port. In separate terminals start:

```bash
python3 main.py A --run-id baseline
python3 main.py B --run-id baseline
python3 main.py C --run-id baseline
python3 main.py D --run-id baseline
python3 main.py H2 --run-id baseline
```

## Basic host communication (3.4)

With the routers and H2 running, send one probe from H1:

```bash
python3 main.py H1 --probe H2 --message "hello from H1" --timeout 3 --run-id baseline
```

The result is MATCHED only when the RESPONSE matches the source, destination,
message ID and payload. A finite timeout prints TIMEOUT and returns exit status 1.
Each probe is transmitted once, with no retries. Echoes are separate RESPONSE
packets routed back through the network.

To initiate an exchange from H2, stop the H2 server, start the H1 server, and probe:

```bash
python3 main.py H1 --run-id reverse
# In another terminal:
python3 main.py H2 --probe H1 --message "hello from H2" --timeout 3 --run-id reverse
```

Do not run a server and probe process for the same host simultaneously.

## Network conditions and logs (3.5)

The default `conditions: []` in `data/network.json` means no injected loss or delay.
For example, replace that array with:

```json
"conditions": [
  {"from": "A", "to": "B", "loss": 0.25, "delay_ms": 100, "seed": 42}
]
```

This applies a 25% drop probability and adds 100 ms delivery delay to surviving
packets on **A→B only**. The reverse B→A link is unaffected. To condition both
directions, add a second rule with `from: B` and `to: A`. Rules must identify
configured neighbors; loss is in [0, 1], delay is nonnegative milliseconds, and
seed is an integer. Each directed link has its own seeded random sequence.
The same rules apply to DATA and RESPONSE packets, including links sent by hosts.
Repeated seeds reproduce decisions for the same ordered traffic on each link.

Loss is decided once per outgoing packet before delay. A worker schedules delayed
packets using monotonic deadlines and a priority queue, without sleeping in the
host/router receive loop. Other outgoing links remain usable. Shutdown cancels
pending deliveries and logs the drops. This implements added delay, not a fixed-rate
congestion model. A reply that arrives after the probe deadline still counts as a
missing reply for that probe.

Use the same `--run-id` in every terminal. INFO logs include timestamps, device,
message ID/type, virtual source/destination, next hop and hop limit. Router receive,
forward, send and drop events let you follow DATA H1→A→B→D→H2 and RESPONSE
H2→D→B→A→H1. `send` means the UDP datagram was actually sent. Condition events record link,
seed, loss and delay; drops distinguish injected loss and forwarding errors.
Fields unavailable for malformed packets are `-`.

Capture a device's output with `2>&1 | tee device.log`, or collect all six server
logs from `--all` in one terminal. `info/baseline_trace.log` contains a real isolated
UDP request/response trace with timestamps and run ID `trace`, using ephemeral ports.

## Verification

```bash
python3 -m unittest discover -s tests
```

Tests cover both initiating hosts, the alternate path, matching payloads, timeouts,
request/response loss, seeded repeatability, invalid rules, nonblocking delays,
and a complete router trace. Integration tests use ephemeral localhost ports,
so they do not conflict with running devices.

`packet.py` validates UTF-8 JSON packets with src, dst, hop_limit (1–8), type
(DATA or RESPONSE), id (unsigned 32-bit), and a string payload. The serialized
packet limit is 1,200 bytes. See `Mini_Internet_Project.md` for the full assignment;
remaining extensions are outside these baseline communication features.
