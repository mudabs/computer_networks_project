# Mini Internet

CSCI 5500 Computer Networks, Fall 2026 · Group 1

This repository is a work in progress for the Mini Internet project. The goal is to run two hosts and four routers as separate Python processes communicating over UDP. The routers forward packets according to manually configured tables. The team will build the common network first, then implement and evaluate its chosen extension track.

## Current status

The present files form an **early router prototype**. `test_routers.py` temporarily simulates both hosts so the routers can be exercised before the team's host programs are integrated. The current configuration selects the upper path. A successful single-probe test is a development check, not evidence that all project requirements have been met.

```text
              Router B
             /        \
Host 1 -- Router A      Router D -- Host 2
             \        /
              Router C
```

The topology links are bidirectional. The configured upper route is `H1 → A → B → D → H2`; the response travels `H2 → D → B → A → H1`. The lower route through C also needs to be demonstrated by changing the static tables between runs.

## Repository files

| File | Current purpose |
|---|---|
| `RouterA.py`–`RouterD.py` | Router processes; the four files currently differ only in their `NAME` value. |
| `config.json` | UDP ports, each host's adjacent router, and static forwarding tables. |
| `packet.py` | Current JSON packet creation and parsing. Its format is provisional until the team agrees on the shared interface. |
| `test_routers.py` | Temporary integration check that simulates H1 and H2 and sends one probe and echo. |
| `ROUTERS.md` | Router startup notes. |
| `LICENSE` | Repository license; the team should confirm its attribution and publication choice. |

## Requirements

- Python 3
- Local UDP ports `5001`, `5002`, and `6001`–`6004` available for the current configuration
- No third-party Python packages are used by these prototype files

All processes can run on one computer using `127.0.0.1`. The current port assignments are examples and may change when the team finalizes configuration.

## Run the current development check

From the repository directory, start each router in its own terminal:

```bash
python RouterA.py
python RouterB.py
python RouterC.py
python RouterD.py
```

Then, in a fifth terminal, run:

```bash
python test_routers.py
```

The test sends one probe from its simulated H1 to H2 and sends an echo back. Inspect the router terminals for the message ID and forwarding events along the upper route. The test has a two-second socket timeout; a timeout means the exchange did not complete and requires investigation. Stop each router with Ctrl+C.

**Temporary test:** `test_routers.py` binds both host ports. Do not run it at the same time as separate host processes using those same ports.

## Current packet agreement

`packet.py` currently encodes a packet as UTF-8 JSON with these keys:

| Key | Meaning |
|---|---|
| `src` | Original virtual source, such as `H1` |
| `dst` | Final virtual destination, such as `H2` |
| `type` | Message type, currently `PROBE` or `ECHO` in the test |
| `id` | Message identifier used to match the probe and echo |
| `ttl` | Hop limit; a router drops an incoming packet when this is at most 1 |
| `payload` | Message content |

The serialized UDP payload limit is 1,200 bytes. **These field names and encoding are provisional.** The host and router developers should agree on one shared format before changing it. When the format changes, both sides and the integration test must change together.

## Configuration and routes

The current `config.json` maps each device name to a UDP port. H1's adjacent router is A; H2's is D. Each router's `table` maps a **final virtual destination** to a **next-hop neighbor**. The upper-path configuration has A send packets for H2 to B and D send packets for H1 to B.

For a lower-path run, the current notes call for changing A's `H2` entry to `C` and D's `H1` entry to `C`. Record which configuration was used for each demonstration. Routes are static; the common network does not automatically switch paths after a failure.

## Work remaining

The current prototype does not yet demonstrate the full common-network requirements. In particular, the team still needs to integrate the real host programs; define and enforce configured neighbors; validate configuration and packets more thoroughly; demonstrate both directions and both static paths; provide hop-limit, error, loss, and delay scenarios; add traceable logs; run repeated baseline experiments; and collect Wireshark evidence. The chosen extension track comes after a working core checkpoint.

Keep the exact setup, test commands, results, and contribution details up to date as the team implements these parts. The project handout and Canvas announcements govern submission requirements and deadlines.

