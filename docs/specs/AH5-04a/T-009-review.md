# Harness review of Showcase T-009 (AH5-04a-2) against the shared contract

- Packet: Showcase `docs/specs/SDD-OBS-001/packets/T-009.json`, `packet_sha256`
  `8a9778a1a9a8b9efd2cb8e6693e76ed7fb189429d2d92e518abf549378046630` (canonical body hash verified). The handoff
  quoted `d981e02e…`, which matches neither this field nor the file; please confirm which revision is accepted.
- Scope reviewed: what crosses the shared contract (ADR 0001, 0002, 0004, 0005 proposed). Showcase-internal
  lifecycle design is Showcase's review.

## Verdict: aligned, with four points to carry into the task

1. Offline backends never count. The test seam's "independent controlled backend" may exercise authority
   logic only: `fake`/`controlled` results (`agent_harness.execution`, ADR 0004) must never satisfy an independent
   obligation or record independent success. The packet's "no physical launch or independent PASS on absent
   qualification" covers this; keep it as an explicit test.
2. Qualification proof is a contract document. Where the packet requires a "qualification proof" or
   "qualified-backend absence", bind to a `CapabilityReport` for the exact target and policy digest that
   `contract.validate_capability_binding` accepts against a passing qualification report (Q01–Q16, B1–B10). Until
   one exists, the path stays `environment-blocked`, as the packet says.
3. Parity port. All changed files are moved modules or their callers; Harness ports the accepted result. Keep the
   library seams: any new lifecycle call that `verification/authority.py` makes (reservation, consumption,
   receipt) must go through the injected lifecycle port, not a direct `harness` import, and new profile or
   registry lookups through explicit parameters (ADR 0001 decision 3). List new port methods in the packet's
   evidence so the port can follow.
4. Receipts. A terminal receipt bound to admission, reservation and consumption is Showcase-internal until the
   cutover; if it is meant to cross to consumers, it needs contract fields (ADR 0005 proposes `target` and
   `limits`; a receipt binding would be another addition). Do not encode it in `versions` or free text.
