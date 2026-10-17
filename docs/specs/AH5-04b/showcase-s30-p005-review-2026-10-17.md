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
