# Host Implementation --- Progress Report Notes

## 1. Scope and Responsibilities

The Host component implements end-host communication for the
mini-internet project. Hosts communicate through the configured router
network using UDP.

The Host component is responsible for:

-   Initializing a host using the shared `Config` object.
-   Binding a UDP socket to the host's configured IP address and port.
-   Sending packets to its immediate neighbor rather than directly to
    the final destination.
-   Receiving and validating packets addressed to the host.
-   Implementing best-effort probe/echo communication with a finite
    response timeout.
-   Implementing optional reliable finite-message transfers using
    acknowledgements and retransmissions.
-   Tracking message sequence numbers and preventing duplicate delivery
    during reliable transfers.
-   Logging host communication events and errors.

Router forwarding, static routing-table implementation, and
router-specific logging are outside the Host component's responsibility.
Routers forward packets based on their final virtual destination and
preserve the packet's reliability fields.

## 2. Current Architecture

The Host uses the following components:

-   **`Config`:** Provides device information, virtual addresses, UDP
    endpoints, and neighbor relationships.
-   **`Packet`:** Represents network messages and provides packet
    validation, serialization, and parsing.
-   **UDP socket:** Sends and receives datagrams between a host and its
    configured immediate neighbor.
-   **`LinkSender`:** Sends serialized packets to the configured next
    hop and applies configured directed-link loss and delay conditions.
-   **Python logging:** Records host communication events and errors.

### Initialization

The constructor accepts a host identifier and a `Config` instance. It
verifies that the specified device is a host and has exactly one
neighbor. It then binds a UDP socket to the configured endpoint.

If binding fails, the socket is closed and the error is propagated. The
Host also stores its configured sending mode, timeout, retry limit, and
receive-side sequence tracking state.

### Sending

The public sending interface is:

``` python
host.send(data, dst, mode=None)
```

The caller supplies the final virtual destination. The Host creates a
`DATA` packet containing the virtual source and destination,
time-to-live value, message type, message identifier, sequence number,
and application payload.

The packet is serialized and sent to the configured neighbor's UDP
endpoint. The packet's `dst` field remains the final destination so that
routers can forward it using their routing tables.

The default mode is `best_effort`. A best-effort send transmits one
`DATA` packet without waiting for an acknowledgement or retransmitting
it. The message identifier increments and wraps around at the unsigned
32-bit boundary.

In `reliable` mode, the Host starts a finite transfer. The transfer uses
one transfer identifier and an increasing sequence number for each
message. Only one packet is outstanding at a time: the sender waits for
the matching ACK before sending the next message.

Each application message must fit in one packet. If the serialized
packet exceeds the 1,200-byte limit, the packet serialization layer
rejects it. The Host does not implement fragmentation or reassembly
because these features are not required for the common core.

### Receiving

The Host receives UDP datagrams and parses them using
`Packet.from_bytes()`.

Malformed packets, oversized datagrams, and packets addressed to another
host are rejected without crashing the receive loop. Valid packets
addressed to the host are returned as `Packet` objects so the Host can
distinguish between `DATA`, `RESPONSE`, `ACK`, and `FIN` messages.

### Best-Effort Probe and Echo

The `probe(dst, payload, timeout)` method sends a `DATA` packet and
waits for a matching `RESPONSE`. A response is accepted only if its
type, source, destination, message identifier, and payload match the
outstanding probe.

The method uses a monotonic clock to enforce a finite timeout. If no
matching response arrives before the deadline, it returns `None`.
Best-effort probes are not retransmitted.

The `echo(packet)` method responds to a `DATA` packet by creating a
separate `RESPONSE` packet. The response uses the receiving host as its
source, the original sender as its destination, and preserves the
original message identifier, sequence number, and payload.

The interactive host receive loop can process incoming packets while
waiting for user input or a probe response. The `run()` method
continuously receives packets and processes them. The `close()` method
closes the link sender and UDP socket.

### Reliable Finite-Message Transfer

Reliable transfer is implemented at the Host endpoints; routers do not
maintain transfer state.

The `start_transfer(messages, dst)` method sends a finite list of
messages to the destination host. Each transfer uses a single transfer
ID and sequence numbers beginning at zero. The sender sends one `DATA`
packet and waits for an `ACK` with the matching source, destination,
transfer ID, and sequence number.

If the matching ACK does not arrive before the configured timeout, the
sender retransmits the same packet. Retransmission continues until an
ACK arrives or the configured retry limit is reached. The transfer then
reports `COMPLETE` or `FAILED`.

The receiver tracks the next expected sequence number for each source
and transfer ID. It delivers only the expected message and advances the
expected sequence number. A duplicate message with an earlier sequence
number is not delivered again, but it is acknowledged again so that a
sender can recover from a lost ACK. A message with a sequence number
greater than the expected value is not delivered.

After all data messages are acknowledged, the sender sends a `FIN`
packet with the next sequence number. The receiver acknowledges the FIN
and marks the transfer complete. This provides an explicit end marker
for the finite transfer.

The timeout and retry limit are configurable through the Host
constructor. The reliable transfer method is synchronous: it returns
after the transfer completes or fails.

## 3. Packet Format and Constraints

The Host uses the shared seven-field packet format.

  -----------------------------------------------------------------------
  Field                               Purpose
  ----------------------------------- -----------------------------------
  `src`                               Virtual source address

  `dst`                               Virtual destination address

  `ttl`                               Limits packet forwarding

  `type`                              Packet type: `DATA`, `RESPONSE`,
                                      `ACK`, or `FIN`

  `id`                                Identifies a message or reliable
                                      transfer

  `seq`                               Identifies a message's position
                                      within a reliable transfer

  `payload`                           Application data; ACK and FIN
                                      control packets use an empty
                                      payload
  -----------------------------------------------------------------------

Packets are serialized as UTF-8 JSON by the packet implementation. The
maximum serialized packet size is 1,200 bytes, including the packet
fields.

The `id` and `seq` fields are unsigned 32-bit integers. The TTL must be
between 1 and 8. ACK packets must have an empty payload.

The Host does not implement fragmentation or reassembly. Best-effort
mode does not provide retransmission or guaranteed delivery. Reliable
mode provides stop-and-wait retransmission and in-order, at-most-once
delivery for a finite transfer.

## 4. Logging

The Host uses Python's standard `logging` module through the project's
event-logging interface. Its log records include a run identifier and
device identifier.

Host events include:

-   Host startup and shutdown.
-   Packet sends and receives.
-   Invalid, oversized, or incorrectly addressed packets.
-   Probe success and timeout.
-   Reliable transfer start, completion, and failure.
-   ACK send and receive events.
-   Retransmissions.
-   Duplicate and out-of-order packet handling.
-   Successful message delivery.

Where applicable, records include the message or transfer identifier,
sequence number, packet type, virtual source and destination, next hop,
and hop limit. Timestamps are provided by the configured logging
formatter.

The application launcher must configure logging for messages to be
emitted. Router forwarding and router-drop events remain the
responsibility of the router implementation. The integrated system
should use a consistent logging format across devices. The same run ID
should be supplied to the sender, receiver, and routers so events can be
correlated.

## 5. Tests and Acceptance Evidence

The existing Host unit-test coverage described for the common
communication implementation includes:

-   Correct initialization and UDP binding.
-   Rejection of router devices and hosts with zero or multiple
    neighbors.
-   Socket cleanup when binding fails.
-   Sending packets to the configured immediate neighbor.
-   Correct packet fields and message identifier increments.
-   Receiving valid packets and ignoring invalid or incorrectly
    addressed packets.
-   Probe success and timeout behavior.
-   Rejection of responses with incorrect message identifiers or
    sources.
-   Echo responses preserving the message identifier and payload.
-   The receive loop continuing after socket timeouts and stopping after
    socket closure.
-   Rejection of oversized outgoing packets without transmitting them.

Reliable-transfer behavior additionally needs corresponding evidence
for:

-   A sender waiting for an ACK before sending the next sequence number.
-   Retransmission after an ACK timeout and termination after the retry
    limit.
-   Receiver sequence tracking and in-order delivery.
-   Duplicate DATA packets being acknowledged without being delivered
    twice.
-   FIN acknowledgement and successful finite-transfer completion.
-   Transfer failure being reported when the retry limit is reached.
-   Reliable transfers working in both H1-to-H2 and H2-to-H1 directions
    through the routers.
-   Existing best-effort probes continuing to work when reliable mode is
    available.

Unit tests provide evidence of component behavior in isolation. They do
not, by themselves, demonstrate that the complete network passes the
project acceptance scenarios. Do not report integration scenarios as
passed until they have actually been run and their results recorded.

### Common-Network Acceptance Evidence

During integration, verify and record evidence that:

1.  Both end hosts can initiate best-effort probe/echo exchanges through
    the routers.
2.  The upper path through Router B works with the appropriate static
    forwarding tables.
3.  The lower path through Router C works after the forwarding tables
    are changed.
4.  A missing probe response results in a finite timeout.
5.  Logs trace a request and response through the complete network.
6.  H1 can send a reliable finite transfer to H2.
7.  H2 can send a reliable finite transfer to H1.
8.  Reliable transfers recover from packet or ACK loss when retries
    remain available.
9.  Reliable-transfer logs show sequence numbers, ACKs, retransmissions,
    deliveries, and completion or failure.

Actual results, commands, log excerpts, and failures should be
documented after these scenarios are run.

## 6. Known Limitations and Unresolved Defects

The following limitations and integration concerns should remain visible
in the progress report.

-   **Concurrent socket readers:** `probe()` and `run()` must not
    independently consume packets from the same UDP socket at the same
    time. If called concurrently, one may consume a packet intended for
    the other. The interactive implementation should use one
    receive/event loop rather than starting competing readers.
    Concurrent independent probes are not supported by the stop-and-wait
    implementation.
-   **One outstanding reliable packet:** Reliable transfer uses
    stop-and-wait rather than a sliding window. This is intentionally
    simple but limits throughput.
-   **Synchronous reliable transfer:** `start_transfer()` blocks until
    the transfer completes or fails. It is not an asynchronous
    background transfer API.
-   **Packet size:** Each message must fit within the 1,200-byte
    serialized packet limit. Fragmentation and reassembly are not
    implemented.
-   **Identifier reuse:** Identifiers wrap modulo 2\^32. Reliable
    transfer matching uses the transfer ID and sequence number.
    Identifiers should not be reused while an earlier transfer using the
    same source and ID could still be active.
-   **Best-effort delivery:** Best-effort probes and sends have no
    retransmission or guaranteed delivery. A timeout indicates that no
    matching response arrived before the deadline; it does not prove
    that the request was never delivered.
-   **Integration dependency:** End-to-end path selection, forwarding,
    and return routing depend on the router implementation and
    configuration. Host unit tests alone do not establish that these
    scenarios work.
-   **Logging consistency:** The complete project needs a consistent run
    identifier, timestamp format, and event-field convention across
    hosts and routers.

Router forwarding, static routing, and router-specific drop handling
remain outside the Host component's responsibility. Automatic routing,
fragmentation, and reassembly are also outside the current
implementation scope.
