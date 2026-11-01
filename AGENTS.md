# JayAgent Market Assistant Rules

- Keep business facts in deterministic services and tools; never invent order or action results.
- Every business resource must be scoped by `tenant_id`.
- Treat model output as a proposal, never as proof that an external action succeeded.
- All write actions require validation, an idempotency key, and an auditable receipt.
- Prefer small vertical slices with tests over adding more agents or channels.

