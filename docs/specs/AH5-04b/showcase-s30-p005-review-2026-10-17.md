# Harness review of Showcase S30-06 P-005 (fencer research) handover (2026-10-17)

- Scope: Showcase `docs/reviews/harness-handover-S30-P005-2026-10-17.md` (uncommitted, read-only) and its proposed
  integration boundary.
- Contract: **no Harness contract change.** Showcase correctly assigns AMQP identity, the fencer, the consumer and
  business-effect integration to itself.
- The design comments below concern correctness of the proposed boundary. They are not gate decisions; S30-06 stays
  OPEN / NOT QUALIFIED.
- Also checked:
  - the review archive's sha256 is `d54e818d…`, as stated;
  - no ADR 0001 frozen module changed relative to `49ab97f`, in commits or in the working tree.

## Agreed

- The broker-generated connection `name` is the identity, and the client display name is diagnostic only.
- A 404 on a stale identifier proves nothing about a replacement connection.
- A timeout, a lost response or an ambiguous observation leaves admission inhibited.
- A handler is live after its channel closes. The generation check belongs inside the same transaction as every
  protected effect.
- An administrator-tagged credential is not an acceptable fencer.

## Gaps in the proposed boundary

1. **Closing a connection does not stop the principal from reconnecting.** Steps 1–2 fence connections, but the
   same Rabbit principal can open a new connection between the re-read and the drain acknowledgement. Freezing
   *application* registration doesn't prevent that.
   - The fence has to be principal-scoped first: revoke or zero the principal's vhost permissions, or block the
     user. Only then close its connections and verify that no live connection exists for that principal.
   - That ordering is also what makes the close-then-verify step meaningful.
   - It sharpens the least-privilege problem: in stock RabbitMQ, permission changes need administrator rights, so
     the "operation-scoped broker-side extension" the handover asks for has to cover the principal block as well
     as the close.
2. **Channel number and consumer tag are weak identifiers.** Channel numbers are per connection and reused.
   Consumer tags are client-chosen unless the broker generates them. Bind identity to the broker connection name,
   the node and a broker-generated consumer tag (`basic.consume` with an empty tag). Treat the channel number as
   informational. Channel identity across reconnect stays unverified, as stated, and the identity model should not
   depend on it.
3. **Drain evidence must not come from one management snapshot.** `/api/connections` lagged
   `/api/channels`/`/api/consumers` in the run. The API is eventually consistent. Require all three endpoints to
   agree that nothing remains for the principal, re-checked after a settle interval. Better still, use broker
   events (`rabbitmq_event_exchange`: `connection.closed`, `consumer.deleted`) bound to the exact identifiers as
   positive evidence.
4. **Make the generation check a write predicate.** For point 4 of the proposal, the protected effect should be a
   conditional write in the same transaction as the current generation, such as
   `UPDATE … WHERE generation = :expected` (checking that rows were affected) or a `SELECT … FOR UPDATE` on the
   generation row. A concurrent fence must not be able to interleave between check and effect. The run proved
   rejection for generation 0 against 1 sequentially, not under that race; add the concurrent case to the test seam.

## For Harness, later

When S30-06 defines an exact target and asks for qualification, send its tuple: broker, node count, image digests
and the fault-injection surface. Harness can then say whether the qualification and grading machinery applies, or
whether a new reviewed qualification is needed. Nothing is needed now.

## Reconnect follow-up (run `20261017T202943Z-9f3564a897`)

Checked against `run-metadata.json`, `probe-result.json` and the first attempt's `cleanup-reconciliation.json`:

- Matches the handover:
  - RabbitMQ digest `fa4df769…`, Docker 29.8.2, guest kernel 7.0.14-linuxkit;
  - channel number 1 reused while connection names and broker-generated consumer tags differ;
  - the old tuple absent from all three management APIs after the client close;
  - a 404 on the stale ID, with the new channel still usable;
  - cleanup complete and checked, and the first attempt's container and probe image removed with 15-second deadlines.
- The status `PASS_FOR_NARROW_OBSERVATION_ONLY` and the stated limitations are accurate.

Two findings:

1. **The broker connection `name` is not unique over time.** This corrects point 2 above. The recorded names are
   `172.18.0.3:50508 -> 172.18.0.2:5672` and `…:50512 -> …`: the client and server address and port. A later
   connection that reuses the same client port gets the same name, so an exact-name DELETE can close a replacement.
   The 404 only shows that the port was not reused in this run.
   - Bind identity to the name plus the broker's `connected_at` and node, re-checked atomically with the close.
   - Or use a unique principal per incarnation (boundary item 1), which makes a principal-scoped fence independent
     of connection names.
2. **The workload connections ran as `p005admin`.** Both incarnations' `principal` is the administrator, so "same
   principal" here means the administrator. Later runs should give the workload its own non-administrator principal,
   distinct from the fencer's and the administrator's, so principal blocking and reconnect refusal are tested on a
   representative principal.

No Harness contract change. S30-06 stays OPEN / NOT QUALIFIED.
