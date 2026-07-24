# Off-CV Dossier — Daniel Kovač (interview ammunition)

Facts the candidate genuinely has but which appear in NEITHER CV file. The tester
answers interview questions ONLY from the CVs plus this dossier — first person,
2–5 sentences, natural phrasing, never beyond these facts. Each block maps to an
expected gap from the README table.

## Kafka / event-driven (expected B gap)
At Cargonaut I designed and ran the shipment order-event pipeline on Apache Kafka:
tracking state changes are published as events (~1.2M events/day, 6 topics) consumed
by billing and the customer notification service. I implemented idempotent consumers
with PostgreSQL offset bookkeeping so replays never double-bill. Ran it in production
for three years including two broker upgrades.

## Kubernetes (expected B gap — signature-story material)
I led Cargonaut's migration from ECS to Kubernetes (EKS): 12 services over 9 months.
Challenge: deploys took 45 minutes and staging drifted from production. Mechanism:
containerised the remaining services, introduced Helm charts + ArgoCD GitOps, one
canary service first, then batches of three. Outcome: deploy time 45 → 8 minutes,
staging/production parity, and rollbacks became one Git revert. I still operate these
workloads day-to-day.

## Observability (expected B gap)
For the settlement-adjacent parts of our stack I built the Prometheus + Grafana setup
myself: RED-metrics dashboards for all four services, SLOs with error budgets for the
tracking API (99.9% availability target), and alert routing that cut pager noise
roughly in half. I give an internal "reading your dashboards" onboarding session.

## FastAPI (expected B gap — CVs only show Django/Flask)
I migrated two internal Cargonaut services from Flask to FastAPI in 2024 (async
endpoints for webhook fan-out; pydantic models shared with the consumer contract
tests). Not the whole platform — the Django monolith remains — but I've shipped and
operated FastAPI in production.

## Payments / PSD2 (expected C gap — position honestly, do NOT overclaim)
Honest position: my payments exposure is the Stripe checkout integration at Finleap
(PSP integration, webhooks, reconciliation of payouts against invoices) and the
invoicing/dunning domain generally. I have NOT worked under a PSD2 licence or with
BaFin supervision. If asked: say exactly that, and offer the transfer argument —
correctness-critical financial data modelling (invoicing, reconciliation,
idempotency) is my daily work; the regulated wrapper is new, the engineering
discipline is not.

## Blockchain / crypto (expected C gap — EXPLICIT DENIAL, verbatim)
"No — I have no blockchain or crypto experience at all. That's an honest gap; I
haven't worked with settlement rails on chain and I don't want to claim otherwise."
(Expected system behaviour: honest status, concept never becomes claimable.)

## Security (supporting colour, only if asked)
At Finleap I ran our quarterly dependency-audit rotation and fixed two
externally-reported auth issues (session fixation, IDOR on invoice PDFs). Familiar
with least-privilege IAM on AWS. No formal security certification.
