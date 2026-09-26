# Routers (Week 1)

Run each router in its own terminal, then run the test:

```
python RouterA.py
python RouterB.py
python RouterC.py
python RouterD.py
python test_routers.py
```

- The four router files are identical except for `NAME`.
- `config.json` holds each device's UDP port and each router's forwarding table (destination -> next hop).
- Packets are JSON: `src`, `dst`, `type`, `id`, `ttl`, `payload`. Max 1200 bytes.
- A router drops a packet if it is invalid, its ttl is 1 or less, or its destination is not in the table.
  Otherwise it decrements ttl and sends it to the next hop.
- Lower path: in `config.json` change A's `H2` entry to `C` and D's `H1` entry to `C`.
