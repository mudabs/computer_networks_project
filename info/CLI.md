# Command-line use

How to run the Mini Internet from the terminal with `main.py`. For the visual demo see [GUI.md](GUI.md). Python 3 is required; no third-party packages are needed. Run commands from the repository root.

## Start the network with the CLI

`data/network.json` defines devices, UDP addresses, bidirectional topology links,
and optional directed link conditions. `data/routes.json` defines static routes.
All configured devices run on localhost. In the current configuration, the lower
path is selected: H1 -> A -> C -> D -> H2. To use the upper path, change A's
H2 route and D's H1 route to B, then restart the devices.

Use `python` on Windows if `python3` is not available. Do not run `main.py --all` while the GUI
is open: both use the configured UDP ports.

### Run all devices

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

`packet.py` validates UTF-8 JSON packets with src, dst, ttl (1–8), type
(DATA, RESPONSE, ACK, or FIN), id and seq (unsigned 32-bit), and a string payload
(empty for ACK). The serialized
packet limit is 1,200 bytes. Remaining extensions are outside these baseline communication features.

Reliable stop-and-wait transfer (Track B) uses `--mode reliable` on both hosts.
Start the receiver (`python3 main.py H2 --mode reliable`) and the routers, then run
`python3 main.py H1 --mode reliable --timeout 1 --transfer H2 --messages one two three`.
It prints `TRANSFER COMPLETE` or `TRANSFER FAILED` (exit code 1) once retries run out.
Reliable receivers ACK DATA/FIN instead of echoing; best-effort hosts behave as before.

**Receiver grace period.** After a reliable receiver accepts a valid FIN and sends its ACK, it keeps
serving for a bounded grace period. If the sender did not get the FIN ACK and retransmits the final
DATA or FIN, the receiver re-ACKs it. When the period expires, `Host.run()` returns without closing
the socket (the owner closes the host), so `main.py` exits with status 0. By default the period is
`timeout * (max_retries + 1)` of the receiver: `main.py` passes `--timeout` (default 3 s) and uses 3
retries, so 12 s unless you pass a different `--timeout`. A receiver that has not accepted a FIN, and
every best-effort host, keeps running until Ctrl+C. The packet format and the sender's COMPLETE or
FAILED behavior are unchanged.

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
