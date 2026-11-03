# Content Safety Policy

Last updated: August 13, 2026

## Purpose and scope

Astron Agent can accept user input, retrieve third-party material, invoke models
and tools, and return generated content. Such content can be inaccurate, misleading,
inappropriate, harmful, or illegal. This document describes the project's safety
expectations, the optional controls in the software, and the responsibilities of
people who deploy workflows.

The Astron Agent maintainers do not operate or moderate every independently hosted
deployment. Each deployer must assess its users, jurisdiction, domain, models, data,
and tools; define enforceable rules; provide an end-user reporting channel; and
staff its own review and appeal process. High-impact uses require safeguards beyond
the general controls described here.

## Baseline rules

Deployments should not knowingly enable or distribute content or actions that:

- violate applicable law or another person's rights;
- sexually exploit or endanger children;
- credibly threaten, harass, or promote violence or hateful abuse against people;
- expose personal, confidential, or authentication data without authorization;
- facilitate fraud, impersonation, malware, unauthorized access, or evasion of
  security controls;
- present fabricated or unverified claims as established fact where people could
  suffer material harm; or
- bypass the deployer's stated safety rules through a model, plugin, tool, or
  retrieved document.

Context matters. Legitimate security research, education, documentation, and
reporting may discuss risky subjects without promoting harm. Human reviewers should
consider purpose, audience, likely impact, and applicable law rather than relying
on keywords alone.

## Available technical controls

Astron Agent contains an optional text-audit pipeline for workflow input and output.
When configured, it can submit text to an iFLYTEK audit service and stop processing
when that service returns a non-allow action. The implementation supports auditing
user input and streamed text output.

Important limitations are public and intentional:

- content auditing is disabled by default (`AUDIT_ENABLE=0`);
- enabling it requires the operator to configure the audit service and credentials;
- text sent for auditing is disclosed to the configured audit service and must be
  covered by the deployment's privacy notice and data-flow review;
- media input and output audit methods are not currently implemented; and
- no automated classifier can guarantee that all harmful content is detected or
  that all flagged content is harmful.

The relevant settings are documented in the workflow configuration templates, and
the implementation is available in the
[workflow audit system](https://github.com/iflytek/astron-agent/tree/main/core/workflow/infra/audit_system).
Operators may add compatible layers at the model gateway, workflow, plugin, or
application boundary, but should test their behavior before relying on them.

## Deployment safeguards

Before making a workflow available, its operator should:

- document intended and prohibited uses, expected users, foreseeable misuse, and
  escalation owners;
- evaluate models, system prompts, tools, plugins, and knowledge sources for the
  deployment's risk level;
- apply least-privilege credentials and require human approval before consequential
  external actions;
- separate untrusted content from instructions and test prompt-injection and data
  exfiltration scenarios;
- disclose that output is AI-generated and may be wrong, and provide sources where
  practical;
- add domain-specific validation and qualified human review for decisions affecting
  health, safety, rights, employment, education, finance, or access to essential
  services;
- monitor representative failures and abuse patterns without collecting unnecessary
  personal data; and
- provide a visible way to report content and challenge consequential outcomes.

Turning on the optional audit service is one control, not a complete safety program.

## Reporting and moderation process

Reports about content in an independently operated Astron Agent application must
go to that application's operator. The operator should publish a contact method
near the user experience and ask reporters for the content or workflow identifier,
time, reason for concern, and enough context to investigate. Reporters should avoid
resending sensitive content unless it is necessary and the channel is protected.

For content on Astron Agent's project-managed community surfaces, report abusive
or harassing conduct to
[ifly_opensource@iflytek.com](mailto:ifly_opensource@iflytek.com) under the
[Code of Conduct](https://github.com/iflytek/astron-agent/blob/main/.github/code_of_conduct.md).
Report security vulnerabilities
privately to [security@iflytek.com](mailto:security@iflytek.com) under the public
[iFLYTEK organization security policy](https://github.com/iflytek/.github/blob/main/SECURITY.md)
and its detailed [community security policy](https://github.com/iflytek/community/blob/master/SECURITY.md).
Do not disclose vulnerabilities or personal data in a public issue.

The project does not yet have enough comparable content reports to publish a
meaningful historical average response time. The iFLYTEK community's
[incident resolution procedures](https://github.com/iflytek/community/blob/master/code-of-conduct/coc-incident-resolution-procedures.md)
state that Code of Conduct reports are usually acknowledged within a few business
days. The organization security policy has a separate 48-hour acknowledgment target
for vulnerability reports. Independent deployers must publish their own service
level and emergency path.

### Appeals for community harassment decisions

For harassment or other Code of Conduct cases on Astron Agent's project-managed
community surfaces, the reporter, the person accused, or another person materially
affected by the outcome may request a review. Send the request to
[ifly_opensource@iflytek.com](mailto:ifly_opensource@iflytek.com) with the subject
`Astron Agent Code of Conduct appeal`. Include the original case reference, the
outcome being challenged, and at least one reason for review, such as a material
procedural error, a relevant conflict of interest, significant new evidence, or
a remedy that appears clearly disproportionate.

Appeals are handled as follows:

1. Receipt should be acknowledged within a few business days. If more time is
   needed, the appellant should receive periodic status updates where practical.
2. A person who did not make the original decision and has no conflict of interest
   will review the request. The reviewer may seek an additional impartial reviewer
   or qualified external advice when the case requires it.
3. Review is limited to the stated grounds and relevant evidence. It is not a new
   investigation unless fairness or significant new information requires one.
4. Temporary protective measures may remain in place during review when needed to
   protect people or evidence. They may be modified if they become unnecessary or
   disproportionate.
5. The reviewer may uphold, modify, or reverse the original decision, or return
   the matter for a new investigation. The appellant and other affected parties
   will receive a written outcome and a concise explanation to the extent privacy,
   safety, and law permit.

Appeal information is shared only with people who need it to conduct the review,
protect participants, or comply with law. Reviewers must protect reporter and
witness identities, avoid unnecessary disclosure of personal data, and follow the
confidentiality and conflict-of-interest rules in the linked incident resolution
procedures. Retaliation for making or participating in a good-faith appeal is
prohibited. This process does not restrict any rights or remedies available under
applicable law.

## Review, action, notice, and appeal

A deployer's documented moderation process should:

1. triage imminent danger, child-safety concerns, and credible security incidents
   for urgent specialist handling;
2. preserve only the evidence needed for a proportionate review;
3. assess the content, context, applicable rule, law, and possible user impact;
4. take a proportionate action, such as warning the user, withholding content,
   disabling a workflow or integration, restricting an account, or escalating to
   an authorized specialist;
5. record the rule and rationale, notify affected users when lawful and safe, and
   provide an appeal route; and
6. remove temporary evidence according to the deployment's retention schedule and
   use confirmed incidents to improve safeguards.

Automated decisions should be reversible where practical. Appeals should be reviewed
by a person who was not solely responsible for the initial decision, especially
when the outcome materially affects an individual.

## Children and young people

Astron Agent is a general-purpose development platform, not a child-directed service.
A deployer that permits use by children or processes their data must perform an
age-appropriate risk assessment, use any legally required parental or guardian
consent, minimize collection and profiling, avoid manipulative design, restrict
contact and high-risk tools, provide child-accessible notices and reporting, and
route serious concerns to trained personnel and the appropriate authorities.

If those protections cannot be provided, the workflow should not be offered to
children. The project
[Code of Conduct](https://github.com/iflytek/astron-agent/blob/main/.github/code_of_conduct.md)
separately protects community participation from harassment regardless of age.

## Privacy, transparency, and review

Content review itself can expose sensitive information. Reports, audit-service
requests, logs, reviewer access, and retained evidence must follow the
[Privacy and Data Governance](PRIVACY_AND_DATA_GOVERNANCE.md) document and the
deployment's own privacy notice.

Operators should disclose which safeguards are active, their important limitations,
and any material external recipients. Material changes to this document are made
through the repository's public review process, and the file history records them.
