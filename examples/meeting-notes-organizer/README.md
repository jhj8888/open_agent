---
id: meeting-notes-organizer
title: Meeting Notes Organizer
description: Turns messy meeting notes or a raw transcript into a structured summary with decisions and action items.
category: productivity
features:
  - Extracts topic, participants, decisions, and action items from raw notes
  - Chains a second LLM pass that formats the extraction into a formal meeting summary
  - Keeps the summary grounded in the transcript (no invented details)
author: fengyue-xve
sourceUrl: ""
dslVersion: v1
event: ""
---

# Meeting Notes Organizer

Paste a raw meeting transcript (or your own hastily typed notes) and get back a structured
meeting summary: topic, discussion points, decisions, and action items with owners and deadlines.

## How it works

**start → LLM (extract) → LLM (format) → end.** The workflow runs two prompts in sequence to
show a simple but useful pattern: a **chained LLM pipeline**.

1. The first LLM acts as a recorder: it pulls the topic, participants, discussion points,
   decisions, and action items out of the raw, messy input, with strict instructions not to invent
   anything.
2. The second LLM receives that structured extraction and rewrites it as a formal, well-organized
   meeting summary.

Splitting "extract" from "format" keeps both prompts small and makes the output far more stable
than asking a single prompt to do everything at once. Swap in your own two-stage prompts to reuse
this skeleton for other "raw input → structured output → polished document" tasks.

## Dependencies

- **Models**: one Spark chat model, used by both LLM nodes (credentials scrubbed — replace
  `YOUR_APP_ID` with your own). Using different models per node also works and is worth trying.
- **Plugins / skills**: none
- **Knowledge bases**: none

## Import & run

1. In Astron Agent: create a workflow → **Import** → choose `workflow.yml` from this directory.
2. Replace the `YOUR_APP_ID` placeholder in both LLM nodes with your own Spark credentials.
3. Run it with a meeting transcript as input.

> Credentials in the exported DSL were replaced with placeholders before publishing.
