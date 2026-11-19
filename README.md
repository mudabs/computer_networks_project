# Mini Internet

A small network simulator in Python: hosts and routers exchange real UDP packets on your own computer, and you can watch them travel, get lost, and be re-sent.

CSCI 5500 Computer Networks, Fall 2026 - Group 13.

![The Mini Internet GUI after a Dropped ACK scenario: topology, counters, and controls](docs/images/gui.png)

## Run the GUI

```bash
python gui.py
```

On macOS or Linux, use `python3 gui.py` if `python` is not found. Python 3 is all you need: there are no third-party packages. Run it from the repository root.

## Using the GUI

1. **Pick the endpoints.** Choose a **Source host** and a **Destination host** (they must differ). The blue line on the map is the route.
2. **Send one packet (best effort).** Type a **Message** and click **Send packet**. The result appears under **LAST RESULT** as MATCHED, TIMEOUT, or LATE. Nothing is re-sent if a packet is lost.
3. **Send reliably.** Under **RELIABLE TRANSFER**, set **Message count**, **Timeout (seconds)** (the field above), and **Max retries**, then click **Start reliable transfer**. Lost packets are re-sent until they are acknowledged. **Stop transfer** cancels it.
4. **Try a scenario.** Under **RELIABLE SCENARIOS**, click **Normal**, **Dropped DATA**, **Dropped ACK**, or **Unreachable** to run a ready-made reliable transfer and watch retries and duplicates.
5. **Look around.** The tabs below the map are **Routes** (change next hops), **Link conditions** (add loss and delay), and **Event log** (every packet event).
6. **Start over.** **Restart network** applies your changes. **Reset all settings** returns to the original configuration. Nothing is saved to the JSON files.

## Troubleshooting

- **Do not run the GUI at the same time as `main.py --all`.** Both use the same UDP ports, so the second one will fail to start.
- **"Could not start network" or a port error:** close any other copy of the GUI or any `main.py` device, then click **Restart network**.
- **`No module named tkinter`:** Tkinter ships with Python on Windows and macOS. On some Linux systems install `python3-tk`.
- **Some buttons are out of view:** the left panel scrolls. Use the mouse wheel or the scroll bar.

## More details

- [GUI manual](info/GUI.md): every control, scenario, counter, and message
- [Command-line use](info/CLI.md): `main.py`, probes, link conditions, logs, tests
- [Host](info/HOST.md), [Packet](info/PACKET.md), and [Routers](info/ROUTERS.md) design notes
- [Evaluation report](results/evaluation_report.md): best-effort vs reliable results
