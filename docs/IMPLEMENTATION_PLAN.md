# RecruitAI — Implementation Plan

The living plan for taking RecruitAI from the current Reader stage to the full
system described in *RecruitAI — The Academic Hiring Agent (Final, Part 1)*
and its data-model workbook. Section numbers below (§) refer to that document.

**This file is the single place the plan is tracked.** When scope, order or a
decision changes, this file is edited in the same commit and the change is
noted in the [Change log](#change-log).

- Last updated: 2026-10-05
- Current phase: **Phase 2 — Statutory rules as data** (not started)

## Status at a glance

| Phase | What it delivers | Status |
|---|---|---|
| 0 | Reader stage: resume to checked, structured fields; Excel export | Done |
| 1 | Foundation: FastAPI app, PostgreSQL schema, application states, audit trail | Done |
| 2 | Statutory rules as data: UGC thresholds and score tables with clause and page | Not started |
| 3 | Intake: job openings, in-app application form, HR manual upload | Not started |
| 4 | Reader aligned to the data model; extraction review (Gate 1) | Not started |
| 5 | Assessor and Decision: the deterministic rule engine | Not started |
| 6 | HR dashboard: outcomes with reasons, approve or override (Gate 2) | Not started |
| 7 | Reporting: digest, plain-language reasons, drafted emails (Gate 3) | Not started |
| 8 | More intake channels: email inbox, Google Forms | Not started |
| 9 | Logins, school-scoped access, views, highlights, policy layer, drives dashboard | Not started |
| 10 | Hardening and delivery: encryption, deployment, manual, final report | Not started |

## Principles carried from the design document

These hold for every phase. A change that breaks one needs an explicit decision
recorded below.

1. The model only reads and writes. It extracts fields and drafts prose. It
   never does arithmetic and never decides an outcome (§3.2).
2. Every decision is computed by deterministic Python against a versioned
   rules table, and carries a clause, a gazette page and a rule version (§5.2, §10.2).
3. School, department, designation, category and study leave come from the
   application form, never inferred from the resume (§8, §9.5).
4. The output is a shortlist for interview, not a ranking that selects (cl. 4.1 Note, §6.4).
5. A human approves at three gates: extraction review, outcome approval,
   communication approval. Nothing is sent automatically (§9.7).
6. Absent values stay null. Nothing the resume does not state is guessed.
7. No real candidate data in logs, tests, fixtures or commits.

## Technology (approved 2026-10-04)

| Layer | Choice |
|---|---|
| Language | Python throughout |
| Backend | FastAPI, Pydantic |
| Database | PostgreSQL, SQLAlchemy, Alembic migrations |
| Frontend | Server-rendered pages (Jinja + HTMX) |
| Background jobs | Scheduler with a job table in PostgreSQL (no Celery/Redis) |
| Workflow | Application state machine in the database (§9.3); LangGraph optional later |
| LLM | Gemini behind the existing `LLMProvider` interface; paid key before real candidate data |
| Dev tool | The Streamlit dashboard stays until the new screens replace it |

## Phase 0 — Reader stage (done)

- PDF/DOCX text extraction; scanned PDFs reported as unreadable, never sent to the model.
- Gemini structured extraction; every value grounded against the resume text.
- Retry with backoff and jitter, failure kinds, per-file result cache, retry-failed.
- Formatting layer: names, degrees, PhD status, dates (DD-MM-YYYY), durations, designations.
- Workbook: `candidates` plus nine detail sheets (ranking, education, publications,
  seminars_workshops, teaching_skills, experience, patents_awards_projects,
  guidance_memberships, contact_details).
- 482 automated tests on synthetic data.

Known gaps carried forward: one scanned resume unreadable (no OCR); teaching years
blank where the resume gives no total; some experience rows lack designation or dates.

## Phase 1 — Foundation (done)

Goal: a running web application with a database that everything later builds on.

- [x] `backend/` FastAPI app; the existing `llm/` and extraction code reused as a library.
- [x] SQLAlchemy models and an Alembic migration for §10.2: regulators, schools, departments,
      candidates, applications, extracted_data, evaluation_results, rule_versions,
      rubric_rules, state_transitions.
- [x] Models for §17: candidate_profile, personal_details, qualifications, experience,
      research_profile, publications, subjects_taught, skills, hobbies, highlights,
      institutions_master, recruitment_drives, job_openings.
- [x] Seed data: 21 schools with regulator, AICTE overlay and `is_hiring_unit`; 6 regulators.
- [x] Application state machine (§9.3); every transition written to `state_transitions`.
- [x] The Reader as a workflow step: its result is stored in the entity tables.
- [x] API: health, schools, create application, run Reader, read record, read audit trail.
- [x] Resume storage by content hash; logging without personal data; tests on a test database.
- [x] Real candidates' first names and institutions removed from the older tests.
- [x] PostgreSQL 16 installed on the development machine; migration applied and reference
      data seeded. `TEST_DATABASE_URL` runs the backend tests against a scratch
      PostgreSQL database.

Verified: 41 backend tests on SQLite and on PostgreSQL 16 (523 tests in total); a live run
of the API on PostgreSQL with the fake provider, covering EXTRACTED, PENDING_REVIEW, a
scanned file going to FAILED, a refused second read and refused bad input; and the
migration applied, checked for drift, downgraded and re-applied on PostgreSQL.

Running on PostgreSQL found one defect SQLite had hidden: a foreign key declared inside
`create_table` was skipped, leaving `candidate_profile.highest_qualification_id`
unconstrained. The migration now adds it explicitly. The review before commit also added:
text from the model is cut to its column length (PostgreSQL rejects over-long values that
SQLite accepts), the stored filename is reduced to its last path component, and the
Reader endpoint takes a row lock so two requests cannot read one application twice.

Known limits, by design at this phase: the API has no login (Phase 9) and must stay
bound to 127.0.0.1; every upload creates a new candidate (duplicate detection is Phase 3).

Differences from the design document, all recorded in `backend/models.py` and `backend/states.py`:

- A school has a primary regulator and an optional overlay, in place of the text "UGC + AICTE".
- Partly stated dates ("2014", "2021-05") are stored as stated, not padded to a full date.
- Four extra tables (events, achievements, guidance, memberships) hold lists the Reader
  extracts that the data model has no sheet for.
- Three extra state moves: NEEDS_JOB_MATCH (§16.1); PARSING back to RECEIVED when the
  model is unavailable, so a quota error is not recorded as an unreadable file; FAILED
  back to RECEIVED when a candidate re-uploads.
- The Reader runs when its endpoint is called. Running it in the background is Phase 3.
- Tables for users, access, policy rules and views are left to Phase 9.

## Phase 2 — Statutory rules as data

Goal: every threshold and points table in the database, each with its citation.

- [ ] Fetch the UGC gazette PDFs listed in §1.2 and transcribe from the source, not from memory.
- [ ] `rule_versions` seeded per §10.3.
- [ ] `rubric_rules` for Assistant Professor, Associate Professor, Professor, Senior Professor (§6.1).
- [ ] Appendix II Table 2 (Research Score) as data rows.
- [ ] Appendix II Table 3A (shortlisting score), including the 3rd Amendment change to S.No. 3.
- [ ] A printable transcription sheet for the mentor to check against the gazette.

Done when: the tables are loaded, each row has clause and page, and a human has
checked the transcription.

## Phase 3 — Intake

Goal: applications arrive tied to a specific opening, with the form fields the rules need.

- [ ] Recruitment drives and job openings: create, list, close.
- [ ] Public application form: school, department (dependent dropdown), designation,
      category, differently-abled flag, state, study leave taken, resume upload.
- [ ] HR manual upload against an opening (single and bulk).
- [ ] Resume file storage; duplicate-applicant detection by email.
- [ ] Unreadable files go to FAILED with an HR alert (§9.6).
- [ ] Run the Reader in the background (job table and scheduler), and retry applications
      sent back to RECEIVED because the model was unavailable.

Done when: a candidate can apply to an opening and HR can see the application in RECEIVED state.

## Phase 4 — Reader aligned to the data model, and Gate 1

- [ ] Extend extraction for the fields the data model has and we lack: state, first-author
      flag, author count, impact factor, research profile IDs and metrics, subject level
      (UG/PG), per-post concurrency with study, project funding amounts, level of awards and talks.
- [ ] Map institutions to `institutions_master` (tier), disciplines to the discipline list.
- [ ] Gate 1 screen: PENDING_REVIEW applications show only the flagged fields for a
      person to complete or correct; edits are audited.
- [ ] Stop flagging NET/SET "low confidence" when the resume has no NET/SET mention.
- [ ] Decide on OCR for scanned resumes.

Done when: an application moves RECEIVED to EXTRACTED, or to PENDING_REVIEW and back
after a human fills the gaps.

## Phase 5 — Assessor and Decision (the rule engine)

- [ ] Regulator resolution from the school; unimplemented regulators go to MANUAL_REVIEW (cl. 1.1).
- [ ] NET/SET logic with every branch in §6.2, including SET state validity.
- [ ] Marks threshold with the cl. 3.4 and cl. 3.5 relaxations.
- [ ] Adjusted experience under the two-part cl. 3.11 rule (§9.5); when study leave is
      unknown, compute both ways and route to review if the outcome differs.
- [ ] Research Score (Table 2), labelled as claimed pending verification.
- [ ] Shortlisting score (Table 3A). This replaces the current home-made ranking.
- [ ] Decision: applied rank first, then walk down the ranks (§9.4); outcome records
      the failing clause, page and rule version.
- [ ] The 13 test cases in §12 as automated tests.

Done when: all §12 cases pass and every outcome can be traced to a clause and page.

## Phase 6 — HR dashboard and Gate 2

- [ ] Per-opening list: outcome, reason in plain words, clause and page.
- [ ] Candidate page with the full record (the nine detail sections).
- [ ] Approve, or override with a recorded justification; return for re-assessment.
- [ ] Shortlist shown as a shortlisting aid, with the interview named as the deciding step.
- [ ] Excel export kept as a download.

## Phase 7 — Reporting and Gate 3

- [ ] Per-opening HR digest.
- [ ] Plain-language explanation of each outcome (the model writes prose from the
      engine's result; it does not change the result).
- [ ] Drafted candidate emails, queued. Each send needs a person's approval.
- [ ] Sending by SMTP after approval; every send audited.

## Phase 8 — More intake channels

- [ ] Email inbox over IMAP (Gmail in development; the college mailbox later is a settings change).
- [ ] Classify each message as JOB_OPENING, APPLICATION or OTHER: fixed rules first,
      the model only for ambiguous cases, and which path was used is logged (§16).
- [ ] Unmatched applications held in NEEDS_JOB_MATCH.
- [ ] Google Forms channel: read responses from the linked Sheet and resumes from Drive.

Development uses made-up resumes only.

## Phase 9 — Access control and the Section 17 extensions

- [ ] Users, password hashing, sessions; roles from §17.6.
- [ ] School- and department-scoped filtering enforced server-side on every query.
- [ ] HR and interviewer views driven by `view_field_visibility`.
- [ ] Highlights (§17.4), university policy layer (§17.7), drives dashboard (§17.8).

## Phase 10 — Hardening and delivery

- [ ] Personal data encrypted at rest and in transit (§11.2).
- [ ] Paid LLM key; confirm data-use terms.
- [ ] Performance check against the 10-second target; backups; error monitoring.
- [ ] Deployment (Docker), user manual, final report.
- [ ] Optional: wrap the workflow in LangGraph for the agent framing.

## Decisions

| Date | Decision |
|---|---|
| 2026-10-04 | Tech stack as in the table above; the document's stack was a reference, not a requirement. |
| 2026-10-04 | Replace the home-made ranking with the UGC Table 3A shortlisting score; add the Table 2 Research Score as a pass/fail threshold. |
| 2026-10-04 | UGC tables are transcribed from the gazette PDFs, not from memory. |
| 2026-10-04 | Primary intake is an in-app form; Google Forms and email are additional channels. |
| 2026-10-04 | Email is read over IMAP so the mailbox can change without code changes. |
| 2026-10-04 | No live Gemini calls without the user's go-ahead (free-tier quota). |
| Earlier | Rank and score across uploaded resumes kept in the Phase 0 workbook at the user's request; to be superseded in Phase 5. |
| Earlier | CGPA shown as a percentage using (CGPA − 0.75) × 10, marked as converted. |

## Open questions

| Question | Needed by |
|---|---|
| HR-confirmed school list and which schools have departments (§7.4) | Before go-live (seeded with the document's 21) |
| Regulator for School of Education (NCTE?) and Allied Healthcare (§7.4) | Phase 5 |
| Are category and contact details hidden from the interview panel? (§17.5) | Phase 9 |
| Which college mailbox, and who grants access | Phase 8 |
| Where the system will be hosted | Phase 10 |
| OCR for scanned resumes: yes or no | Phase 4 |

## Risks

| Risk | Mitigation |
|---|---|
| A UGC table is transcribed wrongly | Source-cited rows; mentor checks the transcription sheet |
| The 2025 draft Regulations are notified mid-project | Rules are versioned data; a new instrument is new rows |
| Free-tier quota blocks testing | Result cache; made-up resumes; paid key before real use |
| Research Score needs evidence resumes lack | Labelled as claimed; verified at document check |
| Older commits on GitHub still contain tests naming real candidates (removed from current code in Phase 1) | Rewrite history only if the user asks |
| SQLite and PostgreSQL behave differently in places | Run the backend tests with `TEST_DATABASE_URL` set before each commit that touches the schema |

## Change log

| Date | Change |
|---|---|
| 2026-10-04 | Plan created after reviewing the Part 1 document and data model. |
| 2026-10-04 | Phase 1 built. PostgreSQL verification left open (not installed on the dev machine). Four detail tables and three state moves added beyond the document. Background running of the Reader moved from Phase 1 to Phase 3. |
| 2026-10-05 | PostgreSQL 16 installed natively (not Docker). Phase 1 verified on it and closed; a missing foreign key in the migration was found and fixed. |
