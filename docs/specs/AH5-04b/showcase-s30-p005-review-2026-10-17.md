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

## Integration boundary proposal (`harness-handover-S30-P005-integration-review-2026-10-17.md`)

Scope: the execution/integration boundary and the shape of the `fence` result only. No Harness contract change is
needed. The candidate is a sound one to test: a per-incarnation principal removes the connection-name identity
problem above. The deciding experiment is the right size. Five points for the boundary:

1. **Typed result.** Anything other than an exact `FENCED` maps to `INHIBITED`.
   - Outcomes:
     - `FENCED`;
     - `GENERATION_MISMATCH`;
     - `UNKNOWN_INCARNATION`;
     - `PARTIAL`, meaning revoked but closure or reconciliation is unconfirmed;
     - `FAILED`.
   - A client timeout or transport error is `UNKNOWN`, decided on the caller's side.
   - `FENCED` echoes `incarnation_id`, `expected_generation` and a caller-supplied request ID.
   - `FENCED` carries the revocation record and the post-reconcile session count, which must be zero.
   - The caller rejects a response whose echo does not match its request.
   - Recovery from `UNKNOWN` re-invokes the idempotent `fence`, not a separate status read. A replay at the same
     generation returns the original revocation record. A lower generation is never reported as success.
2. **Revocation is monotonic.** `PARTIAL` and `FAILED` must never roll back the revocation. An incarnation, once
   revoked, stays revoked, and only a new incarnation with a new principal restores service.
3. **One generation authority, with fixed ordering.** The plugin's registration generation and the database fence
   generation are two stores. Say which one is authoritative and fix the order:
   - first, advance the database generation, so a still-running handler's effect is refused;
   - then call `fence`;
   - then verify drain;
   - then do the audited resume.

   If the broker fence comes first, a live handler can commit a stale effect before the database advances.
4. **Test in-flight handshakes deterministically.** Concurrent reconnects from a client loop do not reliably land
   between authentication (`connection.start-ok`) and `connection.open` or the first channel. A small TCP proxy that
   holds the client's frames can pause a handshake at each of those points while `fence` runs. Also cover these:
   - Credential caching (`rabbitmq_auth_backend_cache`, or any backend cache) must be off, or invalidated by `fence`.
     Otherwise a revoked principal can still authenticate.
   - Compare against a baseline: per-incarnation principal plus user deletion. This shows whether the plugin's own
     serialization adds anything beyond least privilege.
5. **The provisioner joins the trust boundary.** Creating per-incarnation users and permissions needs broader rights
   than `fence`. Name that provisioner, and its credential, in the trust boundary. Keep it separate from the fencer's
   mTLS identity. The registration mapping from incarnation to principal is security-critical: a fencer that can
   alter it can fence an arbitrary principal.

The stated limits are correct and should stay in the result: no business-effect atomicity, no cluster-wide drain, and
no resume safety. I did not re-check the cited RabbitMQ documentation. S30-06 stays OPEN / NOT QUALIFIED.

### Revision check: integration-boundary review closed

The revision incorporates all five points:

1. typed outcomes, exact `FENCED` checks and replay with the same request ID;
2. monotonic revocation;
3. PostgreSQL `fence_generation` as the sole business-effect authority, with the order close admission → advance →
   `fence` → drain → audited resume;
4. the TCP-proxy handshake holds at three boundaries, the user-deletion baseline and the credential cache;
5. separate provisioner and fencer identities, with the mapping immutable to the fencer.

The integration-boundary review is closed. Two non-blocking notes for the experiment:

- A replay that reuses a `request_id` with a different `incarnation_id` or `expected_generation` must be rejected,
  not answered with the stored record.
- The **Trust boundary** bullet should also list the provisioner and its credential.

Registering a bounded research task, and authorizing the disposable plugin spike, are Showcase decisions. No Harness
contract change. S30-06 stays OPEN / NOT QUALIFIED.
