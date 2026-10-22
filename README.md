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
```
```bash
python3 main.py B --run-id baseline
```
```bash
python3 main.py C --run-id baseline
```
```bash
python3 main.py D --run-id baseline
```
```bash
python3 main.py H2 --run-id baseline
```
```bash
python3 main.py H1 --run-id baseline
```

## Basic host communication (3.4)

With routers running, open each host in its own terminal:

```bash
python3 main.py H1 --run-id baseline
```
```bash
python3 main.py H2 --run-id baseline
```

Each host listens and echoes by default. Type a message and press Enter to send
one probe to the other host. A matching response prints MATCHED; no reply within
3 seconds prints TIMEOUT. Both hosts stay running and can send again without
restarting. Incoming probes are echoed even while waiting for your own replies.
Terminal input, packets and deadlines share a single `select` event loop on macOS;
no additional host thread is used. The existing link-delay worker is unchanged.

The fixed timeout is `PROBE_TIMEOUT` in `main.py`. Responses must match the
source, destination, ID and payload. There are no retries. Ctrl+C stops a host.
An oversized message is rejected without stopping the host.

The optional one-shot mode still prompts for a message and exits after its result:

```bash
python3 main.py H1 --probe H2 --run-id baseline
```

`--all` starts echo-only background devices, with terminal input disabled to avoid
multiple hosts reading the same terminal. Use separate terminals for interactive
hosts, and do not start two processes for the same device.

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

For a forced-drop demonstration on the lower path, set `conditions` to
`[{"from":"A","to":"C","loss":1.0,"delay_ms":0,"seed":42}]`.
Restart A, send one H1 probe, and observe A's `reason=injected_loss` and H1's
TIMEOUT. This drops every packet on A→C while enabled; sending just one probe
demonstrates one forced drop. Restore `conditions: []` and restart A afterward.
Use B instead of C for the upper path. For random loss, use `loss: 0.05`.

To demonstrate a late reply, use
`[{"from":"H2","to":"D","loss":0.0,"delay_ms":4000,"seed":42}]`,
restart H2, and send from the interactive H1 terminal. H1 reports TIMEOUT after
3 seconds, then logs `event=late_response` and prints LATE when the matching reply
arrives. It still counts as a timeout. Keep H1 open: one-shot `--probe` exits at
its timeout and cannot observe later replies. Restore the conditions afterward.
