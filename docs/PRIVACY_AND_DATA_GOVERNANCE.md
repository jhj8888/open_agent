# Privacy and Data Governance Policy

Last updated: August 13, 2026

## Purpose and scope

Astron Agent is open-source software for building and operating agentic workflows.
This document explains the project's privacy and data-governance expectations and
the controls available to people who deploy Astron Agent.

The Astron Agent maintainers publish source code and project infrastructure; they
do not operate every deployment made from that source code. The organization that
deploys Astron Agent determines why and how personal data is processed in that
deployment and is responsible for publishing a deployment-specific privacy notice,
selecting lawful processing grounds, handling data-subject requests, and complying
with the laws that apply to it. This document is not a substitute for that notice
and does not claim that every deployment is automatically compliant with any law.

## Applicable law

Privacy and data-protection requirements depend on where a deployment operates,
where its users and infrastructure are located, the people it serves, and the data
and decisions in its workflows. Before processing personal data, each deployer must
identify and document the domestic and international laws that apply to it, complete
any required impact or transfer assessments, and reflect those obligations in its
privacy notice, contracts, operating procedures, and technical configuration.

If legal obligations conflict with a proposed workflow, the deployer should not
run that workflow until it has a lawful design. Project documentation and
configurable controls provide implementation support, not legal advice or a
compliance certification.

## Data the software can process

The exact data depends on the workflows, models, plugins, authentication provider,
and storage services selected by the deployer. A deployment can process:

- account and authentication data supplied through the configured identity provider;
- tenant, organization, space, role, and resource-membership identifiers;
- prompts, model responses, conversation history, workflow state, and memory;
- documents, metadata, extracted text, embeddings, and retrieval results used by
  knowledge bases;
- workflow definitions, agent configuration, plugin and tool inputs and outputs;
- credentials and connection details for models, plugins, tools, and infrastructure;
- operational records such as request identifiers, timestamps, errors, traces,
  metrics, and logs; and
- content sent to configured external model, tool, knowledge, or content-audit
  services.

Prompts, uploaded documents, and tool results may contain personal or sensitive
data even when the platform does not require it. Deployers should classify these
fields according to their actual use rather than relying only on field names.

## Purposes and data minimization

Data should be collected only when needed to authenticate users, isolate tenant
resources, execute and improve the requested workflows, provide configured memory
or knowledge retrieval, secure and troubleshoot the deployment, and meet applicable
legal obligations.

Deployers and workflow authors should:

- use stable, pseudonymous identifiers where possible;
- not place secrets or sensitive personal data in `uid`, `chat_id`, names, or other
  fields that do not require it;
- request only the input required for a workflow's stated purpose;
- redact or tokenize personal data before sending it to a model or plugin when the
  full value is unnecessary;
- avoid collecting special-category or highly sensitive data unless the use case,
  legal basis, safeguards, and deletion process have been reviewed; and
- document the source, purpose, recipients, retention period, and lawful basis for
  each material category of personal data.

The [integration guide](guide/integration.md#production-checklist) also warns against
putting secrets or sensitive personal data in identifiers.

## Storage, access, and tenant boundaries

Astron Agent supports authenticated deployments, application credentials, tenants,
spaces, and permission checks. Its deployment architecture can use databases,
object storage, caches, queues, search services, and observability systems. See
the [deployment guide with authentication](DEPLOYMENT_GUIDE_WITH_AUTH.md) and the
[configuration guide](CONFIGURATION.md).

These capabilities do not replace secure deployment. Operators are responsible for:

- enabling authentication and applying least-privilege roles to users and services;
- changing example and default credentials before exposing a deployment;
- keeping tenant resources and service accounts separated;
- using HTTPS for external traffic and protected networks for internal services;
- encrypting sensitive data and backups according to their threat model and legal
  requirements;
- storing secrets in an appropriate secrets manager instead of source code, logs,
  workflow definitions, or client-side configuration;
- limiting and reviewing access to databases, object storage, logs, traces, and
  backups; and
- testing authorization and tenant isolation for their configuration and extensions.

## External services and international transfers

Models, plugins, tools, knowledge services, identity providers, observability
services, and optional content-audit services can receive data from a workflow.
Before enabling one, the deployer should review its privacy terms, security
practices, retention, training-data rules, hosting locations, subprocessors, and
cross-border transfer mechanism. Workflow authors should make these recipients
visible to the deployer and, where required, to end users.

Astron Agent does not make an external service private merely by integrating it.
Deployers should disable unused integrations and avoid sending a provider more data
than it needs.

## Retention, deletion, and portability

The open-source project does not impose one retention period on independently
operated deployments. Each deployer must define periods that are no longer than
necessary for its stated purposes and legal duties.

A deletion process should cover primary databases, conversation and memory stores,
uploaded files, indexes and embeddings, caches, queues, logs, traces, exports, and
backups. Where immediate backup deletion is impractical, deleted data should be
isolated from normal use and expire under a documented backup schedule. Operators
should also account for copies already sent to external services.

Deployers should provide authenticated channels for access, correction, deletion,
restriction, objection, and portability requests where applicable. Requests should
be verified, recorded, completed within the legally required time, and denied only
on a documented legal basis.

## Security and incident handling

Astron Agent follows the public [iFLYTEK organization security policy](https://github.com/iflytek/.github/blob/main/SECURITY.md),
which points to the detailed [iFLYTEK community security policy](https://github.com/iflytek/community/blob/master/SECURITY.md).
Security vulnerabilities must be reported privately to
[security@iflytek.com](mailto:security@iflytek.com), not in a public issue. The
organization policy describes the information to include, a 48-hour acknowledgment
target, coordinated remediation and disclosure, and the supported-version policy.

Operators remain responsible for monitoring their own deployments, maintaining an
incident-response plan, preserving appropriate evidence, notifying affected parties
and authorities when required, and applying security updates. Only the latest Astron
Agent release should be expected to receive upstream security fixes.

## Project and deployment privacy contacts

- Report a vulnerability privately to
  [security@iflytek.com](mailto:security@iflytek.com).
- Send questions about this project document to
  [ifly_opensource@iflytek.com](mailto:ifly_opensource@iflytek.com). Do not include
  personal data or confidential incident details in a public GitHub issue.
- Contact the operator named in a deployment's own privacy notice for data-subject
  requests or incidents involving that deployment. Astron Agent maintainers cannot
  access or delete data held by an independently operated instance.

## Governance and changes

Privacy-impacting changes should be reviewed for data minimization, permission and
tenant boundaries, external disclosures, retention, logging, and deletion behavior.
Material changes to this document are made through the repository's public review
process. The file history records those changes.
