"""Headless tests for the queued packet animation pipeline.

A fake canvas and a manual scheduler stand in for Tk: nothing sleeps, no
window or sockets are used, and "time" only advances when a test says so.
"""
import tkinter as tk
import unittest

import gui
from gui import PacketAnimator

POSITIONS = {
    "H1": (0, 0), "A": (100, 0), "B": (200, 0), "D": (300, 0), "H2": (400, 0),
}


class FakeCanvas:
    def __init__(self):
        self.items = {}   # id -> {"kind", "options", "coords"}
        self.created = []  # (id, kind, options) in creation order
        self.deleted = []
        self.next_id = 1
        self.fail = False

    def _check(self):
        if self.fail:
            raise tk.TclError("application has been destroyed")

    def _create(self, kind, coords, options):
        self._check()
        item = self.next_id
        self.next_id += 1
        self.items[item] = {"kind": kind, "options": dict(options), "coords": coords}
        self.created.append((item, kind, dict(options)))
        return item

    def create_oval(self, *coords, **options):
        return self._create("oval", coords, options)

    def create_line(self, *coords, **options):
        return self._create("line", coords, options)

    def create_text(self, *coords, **options):
        return self._create("text", coords, options)

    def coords(self, item, *coords):
        self._check()
        if item in self.items:  # real Tk ignores ids that no longer exist
            self.items[item]["coords"] = coords

    def itemconfigure(self, item, **options):
        self._check()
        if item in self.items:
            self.items[item]["options"].update(options)

    def delete(self, item):
        self._check()
        self.deleted.append(item)
        self.items.pop(item, None)


class ManualScheduler:
    def __init__(self):
        self.pending = []

    def after(self, ms, callback, *args):
        self.pending.append((ms, callback, args))

    def run_one(self):
        _, callback, args = self.pending.pop(0)
        callback(*args)

    def run_all(self, limit=100000):
        count = 0
        while self.pending:
            self.run_one()
            count += 1
            self.assertion_guard(count, limit)

    @staticmethod
    def assertion_guard(count, limit):
        if count > limit:
            raise AssertionError("animation never finished")


def send(device, next_hop, type_="DATA"):
    return {"event": "send", "device": device, "next_hop": next_hop, "type": type_}


class AnimatorTestCase(unittest.TestCase):
    def setUp(self):
        self.canvas = FakeCanvas()
        self.scheduler = ManualScheduler()
        self.positions = dict(POSITIONS)
        self.animator = PacketAnimator(
            self.canvas, self.scheduler.after, lambda: self.positions
        )

    def shapes(self):
        return [(kind, options.get("fill")) for _, kind, options in self.canvas.created]


class FifoTests(AnimatorTestCase):
    def test_same_link_packets_never_overlap_and_run_in_order(self):
        first = send("H1", "A", "DATA")
        second = send("H1", "A", "FIN")  # non-DATA keeps the GOOD color
        third = send("H1", "A", "DATA")
        for fields in (first, second, third):
            self.assertTrue(self.animator.handle(fields))

        # Only the head is on screen; the rest wait in FIFO order.
        self.assertEqual(len(self.canvas.items), 1)
        self.assertEqual([j.packet_type for j in self.animator.queues[("H1", "A")]],
                         ["FIN", "DATA"])

        on_screen_max = 0
        while self.scheduler.pending:
            self.scheduler.run_one()
            on_screen_max = max(on_screen_max, len(self.canvas.items))
        self.assertEqual(on_screen_max, 1)
        self.assertEqual([f for _, f in self.shapes()], [gui.ACCENT, gui.GOOD, gui.ACCENT])
        self.assertEqual(self.canvas.items, {})  # everything cleaned up
        self.assertEqual(self.animator.queues, {})
        self.assertEqual(self.animator.active, set())

    def test_packet_travels_from_source_to_next_hop(self):
        self.animator.handle(send("A", "B"))
        item = self.canvas.created[0][0]
        self.assertEqual(self.canvas.items[item]["coords"], (92, -8, 108, 8))
        self.scheduler.run_all()
        # Final frame (before deletion) sat on the destination node.
        self.assertEqual(self.canvas.deleted, [item])

    def test_different_links_animate_independently(self):
        self.animator.handle(send("H1", "A"))
        self.animator.handle(send("B", "D"))
        self.animator.handle(send("H1", "A"))  # waits behind the first only
        self.assertEqual(len(self.canvas.items), 2)  # both links active at once
        self.assertEqual(self.animator.active, {("H1", "A"), ("B", "D")})
        self.assertEqual(len(self.animator.queues[("H1", "A")]), 1)
        self.assertNotIn(("B", "D"), [k for k, q in self.animator.queues.items() if q])

    def test_direction_is_a_separate_queue(self):
        self.animator.handle(send("H1", "A"))
        self.animator.handle(send("A", "H1"))
        self.assertEqual(len(self.canvas.items), 2)

    def test_unknown_endpoints_are_ignored(self):
        self.assertFalse(self.animator.handle(send("H1", "-")))
        self.assertFalse(self.animator.handle(send("X", "A")))
        self.assertEqual(self.canvas.created, [])


class DelayAndDropTests(AnimatorTestCase):
    def test_delay_marks_the_link_midpoint_for_a_bounded_time(self):
        self.assertTrue(self.animator.handle(
            {"event": "delay", "device": "H1", "next_hop": "A", "delay_ms": "50.0"}))
        item, kind, options = self.canvas.created[0]
        self.assertEqual((kind, options["fill"]), ("oval", gui.WARN))
        self.assertEqual(self.canvas.items[item]["coords"], (42, -8, 58, 8))  # midpoint

        frames = 0
        while self.scheduler.pending:
            self.scheduler.run_one()
            frames += 1
        self.assertEqual(frames, PacketAnimator.DELAY_MIN_STEPS + 1)
        self.assertEqual(self.canvas.items, {})

    def test_huge_delay_is_capped_and_never_waited_for(self):
        self.animator.handle(
            {"event": "delay", "device": "H1", "next_hop": "A", "delay_ms": "60000"})
        frames = 0
        while self.scheduler.pending:
            self.scheduler.run_one()
            frames += 1
        self.assertEqual(frames, PacketAnimator.DELAY_MAX_STEPS + 1)

    def test_bad_delay_value_still_gives_a_short_indicator(self):
        self.assertTrue(self.animator.handle(
            {"event": "delay", "device": "H1", "next_hop": "A", "delay_ms": "oops"}))
        self.scheduler.run_all()
        self.assertEqual(self.canvas.items, {})

    def test_drop_flashes_link_red_without_a_moving_packet(self):
        self.assertTrue(self.animator.handle(
            {"event": "drop", "device": "A", "next_hop": "B", "reason": "injected_loss"}))
        item, kind, options = self.canvas.created[0]
        self.assertEqual((kind, options["fill"]), ("line", gui.BAD))
        self.assertEqual(self.canvas.items[item]["coords"], (100, 0, 200, 0))
        self.scheduler.run_all()
        self.assertEqual([k for _, k, _ in self.canvas.created], ["line"])  # no oval
        self.assertEqual(self.canvas.items, {})

    def test_drop_without_a_link_falls_back_to_device_flash(self):
        self.assertFalse(self.animator.handle(
            {"event": "drop", "device": "A", "next_hop": "-", "reason": "no_route"}))
        self.assertFalse(self.animator.handle({"event": "drop", "device": "A"}))
        self.assertEqual(self.canvas.created, [])

    def test_delay_and_drop_share_the_links_fifo_with_sends(self):
        self.animator.handle(send("H1", "A"))
        self.animator.handle(
            {"event": "delay", "device": "H1", "next_hop": "A", "delay_ms": "100"})
        self.animator.handle({"event": "drop", "device": "H1", "next_hop": "A"})
        self.scheduler.run_all()
        self.assertEqual([k for _, k, _ in self.canvas.created], ["oval", "oval", "line"])
        self.assertEqual([o.get("fill") for _, _, o in self.canvas.created],
                         [gui.ACCENT, gui.WARN, gui.BAD])

    def test_other_events_are_ignored(self):
        self.assertFalse(self.animator.handle(
            {"event": "receive", "device": "A", "next_hop": "B"}))
        self.assertEqual(self.canvas.created, [])


class BoundsTests(AnimatorTestCase):
    def test_per_link_queue_is_bounded_and_discards_oldest_visuals(self):
        extra = 25
        for _ in range(PacketAnimator.MAX_PER_LINK + extra + 1):  # +1 goes on screen
            self.animator.handle(send("H1", "A"))
        queued = self.animator.queues[("H1", "A")]
        self.assertEqual(len(queued), PacketAnimator.MAX_PER_LINK)
        self.assertEqual(self.animator.discarded, extra)
        self.assertEqual(self.animator.total, PacketAnimator.MAX_PER_LINK)

    def test_discard_keeps_newest_entries(self):
        for index in range(PacketAnimator.MAX_PER_LINK + 5):
            self.animator.handle(send("H1", "A", f"T{index}"))
        types = [j.packet_type for j in self.animator.queues[("H1", "A")]]
        self.assertEqual(types[-1], f"T{PacketAnimator.MAX_PER_LINK + 4}")
        self.assertEqual(types, sorted(types, key=lambda t: int(t[1:])))  # still FIFO

    def test_total_queue_memory_is_bounded_across_links(self):
        hops = list(POSITIONS)
        links = [(a, b) for a in hops for b in hops if a != b]
        for _ in range(PacketAnimator.MAX_PER_LINK):
            for a, b in links:
                self.animator.handle(send(a, b))
        self.assertLessEqual(self.animator.total, PacketAnimator.MAX_TOTAL)
        self.assertEqual(
            self.animator.total,
            sum(len(q) for q in self.animator.queues.values()),
        )
        self.assertGreater(self.animator.discarded, 0)

    def test_a_bounded_backlog_drains_completely(self):
        for _ in range(PacketAnimator.MAX_PER_LINK * 3):
            self.animator.handle(send("H1", "A"))
        self.scheduler.run_all()
        self.assertEqual((self.animator.total, self.animator.active, self.canvas.items),
                         (0, set(), {}))


class SafetyTests(AnimatorTestCase):
    def test_closed_canvas_stops_the_animation_quietly(self):
        self.animator.handle(send("H1", "A"))
        self.animator.handle(send("H1", "A"))
        self.canvas.fail = True  # window destroyed mid-animation
        self.scheduler.run_all()  # must not raise
        self.assertTrue(self.animator.closed)
        self.assertEqual(self.scheduler.pending, [])

    def test_close_makes_pending_callbacks_noops_and_ignores_new_events(self):
        self.animator.handle(send("H1", "A"))
        self.animator.close()
        created = len(self.canvas.created)
        self.scheduler.run_all()
        self.assertEqual(len(self.canvas.created), created)
        self.assertFalse(self.animator.handle(send("H1", "A")))

    def test_redrawn_canvas_does_not_break_in_flight_animations(self):
        self.animator.handle(send("H1", "A"))
        self.animator.handle(send("H1", "A"))
        self.canvas.items.clear()  # what canvas.delete("all") does
        self.scheduler.run_all()
        self.assertEqual(self.animator.active, set())

    def test_layout_change_skips_queued_jobs_whose_nodes_vanished(self):
        self.animator.handle(send("H1", "A"))
        self.animator.handle(send("H1", "A"))
        del self.positions["A"]
        self.scheduler.run_all()
        self.assertEqual(len(self.canvas.created), 1)  # second job skipped
        self.assertEqual((self.animator.active, self.animator.queues), (set(), {}))


def ev(name, device, next_hop, type_="DATA", seq="1", msg_id="5", **extra):
    """A parsed log event as NetworkGUI hands it to the animator."""
    return {"event": name, "device": device, "next_hop": next_hop,
            "type": type_, "seq": seq, "msg_id": msg_id, **extra}


class ReliableVisualTests(AnimatorTestCase):
    def ovals(self):
        return [(i, o) for i, kind, o in self.canvas.created if kind == "oval"]

    def texts(self):
        return [(i, o) for i, kind, o in self.canvas.created if kind == "text"]

    def test_data_ack_and_retransmission_look_different(self):
        self.animator.handle(ev("send", "H1", "A", "DATA"))
        self.scheduler.run_all()
        self.animator.handle(ev("send", "A", "H1", "ACK"))
        self.scheduler.run_all()
        self.animator.handle(ev("retransmit", "H1", "A", "DATA"))
        self.animator.handle(ev("send", "H1", "A", "DATA"))
        self.scheduler.run_all()

        (_, data), (_, ack), (_, retransmission) = self.ovals()
        self.assertEqual(data["fill"], gui.ACCENT)
        self.assertEqual(ack["fill"], gui.ACK)
        self.assertEqual((retransmission["fill"], retransmission["outline"]),
                         (gui.RETRANSMIT, gui.BAD))
        self.assertEqual(len({data["fill"], ack["fill"], retransmission["fill"]}), 3)

    def test_fin_keeps_the_existing_non_data_styling(self):
        self.animator.handle(ev("send", "H1", "A", "FIN", seq="3"))
        self.assertEqual(self.ovals()[0][1]["fill"], gui.GOOD)

    def test_sequence_label_follows_the_packet_and_is_cleaned_up(self):
        self.animator.handle(ev("send", "H1", "A", "DATA", seq="7"))
        (oval, _), (label, options) = self.ovals()[0], self.texts()[0]
        self.assertEqual(options["text"], "7")
        for _ in range(PacketAnimator.MOVE_STEPS // 2):
            self.scheduler.run_one()
        left, top, right, bottom = self.canvas.items[oval]["coords"]
        label_x, label_y = self.canvas.items[label]["coords"]
        self.assertEqual((label_x, label_y), ((left + right) / 2, (top + bottom) / 2 - 16))
        self.assertGreater(label_x, 0)  # it moved off the source node
        self.scheduler.run_all()
        self.assertEqual(self.canvas.items, {})
        self.assertEqual(sorted(self.canvas.deleted), sorted([oval, label]))

    def test_packets_without_a_numeric_seq_get_no_label(self):
        for seq in ("-", None, "x"):
            fields = ev("send", "H1", "A", "DATA")
            fields["seq"] = seq
            self.animator.handle(fields)
            self.scheduler.run_all()
        self.assertEqual(self.texts(), [])
        self.assertEqual(len(self.ovals()), 3)

    def test_retransmit_event_alone_draws_nothing(self):
        self.assertFalse(self.animator.handle(ev("retransmit", "H1", "A")))
        self.assertEqual(self.canvas.created, [])
        self.assertEqual(len(self.animator.pending_retransmits), 1)

    def test_retransmit_is_correlated_with_its_matching_send(self):
        self.animator.handle(ev("retransmit", "H1", "A", seq="1", retries="1"))
        self.animator.handle(ev("send", "H1", "A", seq="1"))
        self.scheduler.run_all()
        self.assertEqual(len(self.ovals()), 1)  # one moving packet, not two
        self.assertEqual(self.ovals()[0][1]["fill"], gui.RETRANSMIT)
        self.assertEqual(len(self.animator.pending_retransmits), 0)
        # The marker is consumed: the next identical send is an ordinary packet.
        self.animator.handle(ev("send", "H1", "A", seq="1"))
        self.assertEqual(self.ovals()[1][1]["fill"], gui.ACCENT)

    def test_unrelated_sends_do_not_consume_the_retransmit_marker(self):
        self.animator.handle(ev("retransmit", "H1", "A", seq="1"))
        self.animator.handle(ev("send", "H1", "A", seq="2"))        # other seq
        self.animator.handle(ev("send", "B", "D", seq="1"))         # other device
        self.animator.handle(ev("send", "H1", "A", "DATA", seq="1", msg_id="9"))
        self.assertEqual({o["fill"] for _, o in self.ovals()}, {gui.ACCENT})
        self.assertEqual(len(self.animator.pending_retransmits), 1)

    def test_dropped_retransmission_clears_the_marker_and_shows_the_drop(self):
        self.animator.handle(ev("retransmit", "H1", "A", seq="1"))
        self.assertTrue(self.animator.handle(
            ev("drop", "H1", "A", seq="1", reason="injected_loss")))
        self.assertEqual(len(self.animator.pending_retransmits), 0)
        self.assertEqual(self.ovals(), [])  # nothing moved
        self.assertEqual([k for _, k, _ in self.canvas.created], ["line"])
        self.scheduler.run_all()
        # A later send of the same packet is not mistaken for a retransmission.
        self.animator.handle(ev("send", "H1", "A", seq="1"))
        self.assertEqual(self.ovals()[0][1]["fill"], gui.ACCENT)

    def test_drop_only_clears_the_marker_for_the_same_packet_and_link(self):
        self.animator.handle(ev("retransmit", "H1", "A", seq="1"))
        self.assertFalse(self.animator.handle(ev("drop", "H1", "-", seq="1")))
        self.assertEqual(len(self.animator.pending_retransmits), 1)  # other link
        self.assertTrue(self.animator.handle(ev("drop", "H1", "A", seq="1")))
        self.assertEqual(len(self.animator.pending_retransmits), 0)

    def test_duplicate_ack_gets_a_double_ring_in_addition_to_the_ack(self):
        self.animator.handle(ev("duplicate", "H2", "D", "DATA", seq="2"))
        self.assertEqual(self.canvas.created, [])  # marker only
        self.animator.handle(ev("send", "H2", "D", "ACK", seq="2"))
        ovals = self.ovals()
        self.assertEqual(len(ovals), 3)  # the ACK itself plus two rings
        self.assertEqual(ovals[0][1]["fill"], gui.ACK)
        self.assertEqual([o["outline"] for _, o in ovals[1:]], [gui.ACK, gui.ACK])
        self.assertTrue(all("fill" not in o for _, o in ovals[1:]))  # hollow rings
        self.assertEqual(self.texts()[0][1]["text"], "2")
        self.scheduler.run_all()
        self.assertEqual(self.canvas.items, {})  # rings and label cleaned up too
        self.assertEqual(len(self.animator.pending_duplicates), 0)

    def test_normal_ack_has_no_rings(self):
        self.animator.handle(ev("send", "H2", "D", "ACK", seq="2"))
        self.assertEqual(len(self.ovals()), 1)

    def test_dropped_duplicate_ack_leaves_no_stale_marker(self):
        self.animator.handle(ev("duplicate", "H2", "D", "DATA", seq="2"))
        self.animator.handle(ev("drop", "H2", "D", "ACK", seq="2"))
        self.assertEqual(len(self.animator.pending_duplicates), 0)
        self.scheduler.run_all()  # let the drop flash finish first (same link FIFO)
        self.animator.handle(ev("send", "H2", "D", "ACK", seq="2"))
        self.assertEqual(len(self.ovals()), 1)  # the ACK only, no rings

    def test_unmatched_markers_are_bounded_and_cleared_on_close(self):
        for index in range(PacketAnimator.MAX_PENDING + 10):
            self.animator.handle(ev("retransmit", "H1", "A", seq=str(index)))
            self.animator.handle(ev("duplicate", "H2", "D", "DATA", seq=str(index)))
        self.assertEqual(len(self.animator.pending_retransmits), PacketAnimator.MAX_PENDING)
        self.assertEqual(len(self.animator.pending_duplicates), PacketAnimator.MAX_PENDING)
        self.animator.close()
        self.assertEqual(
            (len(self.animator.pending_retransmits), len(self.animator.pending_duplicates)),
            (0, 0))

    def test_labelled_packets_on_one_link_still_never_overlap(self):
        for seq in range(5):
            self.animator.handle(ev("send", "H1", "A", seq=str(seq)))
        on_screen = 0
        while self.scheduler.pending:
            self.scheduler.run_one()
            on_screen = max(on_screen, len(self.canvas.items))
        self.assertEqual(on_screen, 2)  # one packet shape + its label at a time
        self.assertEqual([o["text"] for _, o in self.texts()], list("01234"))

    def test_label_is_removed_with_the_packet_when_the_canvas_dies(self):
        self.animator.handle(ev("send", "H1", "A", seq="1"))
        self.canvas.fail = True
        self.scheduler.run_all()  # TclError is absorbed
        self.assertTrue(self.animator.closed)


if __name__ == "__main__":
    unittest.main()
