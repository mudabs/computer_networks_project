# Mini Internet

CSCI 5500 Computer Networks, Fall 2026 - Group 1

This repository contains an early implementation of the Mini Internet project. The intended network has two hosts and four routers communicating over UDP on one computer. The repository currently contains configuration and packet-building code, an early host class, and an unfinished router/integration prototype.

## Current status

The codebase is not yet an end-to-end runnable network. The packet and configuration modules use the newer interfaces described below, while `network/router.py` and `network/tests/test_routers.py` still use an older packet/configuration interface. As a result, the documented router integration flow is currently blocked until those components are reconciled.

The configured topology is:

```text
              B
             / \
            /   \
H1 -------- A     D -------- H2
            \   /
             \ /
              C
```

The links are represented as bidirectional links in `network/data/network.json`.

## Repository layout

| Path | Purpose |
|---|---|
| `network/config.py` | Loads the current device, address, link, and route data. |
| `network/data/network.json` | Device types, UDP addresses, and physical/logical links. |
| `network/data/routes.json` | Static destination-to-next-hop route entries. |
| `network/packet.py` | Current `Packet` dataclass, validation, JSON serialization, and size checks. |
| `network/host.py` | Early `Host` class that binds a UDP socket and can send fragmented data packets. |
| `network/router.py` | Early router loop; currently uses missing legacy packet functions and the legacy config format. |
| `network/tests/test_config.py` | Executable configuration validation script. |
| `network/tests/test_host.py` | Exploratory host script; currently needs to be updated to the current `Host` API. |
| `network/tests/test_routers.py` | Legacy integration script; currently needs to be updated to the current packet and config APIs. |
| `network/info/PACKET.md` | Packet-format notes for the current packet interface. |
| `network/info/ROUTERS.md` | Older router startup notes that still need to be revised. |
| `network/config.json` | Legacy router prototype configuration. It is not the configuration source used by `network/config.py`. |
| `Mini_Internet_Project.md` | Project description, requirements, and evaluation guidance. |

## Requirements

- Python 3
- Local UDP ports `5001`, `5002`, and `6001` through `6004` available for the current configuration
- No third-party Python packages are required by the current source files

All configured addresses use `127.0.0.1`, so the network is intended to run on one computer once the router and host processes are integrated.

## Current configuration

`network/data/network.json` is the active topology configuration. It defines:

- Hosts `H1` and `H2`
- Routers `A`, `B`, `C`, and `D`
- UDP addresses for all six devices
- Links `H1-A`, `A-B`, `A-C`, `B-D`, `C-D`, and `D-H2`

`network/data/routes.json` is the active static route data. Its default upper path is:

```text
H1 -> A -> B -> D -> H2
H2 -> D -> B -> A -> H1
```

The lower path through `C` is present in the topology but is not the default route. The intended lower-path route change is to set `A`'s `H2` next hop to `C` and `D`'s `H1` next hop to `C` in `network/data/routes.json`. The router process does not yet consume this file, so changing it alone does not currently produce a working end-to-end run.

## Current packet format

`network/packet.py` represents packets with six fields:

| Field | Meaning |
|---|---|
| `src` | Original virtual source, such as `H1` |
| `dst` | Final virtual destination, such as `H2` |
| `hop_limit` | Forwarding hop limit, from `1` through `8` |
| `type` | Currently `DATA` or `RESPONSE` |
| `id` | Unsigned 32-bit message identifier |
| `payload` | String application data |

Packets are serialized as UTF-8 JSON. The complete serialized packet must be no larger than 1,200 bytes. Use `Packet.to_bytes()` to serialize and `Packet.from_bytes()` to parse and validate.

The older `ttl`, `PROBE`, `ECHO`, `make_packet`, and `parse_packet` interface is still referenced by the unfinished router prototype and legacy integration script, but it is not part of the current `Packet` API.

## Checks that currently run

From the repository root:

```bash
python -m compileall -q network
```

The following command runs the current configuration checks. The explicit path insertion is required because the repository does not yet define an installable Python package or test runner configuration:

```bash
python -c "import sys; sys.path.insert(0, 'network'); exec(open('network/tests/test_config.py', encoding='utf-8').read())"
```

A basic packet serialization check is:

```bash
python -c "import sys; sys.path.insert(0, 'network'); from packet import Packet; print(Packet('H1', 'H2', 8, 'DATA', 0, 'hello').to_bytes().decode())"
```

These checks verify syntax, configuration structure, and packet serialization only. They do not prove that packets can traverse the router topology.

## Known blockers

The end-to-end network still requires:

1. Updating `network/router.py` to use `Packet.from_bytes()` and `Packet.to_bytes()`, `hop_limit`, and the current configuration loader.
2. Updating or replacing `network/tests/test_routers.py` so it uses the current packet and route APIs.
3. Adding runnable router entry points or a command-line router configuration instead of relying on the missing `RouterA.py` through `RouterD.py` files.
4. Completing host receive/delivery behavior and correcting `network/tests/test_host.py` to use `Host.neighbor`.
5. Enforcing configured neighbors and validating route entries before forwarding.
6. Adding a real end-to-end test for both directions and both static paths.
7. Adding the required hop-limit, error, loss, delay, logging, repeated-baseline, and Wireshark demonstrations from the project requirements.

Until these items are addressed, this repository should be treated as an implementation checkpoint rather than a runnable mini-internet demonstration.

