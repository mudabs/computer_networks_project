# Mini Internet Packet Format

## Overview

This document defines the packet format used by Mini Internet hosts and
routers.

Packets are serialized as JSON and encoded as UTF-8 before being
transmitted using UDP. The complete serialized packet must not exceed
1,200 bytes.

Each packet contains exactly seven required fields:

-   `src`
-   `dst`
-   `ttl`
-   `type`
-   `id`
-   `seq`
-   `payload`

The packet format supports ordinary data messages, application-level
responses, and acknowledgments used by reliable transfer.

## Packet Structure

A packet is represented as a JSON object containing exactly seven
fields.

### DATA packet

``` json
{
  "src": "H1",
  "dst": "H2",
  "ttl": 8,
  "type": "DATA",
  "id": 42,
  "seq": 0,
  "payload": "Hello H2"
}
```

### ACK packet

``` json
{
  "src": "H2",
  "dst": "H1",
  "ttl": 8,
  "type": "ACK",
  "id": 42,
  "seq": 0,
  "payload": ""
}
```

## Fields

### `src`

The virtual address of the device that created the packet.

-   Type: string
-   Must not be empty or contain only whitespace.
-   Example: `"H1"`
-   For a DATA or RESPONSE packet, this is the original sender.
-   For an ACK packet, this is the host sending the acknowledgment.
-   The source address does not change when a router forwards the
    packet.

### `dst`

The virtual address of the packet's final destination.

-   Type: string
-   Must not be empty or contain only whitespace.
-   Example: `"H2"`
-   Routers use the destination address to select the next hop.
-   The destination address does not change when a router forwards the
    packet.

### `ttl`

The number of forwarding hops remaining for the packet.

-   Type: integer
-   Valid range: `1–8`
-   The value is decreased when a router forwards the packet.
-   A packet whose TTL has expired is discarded.

### `type`

Identifies the kind of packet.

-   Type: string
-   Supported values:
    -   `DATA` --- carries application data.
    -   `ACK` --- acknowledges receipt of a DATA packet.
    -   `RESPONSE` --- carries an application-level response to an
        earlier message.

The packet type determines how the payload and other fields are
interpreted.

### `id`

Identifies the message or transfer so that packets can be correlated.

-   Type: unsigned 32-bit integer
-   Valid range: `0–4,294,967,295`
-   Used to associate packets with the same message or transfer and to
    support logging and acknowledgment matching.
-   The `id` field does not provide reliable delivery by itself.
-   An ACK must contain the same `id` as the DATA packet it
    acknowledges.

For this protocol, `id` identifies a message or transfer; it is not a
separate identifier for an entire program execution.

### `seq`

Identifies a packet's sequence position within a message or transfer.

-   Type: unsigned 32-bit integer
-   Valid range: `0–4,294,967,295`
-   A DATA packet carries the sequence number assigned by the sender.
-   An ACK carries the same `seq` as the DATA packet it acknowledges.
-   The receiver uses the sequence number to distinguish new DATA
    packets from retransmitted duplicates.

The current reliable-transfer implementation uses Stop-and-Wait with
alternating sequence numbers `0` and `1`. The packet format allows the
full unsigned 32-bit range so that other reliable-transfer strategies
can be supported in the future.

The sequence number is not a checksum and does not detect corruption in
packet contents.

### `payload`

Contains the packet's application data.

-   Type: string
-   For `DATA` and `RESPONSE` packets, contains the application message.
-   For `ACK` packets, must be an empty string (`""`).
-   The payload must fit within the overall 1,200-byte packet limit.

## DATA and ACK Semantics

A DATA packet carries information from its source to its destination.

An ACK packet confirms receipt of a particular DATA packet. It does not
carry application data.

An ACK corresponding to a DATA packet must:

1.  Have `type` set to `ACK`.
2.  Set `src` to the DATA packet's `dst`.
3.  Set `dst` to the DATA packet's `src`.
4.  Use the same `id` as the DATA packet.
5.  Use the same `seq` as the DATA packet.
6.  Have an empty `payload`.

The ACK's `ttl` is set for its own route back to the original sender.
Routers may decrement it as they forward the ACK.

The host performing reliable-transfer processing is responsible for
matching ACKs to outstanding DATA packets. Packet validation checks the
structure and field values; it does not determine whether an ACK matches
a particular outstanding packet.

## Sequence Number and Duplicate Handling

The sequence number allows a receiver to recognize a retransmission of a
DATA packet it has already accepted.

For the current Stop-and-Wait implementation:

-   The sender alternates sequence numbers between `0` and `1`.
-   The receiver tracks the sequence number it expects next.
-   A DATA packet with the expected sequence number is treated as new.
-   A repeated DATA packet with the previously accepted sequence number
    is treated as a duplicate.
-   A duplicate must not be delivered to the application a second time.
    The receiver should send the corresponding ACK again.

Sequence-number state and duplicate handling are maintained by the
host's reliable-transfer logic, not by the packet serializer or router.

## Encoding

Packets are serialized as JSON and encoded as UTF-8 before being
transmitted through UDP.

Received UDP data is decoded from UTF-8 and parsed as JSON before the
packet is validated.

## Size Limit

The complete serialized packet must not exceed 1,200 bytes.

The limit includes the JSON structure, field names, field values, and
payload after UTF-8 encoding.

## Validation

A packet is invalid if:

-   It is not valid UTF-8.
-   It is not valid JSON.
-   It is not a JSON object.
-   A required field is missing.
-   An unexpected field is present.
-   A field has the wrong type.
-   `src` or `dst` is empty or contains only whitespace.
-   `ttl` is not an integer or is outside the range `1–8`.
-   `type` is not one of the supported values: `DATA`, `ACK`, or
    `RESPONSE`.
-   `id` is not an integer or is outside the unsigned 32-bit range.
-   `seq` is not an integer or is outside the unsigned 32-bit range.
-   `payload` is not a string.
-   An `ACK` packet has a non-empty payload.
-   The serialized packet exceeds 1,200 bytes.

Boolean values must not be accepted where an integer is required, even
though Python treats `bool` as a subclass of `int`.

Invalid packets must be rejected without crashing the host or router
process. The receiver should record an error in its log when a packet is
rejected.

## Examples

### DATA packet

``` json
{
  "src": "H1",
  "dst": "H2",
  "ttl": 8,
  "type": "DATA",
  "id": 42,
  "seq": 0,
  "payload": "Hello H2"
}
```

When a router forwards the packet, the `ttl` is decreased. The other
fields remain unchanged.

``` json
{
  "src": "H1",
  "dst": "H2",
  "ttl": 7,
  "type": "DATA",
  "id": 42,
  "seq": 0,
  "payload": "Hello H2"
}
```

### Corresponding ACK packet

``` json
{
  "src": "H2",
  "dst": "H1",
  "ttl": 8,
  "type": "ACK",
  "id": 42,
  "seq": 0,
  "payload": ""
}
```

The ACK uses the DATA packet's `id` and `seq`, while reversing its `src`
and `dst`.

## Error Handling

Invalid packets must be rejected without crashing the host or router
process.

The receiver should record an error in its log when a packet is
rejected.

Packet-format validation and reliable-transfer processing are separate
responsibilities:

-   Packet validation checks that an individual packet is structurally
    valid.
-   Reliable-transfer logic checks ACK matching, sequence expectations,
    duplicate DATA packets, timeouts, and retransmissions.
-   Routers forward valid packets and do not need to manage end-to-end
    ACK matching or duplicate detection.
