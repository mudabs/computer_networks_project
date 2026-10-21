
# Host Implementation — Progress Report Notes

## 1. Scope and Responsibilities

The Host component implements end-host communication for the mini-internet project. Hosts communicate through the configured router network using UDP.

The Host component is responsible for:

- Initializing a host using the shared `Config` object.
- Binding a UDP socket to the host's configured IP address and port.
- Sending packets to its immediate neighbor rather than directly to the final destination.
- Receiving and validating packets addressed to the host.
- Implementing a probe/echo exchange with a finite response timeout.
- Logging host communication events and errors.

Router forwarding, static routing-table implementation, and router-specific logging are outside the Host component's responsibility.

## 2. Current Architecture

The Host uses the following components:

- **`Config`:** Provides device information, virtual addresses, UDP endpoints, and neighbor relationships.
- **`Packet`:** Represents network messages and provides packet serialization and parsing.
- **UDP socket:** Sends and receives datagrams between a host and its configured immediate neighbor.
- **Python logging:** Records host communication events and errors.

### Initialization

The constructor accepts a host identifier and a `Config` instance. It verifies that the specified device is a host and has exactly one neighbor. It then binds a UDP socket to the configured endpoint.

If binding fails, the socket is closed and the error is propagated.

### Sending

The public interface is:

```python
host.send(data, dst)
```

The caller supplies the final virtual destination. The Host creates a `DATA` packet containing the virtual source and destination, hop limit, message type, message identifier, and application payload.

The packet is serialized and sent to the configured neighbor's UDP endpoint. The message identifier increments after a successful send and wraps around at the unsigned 32-bit boundary.

Each application message must fit in one packet. If the serialized packet exceeds the 1,200-byte limit, the packet serialization layer rejects it. The Host does not implement fragmentation or reassembly because these features are not required for the common core.

### Receiving

The Host receives UDP datagrams and parses them using `Packet.from_bytes()`.

Malformed packets, oversized datagrams, and packets addressed to another host are rejected without crashing the process. Valid packets addressed to the host are returned as `Packet` objects so the caller can distinguish between `DATA` and `RESPONSE` messages.

### Probe and Echo

The `probe(dst, payload, timeout)` method sends a `DATA` packet and waits for a matching `RESPONSE`. A response is accepted only if its type, source, destination, and message identifier match the outstanding probe.

The method uses a monotonic clock to enforce a finite timeout. If no matching response arrives before the deadline, it returns `None`.

The `echo(packet)` method responds to a `DATA` packet by creating a separate `RESPONSE` packet. The response uses the receiving host as its source, the original sender as its destination, and preserves the original message identifier and payload.

The `run()` method continuously receives packets and echoes valid `DATA` messages. The `close()` method closes the UDP socket.

## 3. Packet Format and Constraints

The Host uses the shared six-field packet format.

| Field       | Purpose                                       |
| ----------- | --------------------------------------------- |
| `src`       | Virtual source address                        |
| `dst`       | Virtual destination address                   |
| `hop_limit` | Limits packet forwarding                      |
| `type`      | Distinguishes `DATA` and `RESPONSE`           |
| `id`        | Identifies a message and matches its response |
| `payload`   | Application data                              |

Packets are serialized as UTF-8 JSON by the packet implementation. The maximum serialized UDP payload is 1,200 bytes, including custom headers.

The Host does not implement fragmentation, reassembly, retransmission, or reliable delivery as part of the common core.

## 4. Logging

The Host uses Python's standard `logging` module. Its log records include a run identifier and device identifier.

Logged events include:

- Host startup and shutdown.
- Packet sends and receives.
- Invalid, oversized, or incorrectly addressed packets.
- Probe success and timeout.
- Echo failures.

Where applicable, records include the message identifier, message type, virtual source and destination, next hop, hop limit, and serialized packet size. Timestamps are provided by the configured logging formatter.

The application launcher must configure logging for messages to be emitted. Router forwarding and router-drop events remain the responsibility of the router implementation. The integrated system should use a consistent logging format across devices.

## 5. Tests and Acceptance Evidence

The Host unit tests currently pass. They cover:

- Correct initialization and UDP binding.
- Rejection of router devices and hosts with zero or multiple neighbors.
- Socket cleanup when binding fails.
- Sending packets to the configured immediate neighbor.
- Correct packet fields and message identifier increments.
- Receiving valid packets and ignoring invalid or incorrectly addressed packets.
- Probe success and timeout behavior.
- Rejection of responses with incorrect message identifiers or sources.
- Echo responses preserving the message identifier and payload.
- The receive loop continuing after socket timeouts and stopping after socket closure.
- Rejection of oversized outgoing packets without transmitting them.

These tests provide evidence of Host behavior in isolation. They do not, by themselves, demonstrate that the complete network passes the project acceptance scenarios.

### Common-Network Acceptance Evidence Still Needed

During integration, verify and record evidence that:

1. Both end hosts can initiate probe/echo exchanges through the routers.
2. The upper path through Router B works with the appropriate static forwarding tables.
3. The lower path through Router C works after the forwarding tables are changed.
4. A missing response results in a finite timeout.
5. Logs trace a request and response through the complete network.

Actual results, commands, log excerpts, and failures should be documented after these scenarios are run.

## 6. Known Limitations and Unresolved Defects

The following issues require attention or integration verification.

- **Concurrent receiving:** `probe()` and `run()` can both read from the same UDP socket. If called concurrently, one may consume a packet intended for the other. Simultaneous probe handling should be tested during integration. If this causes failures, a shared receive loop or packet-dispatch mechanism may be needed.
- **Logging consistency:** The complete project needs a consistent run identifier, timestamp format, and event-field convention across hosts and routers.
- **Integrated behavior:** End-to-end path selection, forwarding, and return routing depend on the router implementation and configuration. Host unit tests alone do not establish that these scenarios work.
- **Message identifier wraparound:** Identifiers wrap modulo 2^32. Responses are matched using source, destination, type, and identifier, but identifiers should not be reused while an earlier request with the same identifier remains outstanding.

Fragmentation, reassembly, retransmission, and automatic routing are outside the common-core scope.

## Summary

The Host component implements configured UDP communication, packet validation, probe/echo behavior, finite response timeouts, and host-side logging. Its unit tests pass.

The remaining work is to verify end-to-end communication, collect common-network acceptance evidence, confirm the baseline repository checkpoint, and document the group's selected extension and experimental plan.