# Mini Internet

CSCI 4/5500: Computer Networks — Fall 2026


Format: Instructor-assigned teams of up to three students; eight weeks of development.
Project launch: Wednesday, September 23, 2026.
Course weight: 30% of the course grade.
Graded deliverables: Progress report and working core demonstration; final presentation; final report, runnable source code, and experimental evidence.

# 1 Project overview

Build your own mini-internet using Python 3 and UDP sockets. First, create a working virtual network in which hosts exchange messages through several routers. Then use that network as a platform for one advanced topic: routing, reliable transfer, or congestion control. The project has two phases:

1. Build the common network. Design virtual addresses and packets, configure neighbor connections, implement forwarding with static tables, and demonstrate communication across multiple routers.

2. Extend the network. Choose one topic, implement it on your working network, and evaluate its behavior through controlled experiments.

The common network carries short messages and provides best-effort delivery. Automatic route computation, reliable transfer, and congestion control belong to the extension tracks. Short messages or generated data are sufficient workloads; a file-transfer application is OPTIONAL.

Required tools: Python 3, UDP sockets, Git, and Wireshark. Write your own framework; do not use ns-3. UDP provides communication between processes, while your code implements the virtual network. You are not implementing the physical Internet or a complete Internet protocol stack.

### Learning objectives

By completing this project, you should be able to:
- Explain virtual addresses, packet headers, payloads, and encapsulation.
- Distinguish forwarding from routing and use forwarding tables to move packets across multiple hops.
- Build and debug communication among independent host and router processes.
- Use logs and packet captures to explain packet paths, delay, and loss.
- Develop one advanced networking feature and evaluate it against a clear baseline.

# 2 Network architecture and scope

Use at least two end hosts and four routers. The topology below is the common starting point; it contains two possible paths between Routers A and D. Links are bidirectional. Groups may add hosts or links for their extension after the common network works.

```text
                   Router B
                 /          \
Host 1 -- Router A         Router D -- Host 2
                 \          /
                   Router C
```

All six processes may run on one laptop using distinct localhost UDP ports. Running across multiple computers is optional and earns no automatic extra credit. A configuration file must define virtual addresses, actual UDP endpoints, and neighbor relationships. A virtual address identifies a device in your mini-internet; a UDP endpoint identifies the process that implements that device.

A process may send only to a configured neighbor. Hosts must communicate through the routers, not directly through each other’s actual UDP ports. Routers select a next hop using the packet’s virtual destination address and then map that neighbor to its UDP endpoint.

Core scope: Fixed virtual addresses, manually configured forwarding tables, and short messages that fit in one packet. Keep every serialized UDP payload at or below 1,200 bytes, including your custom headers. No fragmentation, file transfer, reliability protocol, automatic routing algorithm, flow control, congestion control, DNS, NAT, subnetting, authentication, or encryption is required in the common core.

# 3 Phase 1: build the common mini-internet

## 3.1 Processes, configuration, and startup

Create host and router programs, a topology configuration, and documented startup commands or a launcher. Keep device addresses and neighbor mappings in configuration rather than hard-coding one path into the source. Detect invalid configuration and report startup errors clearly. Each group designs its own modules and interfaces.

## 3.2 Packet format and parsing

Define a packet format with at least a virtual source address, virtual destination address, hop limit, message type, message identifier, and payload. Document field meanings, sizes or encoding, and how packets are parsed. Reject malformed or oversized packets without crashing the process. A message identifier lets you match a request, its response, and log records; it does not by itself provide reliable delivery.

## 3.3 Static forwarding and hop limits

Each router must use a configurable table that maps destination addresses to next-hop neighbors. Configure both forward and return paths. Initially, write these tables by hand; no shortest-path algorithm is required in this phase.

Define and document a consistent hop-limit rule. For example, a router discards a packet whose incoming hop limit is at most one; otherwise it decrements the limit and forwards the packet. Hosts accept packets addressed to themselves. Log packets discarded because of an exhausted hop limit, an unknown destination, or an invalid next hop. A router forwards application payloads without interpreting their contents.

Demonstrate the upper path through Router B and the lower path through Router C by changing the static tables between runs. A link failure need not trigger automatic recovery in the core. It should result in missing replies and an explicit timeout at the source rather than a program that waits forever.

## 3.4 Basic host communication

Implement a simple probe/echo exchange: one host sends a short message and the destination returns a response carrying the same message identifier and payload. A response is a separate packet that travels through the virtual network. Demonstrate exchanges initiated by either host.

Use a finite response timeout and report whether each probe received a matching response. The baseline sends each probe once and does not retransmit it. Under loss, missing replies are an expected outcome to measure. Reliable delivery is reserved for the reliable-transfer extension.

## 3.5 Network conditions and logs

Provide configurable packet loss and added delivery delay on a selected directed link or a clearly identified bidirectional link pair. Apply these conditions consistently to all packet types on that link. Record random seeds and the location of injected conditions. A scheduled queue or equivalent mechanism should delay delivery without blocking unrelated message handling. A simple fixed-rate link model is required only for the congestion-control track; a waiting queue is optional.

Logs must identify the run, device, timestamp, message identifier, message type, virtual source and destination, next hop, and relevant events such as send, receive, forward, drop, and timeout. Include the hop limit when forwarding or dropping a packet. Use logs to trace one request and its response across the complete path.

## 3.6 Core checkpoint

Before integrating the advanced feature, save a repository commit containing a working common network. Demonstrate the acceptance scenarios in Section 6. Keep this baseline runnable so you can compare it with your extension. The progress report should document this checkpoint and the final extension plan.

# 4 Phase 2: choose one advanced topic

Identify a tentative topic in the ungraded Week 1 check-in and confirm its scope in the progress report. Complete the common network before integrating the extension. Each track below can earn full credit. The required scope is one main networking mechanism, a small set of correctness tests, and one baseline comparison. Optional enhancements can earn up to 10 bonus points on the final report/code/evidence component, as described in the bonus rubric. They are not necessary to earn full credit on the required work. Additional features do not replace the required core or careful evaluation. An alternative topic may be proposed with a comparable implementation scope, acceptance tests, and measurable comparison.

## 4.1 Track A: routing and route adaptation

Replace manually written forwarding tables with tables computed by your own implementation of Dijkstra’s algorithm. Use positive, configurable link costs, a deterministic rule for equal-cost paths, and explicit handling of unreachable destinations. Do not call a library shortest-path routine.

For this track, all routers may read a shared topology description. Implement a reproducible router-to-router link failure, update the topology, and recompute forwarding tables. A controller or shared event file may notify routers; distributed topology discovery, failure detection, and link-state flooding are optional enhancements. Disabling a link must prevent actual sends over that virtual link, not merely remove an edge from a graph.

Required evidence: Check paths and next hops against hand calculations for at least two cost configurations. Use repeated probes to show the path before and after a failure, recovery when an alternate path exists, and explicit timeouts when no path remains. Report probe success rate and the time from failure injection to the first successful response over the alternate path. State the probe interval and topology-notification mechanism, which affect this measurement.

Comparison: Static forwarding versus automatic route adaptation under the same failure event. Reliable transport is not required; packet loss during route changes is measured rather than hidden.

## 4.2 Track B: reliable transfer

Implement stop-and-wait delivery of a finite sequence of numbered messages over the common network. The sender keeps at most one unacknowledged message outstanding. Use acknowledgments, a configurable timeout, retransmission, and duplicate detection. The receiver delivers each message once and in order, and acknowledges duplicates so a lost acknowledgment does not prevent progress. Generated messages are sufficient; a file-transfer application is optional.

Use a run identifier and sequence numbers to distinguish messages and acknowledgments. Document the sender and receiver states. Keep the receiver available for a documented, bounded interval after the last message so it can acknowledge a retransmitted final message. Set a retry limit so an unreachable destination produces explicit failure. No sliding window, receiver-advertised flow control, custom checksum, or corruption injection is required. Assume packets may be lost but their contents are not corrupted.

Required evidence: Show correct ordered delivery without duplicates when loss is disabled, recovery from one deliberately dropped data message, and recovery from one deliberately dropped acknowledgment. Use the final message’s acknowledgment for the latter test to exercise completion behavior. Also show that an unreachable destination causes bounded failure rather than an endless wait.

Comparison: Compare best-effort sending with stop-and-wait under 0% and 5% random loss on one documented link or link pair. Use the same finite message set, path, delay, and a documented maximum sending rate; explain that stop-and-wait may send more slowly while waiting for acknowledgments. Repeat each condition at least three times. Report the fraction of unique messages delivered, whether the complete sequence arrived, retransmissions, and elapsed time. For best-effort runs, use a fixed observation timeout rather than waiting indefinitely for missing messages. Explain the reliability and delay tradeoff.

Optional enhancements: Sliding-window transfer, receiver-advertised flow control, or checksum-based corruption detection. These are not required for full credit.

## 4.3 Track C: simple congestion control

Implement one sender that adapts its packet sending rate using a simple additive-increase/multiplicative-decrease (AIMD) rule. Reuse the core’s numbered probe/echo messages for receiver feedback. Compare this sender with a fixed-rate sender. Reliable delivery, retransmission, receiver flow control, multiple competing flows, and a full TCP implementation are not required.

Simple bottleneck model: Give one directed router-to-router link a fixed transmission rate R bits/s. It can transmit one packet at a time and has no waiting buffer. When idle, it accepts a packet of serialized length L bits and stays busy for L/R seconds. A packet arriving while the link is busy is dropped and logged as an overload drop. At transmission completion, the accepted packet continues through the network with the configured propagation delay. Keep other links and the receiver fast enough that they do not limit the flow; leave the return path uncongested.

This is a simplified zero-waiting-buffer model of a capacity-limited link. It creates traffic-dependent loss without requiring FIFO queue management. Disable injected random loss for this track’s main experiments so the overload signal has a clear cause. There is no requirement to change link capacity during a run or measure queue occupancy.

Controller: Pace outgoing probes at a rate r packets/s. Use matching echo replies and a response timeout to detect successful delivery or possible overload. At a fixed control interval, increase the rate additively after successful feedback with no newly detected timeouts, or decrease it multiplicatively after a timeout. Set minimum and maximum rates. If feedback stops, do not keep increasing the rate; back off or stop after a documented limit. The sender must use its received replies and timers, not inspect the router’s internal state.

Required evidence: Show successful delivery below the bottleneck capacity, overload drops above capacity, and an AIMD rate reduction following a congestion signal. Show that the sender resumes gradual increases when successful feedback returns and behaves safely when the receiver stops replying. A fixed bottleneck capacity and one flow are sufficient.

Comparison: Run the fixed-rate and AIMD senders separately with the same packet size, path, bottleneck rate, propagation delay, and run duration. Choose a fixed sending rate above the bottleneck capacity and start AIMD below it. Repeat each mode at least three times. Report packet delivery ratio, useful-data goodput, overload-drop count, and sender rate over time. Count only unique received payload bytes as useful data. Explain the tradeoff between delivered throughput and loss; AIMD need not outperform the fixed sender on every metric.

### Implementation hints for congestion control

1. Reuse the core. Keep the existing probe identifiers, echo replies, logs, delay scheduler, and static routes. Add only link-busy state, sender pacing, and rate updates. A scheduled transmission completion must not block the router’s receive loop. Propagation delay does not keep the transmitter busy after serialization ends.

2. Start with simple numbers. With 1,000-byte serialized packets and R = 800 kbps, transmission takes 10 ms and the maximum link service rate is 100 packets/s. A fixed sender at 120 packets/s exceeds capacity. Start AIMD at 20 packets/s. Actual delivered rates depend on pacing and timing; these numbers are starting settings, not expected answers.

3. Use a small control rule. Once per second, for example, add 5 packets/s after successful feedback without new timeouts, or halve the rate after one or more new timeouts. Clamp the rate to a documented range, such as 5 to 150 packets/s. Apply at most one decrease per interval and count each timeout once. Without successful feedback, never apply the additive increase. Ignore duplicate replies when counting successes.

4. Handle timing carefully. Pace packets rather than sending a one-second batch all at once. Keep recently sent packets pending across control-interval boundaries until they receive a reply or actually time out. Choose the response timeout above the measured unloaded round-trip time, allowing for software scheduling. A missing reply is a possible overload signal, not proof; corroborate it with the router’s drop log. Use a monotonic source clock.

5. Measure a complete run. Try 60-second runs so the rate can rise past capacity and back off several times. Stop new sends at the end, then allow a documented response-drain interval. Compute delivery ratio for the packets sent during the run, and state the time interval used for goodput. Plot rate and overload events together. Periodic pacing and the no-buffer model may affect utilization; discuss these limitations.

Optional enhancements: Add a finite FIFO queue, vary capacity during a run, compare two control rules, or study two competing flows. These are not required for full credit. Receiver flow control and network congestion control solve different problems; this track studies limited link capacity rather than limited receiver buffer space.

# 5 Roadmap and launch activities

The project launches on Wednesday, September 23, 2026. The first four weeks focus on the common network; the next four focus on the chosen extension and evaluation. Week 1 begins on Wednesday, September 23; subsequent development weeks begin on Mondays as listed. The dates below identify development weeks, not submission deadlines. The progress report and working core demonstration are due October 16; the submission time and remaining deadlines will be posted on Canvas. Presentation sessions follow the development period.

| Week beginning | Focus | Checkpoint and evidence |
|---|---|---|
| 1: Sep. 23 | Design and setup | Meet with your assigned team, create the repository, sketch the topology and packet format, exchange messages between neighbors, and complete the ungraded team check-in. |
| 2: Sep. 28 | Forwarding | Implement virtual addressing, packet parsing, and static forwarding. Demonstrate host-to-host communication through routers. |
| 3: Oct. 5 | Complete the core | Add return paths, hop limits, error handling, delay/loss settings, and logs. Test both static paths. |
| 4: Oct. 12 | Core checkpoint | Complete core acceptance tests and baseline measurements. Save the working commit, submit the progress report and core demonstration by October 16, and confirm the extension plan. |
| 5: Oct. 19 | Extension design | Define the extension’s algorithms, control messages, interfaces, tests, and comparison baseline. Begin implementation. |
| 6: Oct. 26 | Extension integration | Integrate the advanced feature with the working mini-internet. Demonstrate its main behavior. |
| 7: Nov. 2 | Experiments | Complete extension acceptance tests, repeat comparative experiments, and explain results. |
| 8: Nov. 9 | Evaluation and preparation | Resolve defects, complete reproducibility checks, draft the report, and rehearse the demonstration. |

First session on September 23: Meet with your instructor-assigned team, create a shared Git repository, assign initial responsibilities, and sketch how virtual addresses map to processes. The first implementation target is two configured neighbors exchanging a UDP message. Select a tentative extension, but prioritize a working common network.

Suggested development practices: Separate packet encoding, host behavior, forwarding, configuration, and condition injection into modules. Start with loss disabled. Save a working version at each checkpoint. Divide ownership but review each other’s work; every member must explain a packet’s full path and the chosen advanced feature.

# 6 Verification and experimental requirements

## 6.1 Core acceptance scenarios

Submit commands/configurations and concise evidence for each scenario:

1. Multihop communication: With injected loss disabled, send at least 20 probes from each host and verify matching responses and payloads. Trace one request and response through all routers on the selected path.

2. Configurable paths: Demonstrate both the upper and lower paths by changing static tables between runs. Show that sends go only to configured neighbors.

3. Hop-limit enforcement: Create an intentional forwarding loop using static tables and show that the packet is discarded after a bounded number of hops.

4. Error handling: Demonstrate an unknown destination, an invalid packet, and a missing response. Log the reason and keep the processes running; the waiting host must time out.

5. Loss and delay: Force one drop, then exercise the random-loss and delay settings. Use logs to distinguish an injected drop from a late response. The core need not recover a lost message.

## 6.2 Baseline and extension measurements

For the core, compare two added-delay settings and two loss settings (0% and 5%) on one documented link or link pair, producing four conditions. Use a fixed set of at least 20 probes per run. Keep message size, path, probe interval, response timeout, and other settings constant. Choose the timeout long enough to allow responses under the planned delay settings. Use a monotonic clock at the source for round-trip measurements.

Run each condition at least three times with recorded seeds. Report the fraction of probes receiving matching responses, round-trip time for successful probes, and timeout count. Include the mean and spread (range or standard deviation) across runs. Round-trip time is measured from sending a probe until its matching response returns; it includes both directions and is not one-way delay. The configured link loss probability is not necessarily the end-to-end probe failure probability.

Repeat the extension’s comparison conditions at least three times as well. Hold the workload and unrelated settings constant and state the measurement definitions. Report failures alongside successful-run timings; do not discard unsuccessful runs. Provide at least two labeled plots or tables, including one baseline result and one extension comparison. Predict trends and explain results using course concepts. Keep deterministic fault tests separate from random experiments.

## 6.3 Wireshark evidence

Capture a representative exchange on the loopback interface or the interface used for a multi-computer setup. Filter to project ports. Submit a short project-only capture with an explanation of the actual UDP endpoints, the virtual endpoints in your payload, and the request/response path, corroborated by logs. Add one example relevant to your extension, such as a changed next hop, a retransmission, or congestion feedback.

Wireshark decodes UDP but does not automatically decode your virtual protocol. Explain relevant payload fields using your packet specification. A packet dropped before the socket send call will not appear in the capture; document such drops in logs. No custom dissector is required.

# 7 Submissions and presentation

| Deliverable | Deadline |
|---|---|
| Progress report and core demonstration | Friday, October 16, 2026 (time on Canvas) |
| Final presentation | To be announced on Canvas |
| Final report and code | To be announced on Canvas |

Week 1 check-in (ungraded): By the end of the first project week, list your assigned team members, initial responsibilities, a repository link, and a tentative extension choice. A short Canvas entry is sufficient; no formal proposal is required. This check-in helps identify scope or setup problems early and carries no project points.

Progress report and working core demonstration (20%): Submit a 1–2 page report with common-network acceptance evidence and baseline results; current architecture; unresolved defects; updated schedule; and the confirmed extension and experimental plan. Include the repository commit identifying the runnable core checkpoint and reproducible commands. Summarize each member’s contributions to date and submit the individual statements described below. Provide a brief recorded demonstration or demonstrate the core live as arranged in class. The report and demonstration receive one combined score using the progress rubric.

Final presentation (15 minutes including questions): Brief architecture explanation, a live or reproducibly recorded core demonstration, an extension demonstration, quantitative findings, limitations, and team contributions. Aim for 11–12 minutes of presentation/demo and 3–4 minutes of questions. All members must participate. Each student must be prepared to explain the common mini-internet, trace a packet through the system, discuss the chosen extension, and interpret experimental results, including work completed jointly or by other team members.

Final report (maximum 4 pages, excluding references): Problem and architecture; key design decisions; experiment setup; results and interpretation; limitations; contributions; repository link and final commit identifier. Include a concise breakdown of team contributions in the report and submit each student’s final contribution statement separately. The separate statements do not count toward the four-page report limit. Put long logs, packet specifications, and execution details in the repository, not in the report.

Repository submission: Include all source code, dependency/version information, a README with exact setup and launch commands, topology/configuration files, test and experiment commands, small example files or generation instructions, raw results, plotting scripts if used, and Wireshark evidence. The submitted version must run from a clean checkout. Submit through the course’s designated repository/Canvas process; public publication is not required.

### Individual contributions and understanding

Each student must submit a brief individual contribution statement with the progress submission and an updated statement with the final submission. Describe work completed to date at the progress checkpoint and the completed contribution breakdown at the end of the project. Submit statements as separate files through the same course submission process; they do not count toward the report page limits.

Identify your specific work in design, implementation, testing and debugging, experiments and analysis, documentation, and integration, as applicable. Reference relevant modules, tests, results, or commits. Clearly identify work completed jointly, including code review and collaborative debugging. Explain changes from the initial division of responsibilities. Contribution percentages are not required.

The team report must summarize who contributed to each major component or activity. Individual statements and repository history provide supporting evidence; commit counts or lines of code alone will not determine contribution. Every student is responsible for understanding the complete common network and the group’s chosen extension, not only their assigned module.

For the Computer Systems Fundamentals assessment, each student’s packet-path explanation, discussion of processes and communication interfaces, and interpretation of experimental evidence will support assessment of individual understanding. These explanations also inform the existing individual-understanding presentation criterion; no additional grading component is introduced.

# 8 Grading rubrics

The project is 30% of the course grade. Project components are progress report and core demonstration 20%, final presentation 35%, and final report/code/evidence 45%. The Week 1 check-in is ungraded. Each required component is scored out of 100. Optional enhancement bonus points are added to the final component:

P = 0.20Pprogress + 0.35Ppresentation + 0.45(Pfinal + B),   0 ≤ B ≤ 10.

Here Pfinal is the required final report/code/evidence score and B is the optional enhancement bonus. The final component can therefore reach 110 points and the project score can reach 104.5. Its contribution to the course percentage is 0.30P ; the maximum bonus adds 1.35 percentage points to the course grade.

## 8.1 Progress report and core demonstration: 20% of project

| Criterion | Points |
|---|---:|
| Working multihop communication with static tables and return paths | 35 |
| Core acceptance tests, baseline measurements, and packet traces | 25 |
| Architecture documentation, reproducible commands, and honest defect analysis | 20 |
| Realistic completion plan, confirmed extension, and contributions | 20 |
| Total | 100 |

## 8.2 Presentation: 35% of project

| Criterion | Points |
|---|---:|
| Core demonstration and accurate explanation of end-to-end behavior | 30 |
| Extension demonstration and explanation of its mechanism | 20 |
| Quantitative results and interpretation using course concepts | 20 |
| Individual understanding and answers to questions | 20 |
| Organization, readable visuals, participation, and time management | 10 |
| Total | 100 |

## 8.3 Final report, code, and evidence: 45% of project

| Criterion | Points |
|---|---:|
| Host communication, packet format, and configuration | 15 |
| Static forwarding, neighbor constraints, return paths, and hop limits | 15 |
| Core error handling, delay/loss controls, and traceable logs | 10 |
| Chosen extension: correct mechanism, scope, and acceptance evidence | 30 |
| Controlled experiments, repeated results, metrics, and interpretation | 15 |
| Reproducibility, code organization, and documented interfaces | 10 |
| Clear report and Wireshark analysis connected to logs | 5 |
| Total | 100 |

### Optional enhancement bonus: up to 10 points

The same bonus opportunity is available to all three tracks. Extend your chosen track beyond its required scope using an optional enhancement listed above, or a comparable enhancement discussed with the instructor. Describe the additional mechanism, distinguish it from required work, and identify each member’s contribution. One well-developed enhancement can earn the full bonus; adding more features does not increase the 10-point cap.

| Criterion | Bonus points |
|---|---:|
| Correct implementation and integration beyond the required scope | 5 |
| Reproducible tests and a meaningful comparison with the required baseline | 3 |
| Clear explanation of the mechanism, results, limitations, and contributions | 2 |
| Maximum bonus | 10 |

Bonus credit is based on demonstrated results, not simply attempting a harder feature or choosing a particular track. Include the enhancement in the final code and evidence, summarize it within the existing report page limit, and be prepared to explain it during the presentation. Required work is scored independently; an enhancement does not waive any core or track requirement. The bonus is normally shared by the team, consistent with the final artifact score. Individual understanding remains assessed through the existing presentation criterion.

### How criteria are evaluated

For each criterion, full credit requires correct, reproducible behavior and a clear explanation. Substantial credit is appropriate for a mostly correct implementation with limited, documented defects. Partial credit requires meaningful working behavior or verifiable progress; unsupported claims and screenshots alone do not establish correctness. Missing or nonfunctional work receives little or no credit for the affected criterion. An ambitious extension does not substitute for the required core.

Progress and final artifact scores are normally shared by the team. The 20-point presentation criterion for individual understanding is scored separately for each member; the other presentation criteria are shared.

Individual understanding is assessed separately from the shared quality of the group implementation, using each student’s explanations and answers as evidence.

# 9 Academic integrity and permitted assistance

Acknowledge external references and permitted third-party dependencies. Do not copy an existing mini-internet framework or a completed implementation of your chosen networking feature. Standard libraries for sockets, serialization, checksums, timing, argument parsing, and plotting are permitted; the required networking algorithms must be your team’s work.

Before submission: Confirm that another person can launch the network from your README, reproduce a successful multihop message exchange, trigger the documented core and extension tests, and reproduce the extension comparison.
