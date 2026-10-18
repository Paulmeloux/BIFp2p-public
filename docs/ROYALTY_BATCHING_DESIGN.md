# Superseded royalty batching experiment

**Status: SUPERSEDED. This is not an implementation plan or an operator guide.**

The approved policy is **one completed trade, one separate royalty payment**.
Batching is not permitted under the [license](../LICENSE).

Use [ROYALTY_RECIPIENT.md](ROYALTY_RECIPIENT.md) for the current policy,
deadlines, payment evidence and incident contact.
Use [RELEASE_GATE.md](RELEASE_GATE.md) for the remaining preparation steps.

This path is retained so older links clearly lead to the superseding decision.
The former batch design is available in Git history; it must not be treated
as a requirement to finish or activate batching.

The experimental batch module, provisional internal-transfer transport and
associated tests have been removed. The active implementation uses the
per-trade worker described in [ROYALTY_WALLET.md](ROYALTY_WALLET.md).
