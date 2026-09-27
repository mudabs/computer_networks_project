# Mini Internet Packet Format
## Overview

This document defines the packet format used by the Mini Internet hosts and routers.
Packets are serialized as JSON and encoded as UTF-8 before being transmitted using UDP.
The complete serialized packet must not exceed 1,200 bytes.
Each packet contains six required fields:

- `src`
- `dst`
- `hop_limit`
- `type`
- `id`
- `payload`

## Packet Structure

A packet is represented as a JSON object containing exactly six fields.

```json
{
  "src": "H1",
  "dst": "H2",
  "hop_limit": 8,
  "type": "DATA",
  "id": 0,
  "payload": "Hello H2"
}
```

## Fields

### `src`
The virtual address of the device that originally created the packet.
- Type: string
- Example: `"H1"`
- The source address does not change when the packet is forwarded.

### `dst`
The virtual address of the packet's final destination.
- Type: string
- Example: `"H2"`
- The destination address does not change when the packet is forwarded.
- Routers use the destination address to select the next hop.

### `hop_limit`
The number of forwarding hops remaining for the packet.
- Type: integer
- Valid range: `1–8`
- The value is decreased when a router forwards the packet.
- A packet whose hop limit has expired is discarded.

### `type`
Identifies the type of message carried by the packet.
- Type: string
- Supported values:
  - `DATA` — an ordinary application message.
  - `RESPONSE` — a response to an earlier message.
- Additional types may be added if required by the project extension.

### `id`
Identifies the message so it can be tracked across the network.
- Type: unsigned 32-bit integer
- Valid range: `0–4,294,967,295`
- Used to correlate messages and log records.
- Does not provide reliable delivery by itself.

### `payload`
Contains the application message carried by the packet.
- Type: string
- Contains the short message being sent between hosts.
- The payload must fit within the overall 1,200-byte packet limit.

## Encoding
Packets are serialized as JSON and encoded as UTF-8 before being transmitted through UDP.
Received UDP data is decoded from UTF-8 and parsed as JSON before the packet is validated.

## Size Limit
The complete serialized packet must not exceed 1,200 bytes.
The limit includes the JSON structure, field names, field values, and payload after UTF-8 encoding.

## Validation
A packet is invalid if:
- It is not valid UTF-8.
- It is not valid JSON.
- It is not a JSON object.
- A required field is missing.
- An unexpected field is present.
- A field has the wrong type.
- `hop_limit` is outside the range `1–8`.
- `type` is not a supported message type.
- `id` is outside the unsigned 32-bit range.
- The serialized packet exceeds 1,200 bytes.
Invalid packets must be rejected without crashing the host or router process.

## Example
A valid packet:
```json
{
  "src": "H1",
  "dst": "H2",
  "hop_limit": 8,
  "type": "DATA",
  "id": 0,
  "payload": "Hello H2"
}
```
When a router forwards the packet, the `hop_limit` is decreased. The other fields remain unchanged.
```json
{
  "src": "H1",
  "dst": "H2",
  "hop_limit": 7,
  "type": "DATA",
  "id": 0,
  "payload": "Hello H2"
}
```

## Error Handling
Invalid packets must be rejected without crashing the host or router process.
The receiver should record an error in its log when a packet is rejected.