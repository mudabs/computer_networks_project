"""Modern visual demo for the Mini Internet project.

The GUI uses the same Host, Router, LinkSender, routing, packet validation,
loss, and delay code as the command-line application.  Changes made in the
Routes and Link Conditions tabs are kept in memory and applied by restarting
the demo network; the JSON files are never modified.

Run from the repository root with:

    python gui.py
"""

import copy
import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from config import Config
from host import Host
from router import Router


BG = "#0b1220"
PANEL = "#111827"
PANEL_2 = "#172033"
CANVAS = "#08101d"
TEXT = "#e5eefb"
MUTED = "#93a4bd"
ACCENT = "#38bdf8"
ACCENT_2 = "#a78bfa"
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#fb7185"
GRID = "#2a3950"


class QueueLogHandler(logging.Handler):
    """Forward application log records to the Tk event loop."""

    def __init__(self, records):
        super().__init__()
        self.records = records

    def emit(self, record):
        try:
            self.records.put_nowait(record)
        except queue.Full:
            pass


def parse_event(message):
    """Parse the key=value event format emitted by link.event()."""
    fields = {}
    for token in message.split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


class NetworkController:
    """Run the real network devices in background threads for the GUI."""

    def __init__(self):
        self.devices = {}
        self.threads = []
        self.config = None
        self.source = None
        self.destination = None
        self.run_id = None
        self.running = False

    @staticmethod
    def _run_device(device):
        try:
            device.run()
        except (OSError, ConnectionError):
            # Normal when the controller closes a socket during restart.
            pass
        except Exception:
            logging.getLogger(__name__).exception(
                "GUI device thread failed for %s", device.id
            )

    def start(self, network_data, route_data, source, destination):
        if source == destination:
            raise ValueError("Source and destination hosts must be different")

        self.stop()
        self.config = Config(network_data=network_data, route_data=route_data)
        self.source = source
        self.destination = destination
        self.run_id = f"gui-{int(time.time() * 1000)}"
        self.devices = {}
        self.threads = []

        # Routers always run.  Only the destination host runs its echo loop;
        # the source host is reserved for the probe started by the GUI.
        for device_id, entry in self.config.data["devices"].items():
            if entry["type"] == "router":
                self.devices[device_id] = Router(
                    device_id, self.config, run_id=self.run_id
                )

        for device_id, entry in self.config.data["devices"].items():
            if entry["type"] == "host":
                self.devices[device_id] = Host(
                    device_id, self.config, run_id=self.run_id
                )

        for device_id, device in self.devices.items():
            if self.config.get_device(device_id)["type"] == "router":
                thread = threading.Thread(
                    target=self._run_device,
                    args=(device,),
                    name=f"router-{device_id}",
                    daemon=True,
                )
                self.threads.append(thread)
                thread.start()

        destination_thread = threading.Thread(
            target=self._run_device,
            args=(self.devices[destination],),
            name=f"host-{destination}",
            daemon=True,
        )
        self.threads.append(destination_thread)
        destination_thread.start()
        self.running = True

    def probe(self, payload, timeout):
        if not self.running:
            raise RuntimeError("The network is not running")
        source_host = self.devices[self.source]
        if getattr(self, "observe_late", False):
            return source_host.probe_with_late(
                self.destination, payload, timeout=timeout, late_window=3.0
            )
        response = source_host.probe(self.destination, payload, timeout=timeout)
        return ("MATCHED", response) if response is not None else ("TIMEOUT", None)

    def stop(self):
        self.running = False
        devices = list(self.devices.values())
        self.devices = {}
        for device in devices:
            try:
                device.close()
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=1.0)
        self.threads = []


class NetworkGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mini Internet · Network Visualizer")
        self._configure_window_for_screen()
        self.configure(bg=BG)

        self.file_config = Config()
        self.base_network = copy.deepcopy(self.file_config.data)
        self.base_routes = copy.deepcopy(self.file_config.route_data)
        self.network_data = copy.deepcopy(self.base_network)
        self.route_data = copy.deepcopy(self.base_routes)

        self.controller = NetworkController()
        self.records = queue.Queue(maxsize=3000)
        self.log_handler = QueueLogHandler(self.records)
        self.log_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
        ))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self.log_handler)

        self.source_var = tk.StringVar(value="H1")
        self.destination_var = tk.StringVar(value="H2")
        self.message_var = tk.StringVar(value="Hello from the GUI")
        self.timeout_var = tk.StringVar(value="3.0")
        self.observe_late_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Starting network…")
        self.result_var = tk.StringVar(value="No probe sent yet")
        self.path_var = tk.StringVar(value="Path: —")
        self.run_var = tk.StringVar(value="Run: —")
        self.packet_count_var = tk.StringVar(value="0 sent")
        self.drop_count_var = tk.StringVar(value="0 dropped")
        self.probe_in_flight = False
        self.observe_late = False
        self.active_source = None
        self.active_destination = None
        self.node_items = {}
        self.node_positions = {}
        self.link_items = []
        self.log_lines = 0

        self._configure_styles()
        self._build_layout()
        self._refresh_route_controls()
        self._refresh_condition_controls()
        self._draw_topology()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(75, self._drain_records)
        self.after(100, self._start_initial_network)

    def _configure_window_for_screen(self):
        """Choose a usable initial size for laptops, TVs, and monitors."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        # Leave room for the taskbar, window borders, and display scaling.
        minimum_width = min(900, max(760, screen_width - 30))
        minimum_height = min(620, max(560, screen_height - 100))
        self.target_width = min(1380, max(minimum_width, screen_width - 40))
        self.target_height = min(860, max(minimum_height, screen_height - 80))
        self.sidebar_width = min(320, max(250, int(self.target_width * 0.23)))

        left = max(0, (screen_width - self.target_width) // 2)
        top = max(0, (screen_height - self.target_height) // 2)
        self.geometry(f"{self.target_width}x{self.target_height}+{left}+{top}")
        self.minsize(minimum_width, minimum_height)

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=PANEL)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL_2)
        style.configure("TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 10))
        style.configure("Section.TLabel", background=PANEL, foreground=ACCENT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("TButton", background=PANEL_2, foreground=TEXT,
                        borderwidth=0, padding=(10, 7), font=("Segoe UI", 9))
        style.map("TButton", background=[("active", "#263854")],
                  foreground=[("disabled", "#607089")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#06111f",
                        padding=(12, 8), font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#7dd3fc")])
        style.configure("Danger.TButton", background="#4c2234", foreground="#fecdd3",
                        padding=(10, 7))
        style.configure("TEntry", fieldbackground="#0e1829", foreground=TEXT,
                        insertcolor=TEXT, borderwidth=0, padding=6)
        style.configure("TCombobox", fieldbackground="#0e1829", foreground=TEXT,
                        background=PANEL_2, arrowcolor=TEXT, borderwidth=0, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", "#0e1829")],
                  foreground=[("readonly", TEXT)])
        style.configure("Treeview", background="#0e1829", fieldbackground="#0e1829",
                        foreground=TEXT, borderwidth=0, rowheight=25)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=MUTED,
                        borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#1d4d70")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL_2, foreground=MUTED,
                        padding=(10, 7))
        style.map("TNotebook.Tab", background=[("selected", "#243652")],
                  foreground=[("selected", TEXT)])

    def _build_layout(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        header = tk.Frame(self, bg=BG, height=76)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        ttk.Label(header, text="Mini Internet", style="Title.TLabel").pack(
            side="left", padx=(28, 8), pady=(15, 0)
        )
        ttk.Label(header, text="Visual network simulator", style="Subtitle.TLabel").pack(
            side="left", pady=(20, 0)
        )
        self.status_badge = tk.Label(
            header, textvariable=self.status_var, bg="#173c3a", fg=GOOD,
            font=("Segoe UI", 9, "bold"), padx=12, pady=6
        )
        self.status_badge.pack(side="right", padx=28, pady=19)

        self.sidebar = ttk.Frame(self, style="Panel.TFrame", padding=(20, 16))
        self.sidebar.grid(row=1, column=0, sticky="nsew")
        self.sidebar.configure(width=self.sidebar_width)
        self.sidebar.grid_propagate(False)
        self._build_sidebar()

        self.content = ttk.Frame(self, style="Panel.TFrame", padding=(0, 0, 20, 20))
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=0)
        self.content.columnconfigure(0, weight=1)
        self._build_content()

    def _build_sidebar(self):
        ttk.Label(self.sidebar, text="DEMO CONTROLS", style="Section.TLabel").pack(
            anchor="w", pady=(0, 12)
        )

        self._field_label("Source host")
        self.source_box = ttk.Combobox(
            self.sidebar, textvariable=self.source_var, values=self._host_ids(),
            state="readonly", width=24
        )
        self.source_box.pack(fill="x", pady=(0, 9))
        self.source_box.bind("<<ComboboxSelected>>", lambda _event: self._endpoints_changed())

        self._field_label("Destination host")
        self.destination_box = ttk.Combobox(
            self.sidebar, textvariable=self.destination_var, values=self._host_ids(),
            state="readonly", width=24
        )
        self.destination_box.pack(fill="x", pady=(0, 9))
        self.destination_box.bind("<<ComboboxSelected>>", lambda _event: self._endpoints_changed())

        self._field_label("Message")
        ttk.Entry(self.sidebar, textvariable=self.message_var).pack(fill="x", pady=(0, 9))

        self._field_label("Timeout (seconds)")
        ttk.Entry(self.sidebar, textvariable=self.timeout_var).pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            self.sidebar, text="Observe late responses", variable=self.observe_late_var,
            command=lambda: setattr(self, "observe_late", self.observe_late_var.get())
        ).pack(anchor="w", pady=(0, 10))

        self.send_button = ttk.Button(
            self.sidebar, text="Send packet", style="Accent.TButton",
            command=self._send_probe
        )
        self.send_button.pack(fill="x", pady=(0, 14))

        result_card = tk.Frame(self.sidebar, bg=PANEL_2, padx=12, pady=10)
        result_card.pack(fill="x", pady=(0, 16))
        tk.Label(result_card, text="LAST RESULT", bg=PANEL_2, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(result_card, textvariable=self.result_var, bg=PANEL_2, fg=TEXT,
                 font=("Segoe UI", 10), wraplength=260, justify="left").pack(anchor="w", pady=(4, 0))

        ttk.Label(self.sidebar, text="QUICK SCENARIOS", style="Section.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        ttk.Button(self.sidebar, text="Normal delivery", command=self._normal_preset).pack(
            fill="x", pady=2
        )
        ttk.Button(self.sidebar, text="Force a route drop", command=self._drop_preset).pack(
            fill="x", pady=2
        )
        ttk.Button(self.sidebar, text="Show a late response", command=self._late_preset).pack(
            fill="x", pady=2
        )

        ttk.Separator(self.sidebar).pack(fill="x", pady=16)
        ttk.Button(self.sidebar, text="Restart network", command=self._restart_network).pack(
            fill="x", pady=2
        )
        ttk.Button(self.sidebar, text="Reset all settings", command=self._reset_all).pack(
            fill="x", pady=2
        )

        footer = tk.Frame(self.sidebar, bg=PANEL)
        footer.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Label(footer, textvariable=self.path_var, style="Muted.TLabel",
                  wraplength=270, justify="left").pack(anchor="w")
        ttk.Label(footer, textvariable=self.run_var, style="Muted.TLabel").pack(
            anchor="w", pady=(7, 0)
        )

    def _build_content(self):
        visual = ttk.Frame(self.content, style="Card.TFrame", padding=14)
        visual.grid(row=0, column=0, sticky="nsew")
        visual.rowconfigure(1, weight=1)
        visual.columnconfigure(0, weight=1)

        top_row = ttk.Frame(visual, style="Card.TFrame")
        top_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top_row.columnconfigure(0, weight=1)
        ttk.Label(top_row, text="Live topology", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(top_row, text="Packets are animated from real send events", style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        self.canvas = tk.Canvas(visual, bg=CANVAS, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._draw_topology())

        stats = ttk.Frame(visual, style="Card.TFrame")
        stats.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        stats.columnconfigure(0, weight=1)
        stats.columnconfigure(1, weight=1)
        stats.columnconfigure(2, weight=1)
        self._stat(stats, 0, "ROUTE", self.path_var)
        self._stat(stats, 1, "TRAFFIC", self.packet_count_var)
        self._stat(stats, 2, "DROPS", self.drop_count_var)

        lower = ttk.Frame(self.content, style="Panel.TFrame")
        lower.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(lower)
        notebook.grid(row=0, column=0, sticky="ew")
        self._build_routes_tab(notebook)
        self._build_conditions_tab(notebook)
        self._build_logs_tab(notebook)

    def _build_routes_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Routes")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        editor = ttk.Frame(tab)
        editor.grid(row=0, column=0, sticky="ew", padx=(0, 18))
        editor.columnconfigure(1, weight=1)
        ttk.Label(editor, text="Router").grid(row=0, column=0, sticky="w", pady=3)
        self.route_router_var = tk.StringVar(value="A")
        self.route_router_box = ttk.Combobox(
            editor, textvariable=self.route_router_var, values=self._router_ids(),
            state="readonly", width=16
        )
        self.route_router_box.grid(row=0, column=1, sticky="ew", pady=3)
        self.route_router_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_route_controls())
        ttk.Label(editor, text="Destination").grid(row=1, column=0, sticky="w", pady=3)
        self.route_destination_var = tk.StringVar()
        self.route_destination_box = ttk.Combobox(
            editor, textvariable=self.route_destination_var, state="readonly", width=16
        )
        self.route_destination_box.grid(row=1, column=1, sticky="ew", pady=3)
        self.route_destination_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_route_hops())
        ttk.Label(editor, text="Next hop").grid(row=2, column=0, sticky="w", pady=3)
        self.route_hop_var = tk.StringVar()
        self.route_hop_box = ttk.Combobox(
            editor, textvariable=self.route_hop_var, state="readonly", width=16
        )
        self.route_hop_box.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(editor, text="Apply route + restart", command=self._set_route).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        table_frame = ttk.Frame(tab)
        table_frame.grid(row=0, column=1, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        columns = ("router", "destination", "next_hop")
        self.route_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=4)
        for column, heading in zip(columns, ("Router", "Destination", "Next hop")):
            self.route_tree.heading(column, text=heading)
            self.route_tree.column(column, width=110, anchor="w")
        self.route_tree.grid(row=0, column=0, sticky="ew")
        self.route_tree.bind("<<TreeviewSelect>>", self._select_route_row)

    def _build_conditions_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Link conditions")
        tab.columnconfigure(1, weight=1)

        form = ttk.Frame(tab)
        form.grid(row=0, column=0, sticky="nw", padx=(0, 18))
        labels = ("From", "To", "Loss (0–1)", "Delay (ms)", "Seed")
        for row, label in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)

        self.condition_from_var = tk.StringVar()
        self.condition_to_var = tk.StringVar()
        self.condition_loss_var = tk.StringVar(value="0.0")
        self.condition_delay_var = tk.StringVar(value="0")
        self.condition_seed_var = tk.StringVar(value="42")
        self.condition_from_box = ttk.Combobox(
            form, textvariable=self.condition_from_var, state="readonly", width=13
        )
        self.condition_to_box = ttk.Combobox(
            form, textvariable=self.condition_to_var, state="readonly", width=13
        )
        self.condition_from_box.grid(row=0, column=1, pady=3)
        self.condition_to_box.grid(row=1, column=1, pady=3)
        self.condition_from_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_condition_to())
        ttk.Entry(form, textvariable=self.condition_loss_var, width=15).grid(row=2, column=1, pady=3)
        ttk.Entry(form, textvariable=self.condition_delay_var, width=15).grid(row=3, column=1, pady=3)
        ttk.Entry(form, textvariable=self.condition_seed_var, width=15).grid(row=4, column=1, pady=3)
        ttk.Button(form, text="Add / update", command=self._set_condition).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(8, 3)
        )
        ttk.Button(form, text="Remove selected", command=self._remove_condition).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=3
        )
        ttk.Button(form, text="Apply conditions + restart", command=self._restart_network).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(8, 3)
        )

        table_frame = ttk.Frame(tab)
        table_frame.grid(row=0, column=1, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        columns = ("from", "to", "loss", "delay", "seed")
        self.condition_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=4)
        for column, heading, width in zip(
            columns, ("From", "To", "Loss", "Delay", "Seed"), (70, 70, 70, 80, 70)
        ):
            self.condition_tree.heading(column, text=heading)
            self.condition_tree.column(column, width=width, anchor="w")
        self.condition_tree.grid(row=0, column=0, sticky="ew")
        self.condition_tree.bind("<<TreeviewSelect>>", self._select_condition_row)

    def _build_logs_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Event log")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            tab, height=7, bg=CANVAS, fg="#b7c7dc", insertbackground=TEXT,
            relief="flat", borderwidth=0, font=("Cascadia Mono", 9), wrap="none"
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ttk.Button(tab, text="Clear log", command=self._clear_log).grid(
            row=1, column=0, sticky="e", pady=(7, 0)
        )

    def _field_label(self, text):
        ttk.Label(self.sidebar, text=text, style="Muted.TLabel").pack(anchor="w", pady=(0, 4))

    @staticmethod
    def _stat(parent, column, title, variable):
        card = tk.Frame(parent, bg=PANEL_2, padx=10, pady=7)
        card.grid(row=0, column=column, sticky="ew", padx=3)
        tk.Label(card, text=title, bg=PANEL_2, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(card, textvariable=variable, bg=PANEL_2, fg=TEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

    def _host_ids(self):
        return [name for name, entry in self.network_data["devices"].items()
                if entry["type"] == "host"]

    def _router_ids(self):
        return [name for name, entry in self.network_data["devices"].items()
                if entry["type"] == "router"]

    def _directed_links(self):
        result = []
        for link in self.network_data["links"]:
            result.append((link["a"], link["b"]))
            result.append((link["b"], link["a"]))
        return result

    def _start_initial_network(self):
        self._restart_network(show_errors=False)

    def _endpoints_changed(self):
        self._draw_topology()
        self.status_var.set("Endpoint change ready — network restarts on send")

    def _restart_network(self, show_errors=True):
        if self.probe_in_flight:
            if show_errors:
                messagebox.showinfo("Probe in progress", "Wait for the current probe to finish.")
            return False
        source = self.source_var.get()
        destination = self.destination_var.get()
        if source == destination:
            if show_errors:
                messagebox.showerror("Invalid endpoints", "Source and destination must differ.")
            return False
        try:
            self.status_var.set("Restarting network…")
            self.update_idletasks()
            self.controller.start(self.network_data, self.route_data, source, destination)
            self.active_source = source
            self.active_destination = destination
            self.status_var.set("Network running")
            self.status_badge.configure(bg="#173c3a", fg=GOOD)
            self.run_var.set(f"Run: {self.controller.run_id}")
            self._draw_topology()
            return True
        except Exception as exc:
            self.status_var.set("Network failed to start")
            self.status_badge.configure(bg="#4a1d2a", fg=BAD)
            logging.getLogger(__name__).error("GUI could not start network: %s", exc)
            if show_errors:
                messagebox.showerror("Could not start network", str(exc))
            return False

    def _send_probe(self):
        if self.probe_in_flight:
            return
        message = self.message_var.get()
        if not message:
            messagebox.showerror("Message required", "Enter a message to send.")
            return
        try:
            timeout = float(self.timeout_var.get())
            if timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid timeout", "Timeout must be a positive number.")
            return

        if (self.source_var.get(), self.destination_var.get()) != (
                self.active_source, self.active_destination):
            if not self._restart_network():
                return

        self.probe_in_flight = True
        self.send_button.configure(state="disabled")
        self.result_var.set(f"Sending {message!r}…")
        self.status_var.set("Probe in flight")
        thread = threading.Thread(
            target=self._probe_worker, args=(message, timeout), daemon=True
        )
        thread.start()

    def _probe_worker(self, message, timeout):
        try:
            result = self.controller.probe(message, timeout)
            error = None
        except Exception as exc:
            result = None
            error = exc
        self.after(0, self._probe_finished, message, result, error)

    def _probe_finished(self, message, result, error):
        self.probe_in_flight = False
        self.send_button.configure(state="normal")
        if error is not None:
            self.result_var.set(f"ERROR: {error}")
            self.status_var.set("Probe failed")
            self.status_badge.configure(bg="#4a1d2a", fg=BAD)
        else:
            state, response = result
            if state == "LATE":
                self.result_var.set(f"LATE response: {response!r}")
                self.status_var.set("Late response observed")
                self.status_badge.configure(bg="#49391a", fg=WARN)
            elif state == "TIMEOUT":
                self.result_var.set(f"TIMEOUT for {message!r}")
                self.status_var.set("Probe timed out")
                self.status_badge.configure(bg="#49391a", fg=WARN)
            else:
                self.result_var.set(f"MATCHED response: {response!r}")
                self.status_var.set("Packet matched")
                self.status_badge.configure(bg="#173c3a", fg=GOOD)

    def _normal_preset(self):
        self.observe_late = False
        self.observe_late_var.set(False)
        self.network_data["conditions"] = []
        self._refresh_condition_controls()
        self._restart_network()

    def _drop_preset(self):
        self.observe_late = False
        self.observe_late_var.set(False)
        path = self._compute_path()
        if len(path) < 3:
            messagebox.showinfo("No router path", "The selected endpoints have no usable route.")
            return
        source_router, next_hop = path[1], path[2]
        self.network_data["conditions"] = [{
            "from": source_router,
            "to": next_hop,
            "loss": 1.0,
            "delay_ms": 0,
            "seed": 42,
        }]
        self._refresh_condition_controls()
        self._restart_network()

    def _late_preset(self):
        self.observe_late = True
        self.observe_late_var.set(True)
        destination = self.destination_var.get()
        neighbors = self._neighbors(destination)
        if not neighbors:
            return
        self.network_data["conditions"] = [{
            "from": destination,
            "to": neighbors[0],
            "loss": 0.0,
            "delay_ms": 4000,
            "seed": 42,
        }]
        self._refresh_condition_controls()
        self._restart_network()

    def _reset_all(self):
        if self.probe_in_flight:
            messagebox.showinfo("Probe in progress", "Wait for the current probe to finish.")
            return
        self.network_data = copy.deepcopy(self.base_network)
        self.route_data = copy.deepcopy(self.base_routes)
        self.observe_late = False
        self.observe_late_var.set(False)
        self._refresh_route_controls()
        self._refresh_condition_controls()
        self._restart_network()

    def _set_route(self):
        router = self.route_router_var.get()
        destination = self.route_destination_var.get()
        next_hop = self.route_hop_var.get()
        if not all((router, destination, next_hop)):
            return
        if next_hop not in self._neighbors(router):
            messagebox.showerror("Invalid next hop", f"{next_hop} is not adjacent to {router}.")
            return
        self.route_data.setdefault(router, {})[destination] = next_hop
        self._refresh_route_controls()
        self._restart_network()

    def _refresh_route_controls(self):
        routers = self._router_ids()
        if hasattr(self, "route_router_box"):
            self.route_router_box["values"] = routers
        if not routers:
            return
        if self.route_router_var.get() not in routers:
            self.route_router_var.set(routers[0])
        router = self.route_router_var.get()
        destinations = list(self.route_data.get(router, {}))
        self.route_destination_box["values"] = destinations
        if destinations and self.route_destination_var.get() not in destinations:
            self.route_destination_var.set(destinations[0])
        self._refresh_route_hops()
        if hasattr(self, "route_tree"):
            for row in self.route_tree.get_children():
                self.route_tree.delete(row)
            for current_router in routers:
                for destination, hop in self.route_data.get(current_router, {}).items():
                    self.route_tree.insert("", "end", values=(current_router, destination, hop))

    def _refresh_route_hops(self):
        router = self.route_router_var.get()
        destination = self.route_destination_var.get()
        neighbors = self._neighbors(router)
        self.route_hop_box["values"] = neighbors
        current = self.route_data.get(router, {}).get(destination)
        self.route_hop_var.set(current if current in neighbors else (neighbors[0] if neighbors else ""))

    def _select_route_row(self, _event=None):
        selected = self.route_tree.selection()
        if not selected:
            return
        router, destination, hop = self.route_tree.item(selected[0], "values")
        self.route_router_var.set(router)
        self._refresh_route_controls()
        self.route_destination_var.set(destination)
        self._refresh_route_hops()
        self.route_hop_var.set(hop)

    def _refresh_condition_controls(self):
        devices = list(self.network_data["devices"])
        if hasattr(self, "condition_from_box"):
            self.condition_from_box["values"] = devices
            if self.condition_from_var.get() not in devices:
                self.condition_from_var.set(devices[0] if devices else "")
            self._refresh_condition_to()
        if hasattr(self, "condition_tree"):
            for row in self.condition_tree.get_children():
                self.condition_tree.delete(row)
            for rule in self.network_data.get("conditions", []):
                self.condition_tree.insert("", "end", values=(
                    rule["from"], rule["to"], rule["loss"], rule["delay_ms"], rule["seed"]
                ))

    def _refresh_condition_to(self):
        source = self.condition_from_var.get()
        neighbors = self._neighbors(source)
        self.condition_to_box["values"] = neighbors
        if self.condition_to_var.get() not in neighbors:
            self.condition_to_var.set(neighbors[0] if neighbors else "")

    def _set_condition(self):
        source = self.condition_from_var.get()
        destination = self.condition_to_var.get()
        try:
            loss = float(self.condition_loss_var.get())
            delay_ms = float(self.condition_delay_var.get())
            seed = int(self.condition_seed_var.get())
            if not 0 <= loss <= 1 or delay_ms < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid condition", "Use loss 0–1, nonnegative delay, and integer seed.")
            return
        if destination not in self._neighbors(source):
            messagebox.showerror("Invalid link", "Conditions must target a configured directed link.")
            return
        rule = {"from": source, "to": destination, "loss": loss,
                "delay_ms": delay_ms, "seed": seed}
        conditions = self.network_data.setdefault("conditions", [])
        for index, existing in enumerate(conditions):
            if existing["from"] == source and existing["to"] == destination:
                conditions[index] = rule
                break
        else:
            conditions.append(rule)
        self._refresh_condition_controls()

    def _remove_condition(self):
        selected = self.condition_tree.selection()
        if not selected:
            return
        source, destination, *_ = self.condition_tree.item(selected[0], "values")
        self.network_data["conditions"] = [
            rule for rule in self.network_data.get("conditions", [])
            if not (rule["from"] == source and rule["to"] == destination)
        ]
        self._refresh_condition_controls()

    def _select_condition_row(self, _event=None):
        selected = self.condition_tree.selection()
        if not selected:
            return
        source, destination, loss, delay_ms, seed = self.condition_tree.item(selected[0], "values")
        self.condition_from_var.set(source)
        self._refresh_condition_to()
        self.condition_to_var.set(destination)
        self.condition_loss_var.set(loss)
        self.condition_delay_var.set(delay_ms)
        self.condition_seed_var.set(seed)

    def _neighbors(self, device_id):
        return [
            link["b"] if link["a"] == device_id else link["a"]
            for link in self.network_data["links"]
            if device_id in (link["a"], link["b"])
        ]

    def _compute_path(self):
        source = self.source_var.get()
        destination = self.destination_var.get()
        if not source or not destination:
            return []
        path = [source]
        current = source
        visited = {source}
        for _ in range(len(self.network_data["devices"]) + 1):
            if current == destination:
                return path
            if self.network_data["devices"].get(current, {}).get("type") == "host":
                next_hops = self._neighbors(current)
                next_hop = next_hops[0] if next_hops else None
            else:
                next_hop = self.route_data.get(current, {}).get(destination)
            if not next_hop or next_hop in visited:
                return path
            path.append(next_hop)
            visited.add(next_hop)
            current = next_hop
        return path

    def _draw_topology(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        actual_width = self.canvas.winfo_width()
        actual_height = self.canvas.winfo_height()
        width = (actual_width if actual_width > 50 else
                 max(480, self.target_width - self.sidebar_width - 60))
        height = (actual_height if actual_height > 50 else
                  max(260, self.target_height - 350))
        center_y = height / 2
        horizontal_margin = max(38, min(70, width * 0.08))
        left = horizontal_margin
        right = max(left + 1, width - horizontal_margin)
        span = right - left
        self.node_positions = {
            "H1": (left, center_y),
            "A": (left + span * 0.22, center_y),
            "B": (left + span * 0.44, height * 0.27),
            "C": (left + span * 0.44, height * 0.73),
            "D": (left + span * 0.68, center_y),
            "H2": (right, center_y),
        }
        path = self._compute_path()
        path_pairs = {frozenset((path[i], path[i + 1])) for i in range(len(path) - 1)}
        self.path_var.set("Path: " + (" → ".join(path) if path else "no route"))

        for link in self.network_data["links"]:
            first, second = link["a"], link["b"]
            if first not in self.node_positions or second not in self.node_positions:
                continue
            x1, y1 = self.node_positions[first]
            x2, y2 = self.node_positions[second]
            active = frozenset((first, second)) in path_pairs
            item = self.canvas.create_line(
                x1, y1, x2, y2, fill=ACCENT if active else GRID,
                width=4 if active else 2, capstyle="round"
            )
            self.link_items.append(item)
            for rule in self.network_data.get("conditions", []):
                if rule["from"] == first and rule["to"] == second:
                    label = f"→ {rule['loss']:.0%} loss / +{rule['delay_ms']:g}ms"
                    self.canvas.create_text(
                        (x1 + x2) / 2, (y1 + y2) / 2 - 13,
                        text=label, fill=BAD if rule["loss"] else WARN,
                        font=("Segoe UI", 8, "bold")
                    )

        self.node_items = {}
        for device_id, (x, y) in self.node_positions.items():
            if device_id not in self.network_data["devices"]:
                continue
            is_host = self.network_data["devices"][device_id]["type"] == "host"
            fill = "#12324a" if is_host else "#252149"
            outline = ACCENT if is_host else ACCENT_2
            radius = 27 if is_host else 24
            oval = self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=fill, outline=outline, width=2
            )
            self.node_items[device_id] = oval
            self.canvas.create_text(x, y - 1, text=device_id, fill=TEXT,
                                    font=("Segoe UI", 13, "bold"))
            label = "HOST" if is_host else "ROUTER"
            self.canvas.create_text(x, y + radius + 15, text=label, fill=MUTED,
                                    font=("Segoe UI", 8, "bold"))

        self.canvas.create_text(
            18, 18, anchor="nw", text="Blue = active route   •   Purple = router   •   packet motion = real UDP event",
            fill=MUTED, font=("Segoe UI", 9)
        )

    def _drain_records(self):
        try:
            while True:
                record = self.records.get_nowait()
                message = record.getMessage()
                self._append_log(self.log_handler.format(record))
                fields = parse_event(message)
                event_name = fields.get("event")
                if event_name == "send":
                    self.packet_count_var.set(f"{self._increment_stat(self.packet_count_var.get())} sent")
                    self._animate_packet(fields)
                elif event_name == "drop":
                    self.drop_count_var.set(f"{self._increment_stat(self.drop_count_var.get())} dropped")
                    self._flash_device(fields.get("device"))
                elif event_name == "late_response":
                    self.status_var.set("Late response observed")
                    self.status_badge.configure(bg="#49391a", fg=WARN)
        except queue.Empty:
            pass
        self.after(75, self._drain_records)

    @staticmethod
    def _increment_stat(value):
        try:
            return int(value.split()[0]) + 1
        except (ValueError, IndexError):
            return 1

    def _animate_packet(self, fields):
        source = fields.get("device")
        destination = fields.get("next_hop")
        if source not in self.node_positions or destination not in self.node_positions:
            return
        x1, y1 = self.node_positions[source]
        x2, y2 = self.node_positions[destination]
        color = ACCENT if fields.get("type") == "DATA" else GOOD
        packet = self.canvas.create_oval(
            x1 - 8, y1 - 8, x1 + 8, y1 + 8,
            fill=color, outline="#ffffff", width=1
        )

        def move(step=0):
            if step > 20:
                try:
                    self.canvas.delete(packet)
                except tk.TclError:
                    pass
                return
            fraction = step / 20
            try:
                self.canvas.coords(
                    packet,
                    x1 + (x2 - x1) * fraction - 8,
                    y1 + (y2 - y1) * fraction - 8,
                    x1 + (x2 - x1) * fraction + 8,
                    y1 + (y2 - y1) * fraction + 8,
                )
                self.after(30, move, step + 1)
            except tk.TclError:
                return

        move()

    def _flash_device(self, device_id):
        item = self.node_items.get(device_id)
        if item is None:
            return
        try:
            self.canvas.itemconfigure(item, outline=BAD, width=4)
            self.after(450, lambda: self.canvas.itemconfigure(
                item, outline=ACCENT if self.network_data["devices"].get(device_id, {}).get("type") == "host" else ACCENT_2,
                width=2
            ))
        except tk.TclError:
            pass

    def _append_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_lines += 1
        if self.log_lines > 700:
            self.log_text.delete("1.0", "80.0")
            self.log_lines -= 80
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log_lines = 0

    def _on_close(self):
        if self.probe_in_flight:
            if not messagebox.askyesno("Probe in progress", "Stop the demo and close the GUI?"):
                return
        self.controller.stop()
        logging.getLogger().removeHandler(self.log_handler)
        self.destroy()


if __name__ == "__main__":
    app = NetworkGUI()
    app.mainloop()
