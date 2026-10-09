# RecruitAI — Implementation Plan

The living plan for taking RecruitAI from the current Reader stage to the full
system described in *RecruitAI — The Academic Hiring Agent (Final, Part 1)*
and its data-model workbook. Section numbers below (§) refer to that document.

**This file is the single place the plan is tracked.** When scope, order or a
decision changes, this file is edited in the same commit and the change is
noted in the [Change log](#change-log).

- Last updated: 2026-10-07
- Current phase: **Phase 8 — More intake channels** (built; awaiting the user's check on the real mailbox)

## Status at a glance

| Phase | What it delivers | Status |
|---|---|---|
| 0 | Reader stage: resume to checked, structured fields; Excel export | Done |
| 1 | Foundation: FastAPI app, PostgreSQL schema, application states, audit trail | Done |
| 2 | Statutory rules as data: UGC thresholds and score tables with clause and page | Built, UGC and AICTE; awaiting a human check of the transcription |
| 3 | Intake: job openings, in-app application form, HR manual upload | Done |
| 4 | Reader aligned to the data model; extraction review (Gate 1) | Built; new fields' accuracy not yet measured |
| 5 | Assessor and Decision: the deterministic rule engine | Built; several readings await the mentor |
| 6 | HR dashboard: outcomes with reasons, approve or override (Gate 2) | Done |
| 7 | Reporting: digest, plain-language reasons, drafted emails (Gate 3) | Done; live rewording untried |
| 8 | More intake channels: email inbox, Google Forms | Built; inbox tried live once; form script untried |
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

## Phase 2 — Statutory rules as data (built; one item open)

Goal: every threshold and points table in the database, each with its citation.

- [x] Fetched the four UGC gazette PDFs listed in §1.2 and read the rules from them
      (principal Regulations pp. 57-61 and 105-109; 2nd, 3rd and 4th Amendments in full).
      Each instrument row records its source URL and the SHA-256 of the file read.
- [x] `rule_versions`: five instruments. The 1st Amendment, 2021 is recorded as deleted
      and not fetched. The 2025 draft is not loaded.
- [x] `rubric_rules` for Assistant Professor, Associate Professor, Professor, Senior Professor.
- [x] `relaxation_rules`: cl. 3.4 (reserved categories) and cl. 3.5 (pre-1991 Master's).
- [x] Appendix II Table 2 (Research Score): 52 rows, including the impact-factor list,
      authorship shares, the 30% cap and the three-categories condition.
- [x] Appendix II Tables 3A and 3B (short-listing score), with the 3rd Amendment's changes
      to S.No. 3 and Note D attributed to that amendment.
- [x] `docs/ugc_rules_transcription.md`: every loaded value with its page and a tick box,
      generated from the same data the system loads; a test fails if the two differ.
- [x] Read-only API: `/rules` and `/rules/score-tables/{table}`.
- [ ] **A person checks the transcription sheet against the gazette** and signs it.
- [x] **AICTE (Degree) Regulation, 2019** fetched from the AICTE website and loaded: Assistant
      Professor for ten discipline groups (cl. 5.1), Associate Professor and Professor
      (cl. 5.2), and the First Class and grade-point table (cl. 7.3). `rubric_rules` gained
      `discipline_group` and a structured `criteria` column for requirements the UGC
      columns cannot hold (which degree must be First Class, years after the Ph.D.,
      alternative routes).
- [x] `docs/UGC_Rules_Mentor_Review.docx`: the same values as a Word document for the mentor,
      with instructions, what is expected of the mentor, and a declaration to sign.
      Generated by `tools/make_mentor_review_docx.py`.

Verified: every Table 2 figure was compared, in order, with the numbers in the gazette's
own text; Tables 3A and 3B were read from the page images; the AICTE clauses were read
from the full gazette text and agree with the page images in the university's own extract.
82 rule tests pass on SQLite and PostgreSQL (606 tests in total). The page numbers in the
design document all matched the UGC gazette.

What the university's HR portal publishes (read 2026-10-06):

- "AICTE Norms" is pp. 32-37 of the AICTE (Degree) Regulation, 2019. Its text matches the gazette.
- "UGC Norms" is pp. 5-8 of Maharashtra G.R. Misc-2018/C.R.56/18/UNI-1. It differs from the
  UGC gazette in three places: it still requires a Ph.D. for Assistant Professor from
  01.07.2021; it omits the cl. 3.11 sentence that teaching service during a research degree,
  without leave, counts as experience; and it says "two examiners" where UGC says "two
  external examiners". The system follows the gazette.
- "UGC Additional" is pp. 59-66 of the UGC 2018 gazette, already loaded.

What reading the gazette turned up:

- **Engineering is not in the UGC clause 4 at all.** Clauses 4.1 to 4.8 cover arts, sciences,
  music, drama, yoga, occupational therapy, librarians and physical education. Under cl. 1.1
  the technical-education authority's norms prevail, so the computing, engineering and
  technology schools are governed by AICTE's regulations. The design document treats AICTE
  as an overlay on the UGC floor; for those schools it is the governing instrument. Nearly
  every resume received so far is for such a post. Under AICTE cl. 5.1(a) an Assistant
  Professor in engineering needs First Class in the Bachelor's or the Master's degree, and
  neither NET/SET nor 55%. AICTE sets no Research Score and no short-listing score.
- AICTE cl. 7.3 gives a grade-point table (6.25 = 55% ... 8.25 = 75%). The (CGPA - 0.75) x 10
  conversion already used for display gives the same figure at each listed point.
- The gazette does not say whether impact-factor points add to, or replace, the 8/10 per paper.
- After the 3rd Amendment it is unclear whether an M.Tech/M.E. scores as Post-Graduation,
  under S.No. 3, or both.
- Percentage bands cannot be applied to a CGPA without the awarding university's conversion.

All of these are listed in `OPEN_POINTS` in `backend/rules_data.py` and printed in the
transcription sheet with a line for the decision.

Beyond the document's schema: `relaxation_rules` and `score_rules` tables; `code`,
`source_url` and `source_sha256` on `rule_versions`; `min_doctoral_guided` and `notes`
on `rubric_rules`.

## Phase 3 — Intake (done)

Goal: applications arrive tied to a specific opening, with the form fields the rules need.

- [x] Openings: create, list, close, with an optional closing date. A recruitment drive
      (advertisement) is created or reused from the advertisement reference.
- [x] Each opening records the rule set HR chose for it (`discipline_group`): UGC, or an
      AICTE discipline. The form suggests one from the school's regulator; HR confirms it.
      This answers "which AICTE discipline does each school fall under" per opening.
- [x] Departments are created the first time HR names one for a school, and reused after.
- [x] Public application form: name, email, phone, state, category, differently-abled,
      study leave, resume, declaration. School, department and designation come from
      the opening. Errors are shown per field and the answers are kept.
- [x] HR upload of one or many resumes against an opening; a file that cannot be accepted
      is reported and the rest still go in.
- [x] Duplicates: the same email cannot apply twice to one opening; the same person
      applying to two openings is one candidate; the same file uploaded twice to one
      opening is refused; an uploaded resume whose email matches an existing candidate
      is flagged for a person and never merged automatically.
- [x] Reading queue (`jobs` table): an application is queued on arrival. It is read when HR
      presses "Process queue" or `python -m backend.worker` runs. When the model is
      unavailable the job is retried later (2, 4, 8 ... minutes; one hour for a spent
      quota) and stops after 8 attempts.
- [x] "Process queue" reads in the background: the button answers at once, the page shows
      "Reading in progress" and refreshes itself, and a second press cannot start a second
      run. A job interrupted by a server stop is put back in the queue after 20 minutes.
- [x] Unreadable files go to FAILED and appear under "Needs attention" on the HR home page.
- [x] Form answers outrank what the Reader extracts for name, email, phone and state.
- [x] Web pages: HR home, new opening, opening detail, applicant list, application form,
      confirmation. Dates in DD-MM-YYYY, times in local time.

Verified: 47 intake tests on SQLite and PostgreSQL (653 tests in total). The pages were run
against PostgreSQL with the fake provider and made-up files: an opening created, an
application submitted, a repeat submission refused, three files uploaded, the queue
processed. The HR home, new-opening and opening-detail pages and the applicant form (at a
narrow width) were checked by screenshot. Page responses measured 5 to 80 ms; an upload of
three files, one of 40 pages, took 78 ms. With a provider slowed to 3 seconds per resume,
"Process queue" answered in 5 ms and the other pages stayed under 80 ms while four resumes
were read behind it.

Different from the plan as first written:

- **Nothing reads resumes automatically.** The queue runs only when a person starts it.
  That keeps model requests, and the free-tier quota, under the user's control. A
  deployment with a paid key would run `python -m backend.worker --loop` as a service.
- **No HTMX yet.** The pages are plain server-rendered forms and needed no JavaScript.
  HTMX stays the choice for the first screen that needs partial updates (Gate 1, Phase 4).
- **The "HR alert" is the Needs attention panel**, not an email or a notification.
- Applications record what the applicant typed (`applicant_name`, `applicant_email`,
  `applicant_phone`, `applicant_state`) separately from what the Reader extracts.

Not done, and where it belongs:

- No login: the HR pages are open to anyone who can reach the server (Phase 9). Keep it
  bound to 127.0.0.1.
- No CSRF protection or rate limiting on the public form (Phase 9, with sessions).
- HR cannot yet enter category, state or study leave for an uploaded resume, resolve a
  possible duplicate, or re-queue a failed job from the screen (Phase 4, Gate 1).
- An applicant cannot look up the status of an application.

## Phase 4 — Reader aligned to the data model, and Gate 1 (built; the new fields' accuracy not yet measured)

Goal: the Reader fills the data model's fields, and a person can settle what it could not.

- [x] Extraction extended (`llm/interface.py`, `llm/prompts/extraction.md`, the Gemini schema): the State in the
      candidate's address; per paper, the author list, first-author flag and impact factor; research profile IDs
      (Scopus, ORCID, Google Scholar) and stated metrics (citations, h-index, i10-index); the programme level each
      subject was taught at; whether the resume says a post was held while studying; a funded project's amount;
      the level (international, national, State, university) of awards and events.
- [x] The model copies; Python counts and converts. The author count is the length of the copied author list.
      A funding amount is copied as written and turned into rupees in code (`parse_amount_inr`), and only when it
      is plainly one rupee amount. Each new value is kept only if the resume states it: an author list with a name
      not in the resume is dropped whole, an impact factor or metric must appear as a number in the text, a level
      needs its word in the resume, and "held while studying" is only ever yes or unknown, never no.
- [x] Institutions: `institutions_master` is seeded with the four rows of the workbook's Institutions_Master sheet
      and matched by exact name or alias, never by a near match. HR extends the list from a CSV
      (`python -m backend.institutions <file.csv>`).
- [x] Disciplines: each qualification's course is placed on the workbook's Discipline list (25 entries) when its
      wording names one; otherwise it is left unplaced, not filed under "Other".
- [x] Gate 1 screen (`/hr/applications/{id}`, `backend/gate1.py`): a PENDING_REVIEW application shows only the
      flagged fields, each with why it was flagged and the line the Reader quoted. A person enters or confirms a
      value, or ticks "not stated; leave empty". The check is saved whole or not at all; every field is recorded in
      `review_edits` (confirmed, corrected, entered or left empty, with the value before and after); the application
      then moves to EXTRACTED. The audit trail in `state_transitions` names the fields, never the values.
- [x] NET/SET "low confidence" no longer raised when the resume has no NET/SET wording at all: a text search
      confirms the absence, so the confidence is only capped. If the wording is present and the Reader still
      recorded none, the row is flagged with its own reason (`net_set_status:mentioned_in_resume`).
- [x] An HR upload has no form answers, so it now always stops at Gate 1 for category, State, differently-abled
      and study leave. They are entered by a person, never read off the resume. "Not known" is an allowed answer.
- [x] A possible duplicate is a reason for review. A person answers "same person" (the application and everything
      read from its resume move to the candidate already held) or "different person". One person cannot end up
      with two live applications for one opening; one of them is withdrawn instead.
- [x] A job that stopped retrying has a "Try again" button on the HR home page.
- [x] The stored resume opens from the review page, served under the application reference.
- [x] OCR for scanned resumes: **no, for now** (decided 2026-10-06). A scanned file goes to "Needs attention".
- [x] The new prompt run on a live model: the user read 12 resumes on 2026-10-06 and all 12 were read and stored.
- [ ] **Compare the new fields with the resumes.** In that run the author count was filled for 26 of 131
      publications, an event level for 19 of 109, a subject level for 10 of 68, and impact factor, funding amount
      and "held while studying" for none. A value is kept only when the resume states it, so low is not wrong
      by itself, but nobody has yet checked how many were missed. The author list is the first to look at: it
      is dropped whole if one name does not match the resume text.

Verified: 72 new tests; 725 in total on SQLite, and the 243 backend tests on PostgreSQL 16. Migration 0004 applied,
checked for drift, downgraded and re-applied on PostgreSQL. The review page was run on a temporary server with the
fake provider and made-up files: three uploads read, one checked and saved through to EXTRACTED, and the page
checked by screenshot. The user then read 12 resumes on the live model and completed a check on the real screen.
In that run all 12 came back with no NET/SET and 2 were flagged for it; 6 of the 12 needed only the form answers.
A first attempt the same afternoon failed on HTTP 503 from one model (overloaded), not on the new response format.

Different from the plan as first written:

- **Still no HTMX.** The check is one form saved in one step, which needs no partial updates.
- **Whether a talk was abroad is not extracted.** Table 2 scores an international talk abroad and one in India
  differently; the Reader records "international" only. Left for Phase 5 to ask or for a person to supply.
- **"Held while studying" is recorded only when the resume says so in words.** Working it out from the dates of
  the post and the degree is arithmetic, so it belongs to the Phase 5 engine.
- New columns beyond the workbook: `author_count` on publications; `level` on events; `level`, `amount_stated`
  and `amount_inr` on achievements; `discipline_listed` on qualifications; `aliases` on institutions; and the
  `review_edits` table.
- The fixed lists (categories, States, disciplines) moved to `backend/lists.py`.

Not done, and where it belongs:

- Every resume read before this phase has to be read again to get the new fields: the prompt changed, so the
  result cache no longer matches.
- HR cannot replace an unreadable file from the screen (the applicant re-applies, or HR uploads the new file).
- A field that was not flagged cannot be edited at Gate 1. That is the design (Section 9.6); Gate 2 (Phase 6)
  is where an outcome is overridden.
- The institutions list holds four rows until HR supplies the real one.

Done when: an application moves RECEIVED to EXTRACTED, or to PENDING_REVIEW and back
after a human fills the gaps. Both paths are covered by tests.

## Phase 5 — Assessor and Decision (the rule engine) (built; several readings await the mentor)

Goal: every read application gets an outcome worked out by Python from the rule tables, traceable to a clause and page.

- [x] `backend/engine/`: facts, experience, scores, rules, decision. No model is called anywhere in it.
- [x] Three answers, not two. Each requirement is met, not met, or cannot be told from what is known. A quantity
      a resume gives loosely (a post dated "2014 to 2019") is carried as a lower and an upper bound. A rank is
      met only if every requirement is met; if one cannot be told, the application goes to MANUAL_REVIEW with the
      open point named. Nothing unknown is rounded either way. This is how Section 9.6's "compute both ways" is done.
- [x] Regulator resolution from the school; BCI, COA and DG Shipping posts go to MANUAL_REVIEW with no threshold applied (cl. 1.1).
- [x] The rule set follows the opening's `discipline_group`: UGC cl. 4.1, or AICTE cl. 5.1 for the discipline and
      cl. 5.2 for Associate Professor and Professor. AICTE cl. 5.1(j) sends science and humanities faculty to the UGC rule.
- [x] NET/SET with every branch that can be read: NET; SET/SLET valid only in the institution's State; the Ph.D.
      exemption under the 2009 or 2016 Regulations. A Ph.D. whose Regulations are not known, and a SET whose State
      is not known, are open points. A Ph.D. holder with a SET from another State is still exempt (the design
      document's sketch returned early there).
- [x] Marks threshold with the cl. 3.4 and cl. 3.5 relaxations, read from `relaxation_rules`. They are alternatives,
      not added together. An unknown category matters only when the marks fall between the two floors.
- [x] Adjusted experience under the two-part cl. 3.11 rule, for UGC-governed posts. Leave taken: the overlap with
      the research degree is deducted. No leave: nothing is deducted. Not known: both, as bounds.
- [x] **AICTE posts get no cl. 3.11 deduction.** The AICTE gazette was searched (same file as recorded, hash
      checked) and has no such provision. Its cl. 2.25 (p. 31) sets conditions for counting past service that
      only documents can show; the experience row cites it and leaves those conditions to the document check.
      Put to the mentor as open point AICTE_NO_RESEARCH_DEGREE_EXCLUSION. (The first build applied and cited
      UGC cl. 3.11 on AICTE posts; the user's first live assessment showed it, and it was corrected before commit.)
- [x] AICTE First Class under cl. 7.3: from the stated class, or 60%, or a CGPA of 6.75 on a ten-point scale.
- [x] AICTE cl. 5.2: experience in teaching, research or industry; two years after the Ph.D.; for Professor, three
      years at Associate Professor level and the two publication routes.
- [x] Research Score (Table 2) and short-listing score (Table 3A), every figure read from `score_rules`, both as
      bounds, labelled as claimed. The home-made ranking is no longer used by the web application.
- [x] Decision: applied rank first, then down the ranks; the outcome records the failing clause, page and rule version.
- [x] `evaluation_results.details` (migration 0005) holds the whole working. The audit trail cites the clause only.
- [x] "Assess read applications" on the opening page, and the working shown on the application page.
- [x] The 13 cases of Section 12 as automated tests, each named for its provision.

Verified: 68 new tests; 793 in total on SQLite and the 311 backend tests on PostgreSQL 16. Migration 0005 applied,
checked, downgraded and re-applied on PostgreSQL. A dry run over the 12 live applications (nothing stored): all
applied for Professor under AICTE engineering; 11 came out as meeting Assistant Professor, 1 as needing a person
(no class or marks stated for either degree). One was then assessed for real by the user on the live screen.

Audit before commit (2026-10-06). After the user's first live assessment showed a UGC clause cited on an AICTE
post, every clause the engine relies on was read again in the two gazette files (both hashes match the recorded
ones) and compared with the code. cl. 3.3, 3.4, 3.5, 3.6, 4.1 I to IV, the Table 2 notes, Tables 3A notes, and
AICTE cl. 5.1, 5.2(c), 5.2(d) and 7.3 agree with what is loaded. Five things were wrong or missing and were fixed:

- UGC cl. 3.11 was applied to, and cited on, AICTE posts. AICTE has no such provision (see above).
- **Years as a research scholar were counted as experience.** cl. 3.11 says the time taken to acquire the degree
  "shall not be considered as teaching/research experience"; only teaching alongside it without leave is saved.
  Research posts that fall inside the research-degree period are now left out.
- **"The research score shall be from the minimum of three categories out of six" (p. 107) was loaded but not
  enforced.** It is now its own check. Category 3 is not read from resumes, so two sure categories is an open
  point, not a failure.
- **UGC cl. 4.1 would have been applied to drama, music, the arts, yoga and therapy posts**, which have their own
  clauses (4.2 to 4.6) with different requirements. Those are now separate choices on the opening form, suggested
  for the Drama, Fine Arts and Sangeet schools. Their rules are not loaded, so such a post goes to a person and
  the page names the clause that governs. An application with no opening is likewise not assessed under a guess.
- Table 3A counted any research post as post-doctoral experience. It now counts teaching for certain and
  research posts in the upper bound only.

Also found: AICTE lists Senior Professor as filled by promotion (Table 1, p. 26), so the engine says that instead
of "no rule loaded". And three Phase 3 queue tests carried a fixed clock of 12:00 UTC on 2026-10-06 and began to
fail when that moment passed; the clock is now a date far ahead.

Not changed, but noted: Gate 1 still asks for the Ph.D. Regulations year and the SET State on AICTE openings,
where neither affects the outcome. Harmless, but extra work for HR. Trimmed in Phase 6.

Readings the engine takes until the mentor rules on them (each is one place in the code):

- **Technical posts are assessed under AICTE, not UGC**, because that is what HR chose on the opening (open point
  ENGINEERING_NOT_IN_UGC_CL4). The outcome says so on the page.
- **A CGPA of 6.75 or more on a ten-point scale is First Class.** The cl. 7.3 table gives 6.75 = 60%; a higher grade
  point is taken as not lower. A grade point of 4 or less is treated as another scale and left to a person.
- **A CGPA is never set against the UGC 55%.** It is an open point every time (cl. 3.6), so a UGC candidate whose
  Master's result is a CGPA goes to MANUAL_REVIEW.
- **Table 2, impact factor:** both readings are taken (replaces the base points, or adds to them), so the score is a range.
- **Table 3A, M.Tech/M.E.:** scored under S.No. 2 and under S.No. 3, as a range.

Limits to know about:

- The Research Score counts only what the Reader extracts. Category 3 (pedagogy, MOOCs, e-content), consultancy
  and policy documents are not read, so a candidate who relies on them can be under-scored. Every outcome is
  still approved by HR at Gate 2.
- The Research Score range is usually wide (authors per paper are rarely listed), so most UGC Associate Professor
  and Professor applications will go to a person until those details are filled in.
- "Equivalent to Assistant Professor" and "relevant branch" are not judged; the page says they are for the committee.
- AICTE disciplines other than engineering have requirements a resume cannot show (professional experience
  after the Master's, a 4-star hotel post). Those go to a person unless something plainly fails.
- Senior Professor is never cleared by the engine: the 10% cap and the three expert reviews are not on a resume.
- NET-exempt disciplines and the foreign top-500 Ph.D. route are not read; such a candidate is an open point, not a failure.

Done when: all Section 12 cases pass and every outcome can be traced to a clause and page. Both hold.

## Phase 6 — HR dashboard and Gate 2 (done)

Goal: a person sees every outcome with its reason, and decides.

- [x] Per-opening list: each application's status, the reason with its clause and page (or the first open point),
      and the HR decision. Listed in order of receipt and labelled as a short-listing aid; nothing is sorted by score.
- [x] Application page with the full record: qualifications, posts, publications, research profile, patents,
      awards, projects, guidance, events, subjects, skills and memberships, beside the assessment's working.
- [x] Gate 2 (`backend/gate2.py`, table `hr_decisions`, migration 0006). For every assessed application HR can:
      **approve** the finding as it stands; **override** it with their own decision, the post, and a justification
      of at least 15 characters; or **send it back**, either to correct named fields at Gate 1 first or to be
      assessed again as it stands. Where the engine could not settle the matter there is nothing to approve: HR
      decides, with a justification.
- [x] An override never erases what it overrode: the engine's finding stays in `evaluation_results`, HR's decision
      beside it in `hr_decisions`, and each re-assessment adds a new evaluation instead of rewriting the old one.
      The justification is kept with the decision and never written to the state audit trail.
- [x] Re-assessment (carried over from Phase 5). Sending back reopens the chosen fields with the reason "returned
      by HR"; once saved, the application is assessed again from the opening's page.
- [x] The Bachelor's marks and CGPA can now be entered at Gate 1, since AICTE First Class most often turns on them.
      A degree row is added only when a value is saved, and is marked as not from the resume.
- [x] On an AICTE opening the Reader no longer sends an application to Gate 1 for the Ph.D. Regulations year or a
      SET's State, since neither affects an AICTE outcome (noted in the Phase 5 audit).
- [x] Excel download per opening (`/hr/openings/{id}/export.xlsx`, `backend/export.py`), laid out like the Reader
      stage's workbook at the user's request: `candidates`, then `education`, `publications`, `seminars_workshops`,
      `teaching_skills`, `experience`, `patents_awards_projects`, `guidance_memberships` and `contact_details`, with
      the same grey headers, frozen key columns, filters, `check` column and shading. Differences: rows are keyed
      by application reference in order of receipt, with **no rank and no home-made score** (the user confirmed this
      on 2026-10-07); `candidates` leads with the finding, clause, page and HR decision; `assessment_checks` lists
      every requirement checked; the Phase 4 fields are included. A CGPA is still shown as a percentage marked
      "converted from CGPA", for reading only. Built in memory; text is guarded against being run as a formula.
- [x] The HR home page counts, per opening: to check, to assess, to decide, decided.
- [x] **An HR upload is no longer held at Gate 1 for the form answers** (category, State, differently-abled, study
      leave). They affect only the cl. 3.4 relaxation and cl. 3.11, and the engine already names a missing answer
      when it would change the outcome. Holding every upload for them stopped all twelve of the user's resumes for
      answers that changed nothing. The application page lists what is not on record and offers "Enter them now",
      which reopens those fields (new move EXTRACTED to PENDING_REVIEW). This reverses a Phase 4 decision.
- [x] **Move to another opening**, one application or a whole opening, for resumes filed under the wrong post. The
      post, school and rule set come from the new opening; nothing is read again. An assessment already made is
      set aside (kept as history) and done again under the new rules. Not allowed once HR has decided, onto a
      closed opening, or where the candidate or the same file is already there.

Verified: 24 new tests; 818 in total on SQLite and the 335 backend tests on PostgreSQL 16. The download from the
live data has the same number of rows as the old workbook for candidates, education, publications, experience and
patents/awards/projects (12, 36, 131, 44, 30). Migration 0006 applied,
checked, downgraded and re-applied on PostgreSQL. The outcome list, the decision forms and the download were run on
a temporary server with the fake provider and made-up files, and checked by screenshot.

Different from the plan as first written:

- **Two ways to send back, not one.** "Return for re-assessment" alone would re-run the same facts and give the
  same answer; what HR usually needs is to correct a field first. Both are offered.
- **The old Streamlit workbook is not reused.** It is built from raw model output and carries the home-made
  ranking. The new download is built from the database and has no rank column.
- The state machine gains the moves outcome to PENDING_REVIEW and outcome to EXTRACTED for the two kinds of return.

Not done, and where it belongs:

- No login, so every decision is recorded as "HR" (Phase 9).
- A decision, once recorded, cannot be reopened from the screen. If one is wrong it has to be corrected in the
  database until Phase 9 gives an administrator role that can.
- No filter or search on the list; an opening with hundreds of applications will want one.
- The plain-language explanation of each outcome and the candidate emails are Phase 7.

## Phase 7 — Reporting and Gate 3 (done; the model rewording still to be tried live)

Goal: each outcome can be read in a sentence, an opening can be read on a page, and no candidate hears anything
until a person has approved the words.

- [x] Plain-language finding for every assessment (`backend/reporting.py`): what was met or not met, the figures
      compared, the clause and the page. Written by code from the stored working, so it cannot say anything the
      engine did not find. Shown on the application page and in the digest.
- [x] Per-opening digest (`/hr/openings/{id}/digest`): applications grouped by where they stand (decided, waiting
      for a decision, for a person, to assess, to check, not read), each with its finding, HR's decision and the
      state of its email; totals at the top. Printable from the browser. Not ranked.
- [x] Candidate emails (`backend/emails.py`, table `email_drafts`, migration 0007). A draft is written the moment
      HR records a decision: short-listed for the post applied for, short-listed for a lower post (with what was
      not met for the higher one), or not meeting the minimum qualifications (with the reasons, clause and page,
      and seven days to write back with a document).
- [x] Where HR overrode the engine or decided a case it could not settle, the engine's reasons are not put in
      HR's mouth: the draft carries a part marked `[HR: ...]`, and cannot be approved until a person has written
      it. HR's internal justification is never copied into the email.
- [x] Gate 3: a person edits, then approves each email. Approval alone sends nothing. An approved email changed
      afterwards has to be approved again. A sent email can no longer be changed or sent twice.
- [x] Sending by SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`). With
      no server configured, which is the state of this installation, an approved email waits. A successful send
      moves the application to CONTACTED and is recorded; the audit trail holds no address. A refused send keeps
      the email approved and records only the kind of failure.
- [x] `EMAIL_REDIRECT_TO`: a development safeguard. When set, every email goes to that one address as a **test
      copy**. The email stays approved, the candidate is not marked as informed, and it can still be sent for
      real once the redirect is removed. The email panel says which mode is in force before the button is
      pressed: "Test mode" with the address the copy will go to, or "Live".
- [x] Rewording by the language model, only when a person presses the button (one request). The model is sent the
      body with the candidate's name replaced by a token, under `llm/prompts/reword_email.md`. Its answer is kept
      only if it still has the application reference, every post named, every clause cited and the name token,
      and contains no figure that was not in the draft; otherwise the draft is left as it was and the person is
      told why. The model cannot change what was decided.

Verified: 29 new tests; 847 in total on SQLite and the 364 backend tests on PostgreSQL 16. Migration 0007 applied,
checked, downgraded and re-applied on PostgreSQL. No email was sent and no model was called in building or testing
this: sending is tested against a stand-in transport, rewording against the fake provider. The email panel and the
digest were run on a temporary server with made-up data and checked by screenshot.

Different from the plan as first written:

- **The explanation is written by code, not by the model.** The plan said the model writes the prose. Code can
  state the finding exactly and costs nothing; the model's part is the optional rewording of an email, behind a
  check that it changed no fact.
- The digest is a page, not a document sent out. Emailing it to HR on a schedule can follow if wanted.

Not done, or not yet tried:

- The Gemini rewording has never been called live; it needs the user's go-ahead.
- Sending has been tried only in test mode (see below), never to a candidate.
- No interview scheduling (out of scope in the design document); CONTACTED is the last state the system reaches.
- One email per decision. A reminder or a second letter would be written by hand outside the system.
- Emails are plain text, in English only.

## Phase 8 — More intake channels (built; not yet tried on the real mailbox)

Goal: an application can arrive by email or through a Google Form and end up exactly where a web-form one does.

- [x] Email inbox over IMAP (`backend/inbox.py`, table `inbox_messages`, migration 0008). Settings `IMAP_HOST`,
      `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER`; the login defaults to the sending account, so one
      mailbox for both needs only the host. Moving to the college mailbox is a change to `.env`.
- [x] Read only when a person asks ("Check inbox" on the Inbox page, or `python -m backend.inbox`), over the last
      14 days. The mailbox is opened read-only and messages are peeked: nothing is deleted, moved or marked read.
      Each message is remembered by its Message-ID and never taken twice.
- [x] Each message is sorted APPLICATION, JOB_OPENING or OTHER by fixed rules, and the rule that decided is stored
      on the row (`classified_by`). A resume (PDF or DOCX) makes it an application; vacancy wording makes it an
      opening announcement; automatic replies, bounces and the account's own mail are set aside.
- [x] An application names its opening by reference ("OPN-00003") in the subject or body. It is then filed under
      that opening and queued for reading. **Whose resume it is comes from the resume, not from who sent the
      message**: resumes are forwarded by HR, colleagues and agencies. The applicant's name and address are read
      from the resume, as for an HR upload; the sender is kept on the inbox row and shown on the application page,
      with a note when the sender's name is not the name on the resume. One sender may send many resumes; the same
      file is not taken twice for one opening. (The first build took the sender as the applicant. The user's first
      live test, forwarding a resume from their own address, showed it.)
- [x] One that names no opening, or a closed one, is held as NEEDS_JOB_MATCH with its resume stored, until a person
      picks the opening on the Inbox page or sets it aside.
- [x] What a person needs to choose the opening for a held application is on the row: the start of what the sender
      wrote (`body_excerpt`, migration 0009), a link to open the attached resume, and a suggested opening when the
      message's own words name a post, department, school or opening title that matches one, with the reason shown.
      The suggestion only pre-selects; a message that names nothing gets none. A "Reply to ask which post" link
      opens the person's own mail program. (The first build showed only sender, subject and file name, and the
      user rightly asked how HR was meant to choose.)
- [x] A held resume that is already in the system (the very same file, under any opening) is pointed out on its
      row with a link to the existing application and its state, before a person files it again. It is not
      refused outright: one person may apply for two posts. Under the same opening it is still refused.
- [x] The read button says how many applications a press will read ("Read the 1 waiting application", "Read 12 of
      the 14"), not only its limit.
- [x] On the field-check form a value and the "leave empty" tick can no longer be given together: the form
      prevents it and the server refuses it.
- [x] Google Form channel, through the same mailbox. A short script on the form (docs/GOOGLE_FORM_SETUP.md) emails
      each response with the uploaded resume and the answers as "Field: value" lines. Such a message is trusted
      only from the form owner's address, and its answers pass the same checks as the web form; answers that fail
      go to a person with the reason. The application is marked as received by Google Form.
- [x] Inbox page: what is waiting for a person, with "File under this opening" and "Set aside", and the last 50
      messages seen with how each was sorted. The HR home page shows how many are waiting.

Verified: 53 new tests in this phase and its review; 900 in total on SQLite and the backend tests on PostgreSQL 16. Migrations 0008 and 0009 applied,
checked, downgraded and re-applied on PostgreSQL. No mailbox was contacted in
building or testing this: messages are built in the tests and handed over by a stand-in mailbox. The user then ran the
first live check: a resume emailed to the development mailbox was found, filed under its opening and read.

Different from the plan as first written:

- **No language model sorts mail.** The plan allowed the model for ambiguous cases. A message the rules cannot
  place is shown to a person instead. That sends no applicant's email to a model, costs no requests, and a wrong
  guess here would file or drop a real application.
- **An opening is never created from an email** (the design document's Section 16.1 has one created
  automatically). The opening fixes which regulation a candidate is judged under, so it stays HR's choice; a
  vacancy announcement is shown to HR to act on.
- **Google Forms goes through the mailbox, not through the Sheets and Drive APIs.** That needs no Google Cloud
  project, key or consent screen, and leaves one channel to look after. The linked spreadsheet is not read.
- **Held applications are rows in `inbox_messages`, not applications in the NEEDS_JOB_MATCH state.** An application
  row needs a school and a post, which only an opening gives. The state remains in the state machine, unused.

Not done, or not yet tried:

- The form script has not been run on a real Google Form.
- Tried on the real mailbox by the user: a filed application, a held one, a refused duplicate and a repeat check. The form-response path has not been seen live.
- Only the first PDF or DOCX of a message is taken. A resume sent as a link, a ZIP or an image is not read.
- An emailed application has no form answers (category, State, study leave); they are entered on its page.
- If the resume gives no email address, the candidate's letter has no address until a person types one; the
  sender's address is not used for it.
- No automatic checking on a schedule. `python -m backend.inbox` can be run by a scheduler when wanted.
- No acknowledgement is sent to someone who applies by email.

## Gap review, 2026-10-08

After the user's live tests of Phase 8 turned up one gap after another, the whole application was walked through
flow by flow, as HR and as an applicant would use it, and then page by page on a temporary server with made-up data
in every state. What was clear-cut was fixed; what needs a decision is listed for the user.

Fixed:

- **A reply to one of our letters could become a second application.** Our letters quote the application reference
  and invite a reply with documents; a reply with a PDF attached would have been filed as a new resume. A message
  quoting an existing "APP-" reference is now attached to that application as correspondence, shown on its page
  and on the Inbox page, and its attachment is kept as a document, not read as a resume.
- **An application could be withdrawn only before it was read or checked.** An applicant may pull out at any
  stage, so every live state can now reach WITHDRAWN, and a letter not yet sent is dropped when it does.
- **A scanned resume was accepted at the application form** and failed later, with the applicant none the wiser.
  The form now refuses a file with no readable text, in words the applicant can act on. (An HR upload or an
  emailed resume is still accepted and reported under "Needs attention", since the sender is not at the screen.)
- **Every inbox check downloaded every message of the window again**, attachments included. A message already seen
  is now recognised from one header and skipped, and the window is 30 days, not 14.
- **One overloaded Gemini model stopped a reading**, though the other was serving (seen on both days of live
  use). After the retries on one model end in "overloaded", the next model in the pool is tried. A timeout or
  malformed output still does not spend the rest of the pool. This reverses an earlier decision, on evidence.
- **Sending back needed HR to work out which field to tick.** The fields that would settle what the assessment
  left open are now ticked ready (the SET's State, the Bachelor's marks, the category, and so on).
- **Before assessment only the form answers could be reopened.** Any field can now be reopened from the
  application page, so a value read wrongly need not go through an assessment first.
- A message's received time is stored in UTC (on SQLite it was shown hours out); "Reply to ask which post" appears
  only on a row waiting for a post; an attachment is called a resume only when it is one; a draft with no address
  says so; the applicant link is shown whole; `run_app.bat` brings the database up to date and starts the app.

Built after the review, at the user's choice (items 1 and 2 of the list below):

- **Emails to candidates, per opening** (`/hr/openings/{id}/emails`). Every letter of the opening is listed and can
  be opened and read there. A person ticks the ones they approve and presses one button; nothing unticked is
  sent. A letter with a reason still to be written, or with no address, cannot be ticked and says why. One
  refused by the mail server does not stop the others. The page states the mode in force: no mail server, test
  mode with the redirect address, or live. It is still one approval per letter (Gate 3), with fewer clicks.
- **Editing an opening** (`/hr/openings/{id}/edit`): the rule set, department, title and closing date; and a closed
  opening can be reopened. The school and the post cannot be changed, being what was applied for. Changing the
  rule set sets aside every assessment not yet decided (kept as history) so it is assessed again under the new
  rules; an application HR has already decided keeps its decision, and the page says how many there are.

Built next, also at the user's choice (items 3, 5 and 7 of the list below):

- **The date eligibility is counted on.** Each opening has one date on which qualifications and experience are
  counted: the eligibility date HR sets for it, or else its closing date (`job_openings.eligibility_date`,
  migration 0010; `backend.engine.facts.counting_date`). Until that date has passed, and for an opening with
  neither date, the count runs to the day of assessment, since service not yet done cannot be counted. Once it has
  passed: service after it is not counted, whenever the post ended; a Ph.D. whose stated award date is after it is
  not held on it (and does not exempt from NET/SET); publications dated after its year are left out, and the
  assessment says so. A degree dated only to a year that includes the date is taken as held if the resume arrived
  by the date, and otherwise goes to a person, with the award date offered ready-ticked on "send back". A Master's
  degree dated after the date is never failed by the engine: it goes to a person, cited as "Eligibility date of
  the opening, set by HR" and not as a clause of the Regulations. Every assessment records the date it used and
  why. Changing the date in force sets aside assessments not yet decided, as a change of rule set does. An
  assessment made before the closing date becomes out of date when that date passes; the opening's page counts
  these and offers one button to assess them again. **The default (closing date) is the usual practice and is
  provisional: neither Regulation names a date, so the mentor or HR is to confirm it.**
- **Reading a resume again** (application page, before HR has decided). The application goes back to the reading
  queue as a job of kind READ_AGAIN, which does not use the stored result of the first reading; nothing is sent
  to the model until a person runs the queue. The new reading replaces the old one, including corrections made
  by hand; the form answers and the list of earlier corrections are kept; an assessment already made is set
  aside. The model runs at temperature 0, so the same resume often reads the same; its use is after a back-up
  model reading or a change of prompt.
- **Correcting the posts held** (application page, before HR has decided). The type and the dates of each post
  can be corrected and a missing post added, with dates to a year, a month or a day. A post entered by HR is
  marked as such. Each change is in `review_edits`. Saving sets an assessment already made aside, and the
  application is assessed again. A post of type "Other" is not counted.

- **Acknowledgement of receipt** (item 6 below; built 2026-10-09). A second kind of letter
  (`email_drafts.kind`, migration 0011): fixed wording written by code, giving the post, the date received and
  the reference, and saying in terms that it states nothing about eligibility. **By the user's decision it is the
  one letter that may go without a person approving it**, and only where the applicant typed their own address:
  the application form (sent after the page has answered) and the Google Form (sent when the inbox is checked).
  It is recorded as approved by `system:acknowledgement`. For an emailed resume the address is the one read off
  the resume, so the letter is written only after the reading, as a draft, and waits on the opening's emails
  page for a person's tick. A resume HR uploaded gets none. One per application, ever. It obeys
  `EMAIL_REDIRECT_TO` (one test copy, nothing to the applicant), a refusal by the mail server never disturbs the
  application (the letter waits on the emails page), sending it moves the application nowhere, and
  `ACKNOWLEDGE_APPLICATIONS=false` turns it off. Every other letter still needs a person's approval.
  `tests/conftest.py` now blanks the mail settings for every test, so no test can reach a real mail server.

For the user to decide (not built):

1. (Built; see above.)
2. (Built; see above.)
3. (Built; see above.) Reading a resume again.
4. (Built in Phase 9, part 1.) Reopening an HR decision not yet sent to the candidate.
5. (Built; see above.) The date eligibility is counted on. The default awaits the mentor's or HR's confirmation.
6. (Built; see above.) Acknowledgement of receipt. A page where an applicant looks up their status is not built.
7. (Built; see above.) Entering and correcting post dates.
8. **Form answers for uploaded and emailed resumes** are still entered by HR when wanted; asking the applicant
   through a private link was discussed and set aside for now.
9. The README's opening section still describes the first, Excel-only stage. To be rewritten in Phase 10.

## Phase 9 — Access control and the Section 17 extensions

Built in two parts, both on 2026-10-09. Neither is committed yet.

### Part 1: accounts and access (built 2026-10-09; not yet committed)

- [x] Users, password hashing, sessions; roles from §17.6 (`backend/access.py`; `users`, `user_school_access`,
      `user_sessions`; migration 0012).
- [x] School- and department-scoped access enforced server-side on every request.
- [x] Every action recorded against the person who took it.
- [x] Reopening an HR decision (gap-review item 4).

How it works:

- **Accounts.** Five kinds, as in §17.6: university administrator, HR administrator, school HR, department HR,
  interviewer. Administrators see every school. A school or department HR account sees only what it is granted:
  a school, or one department of it, at one of three levels: view; view and edit (correct the record, add
  resumes, read, assess); or also approve (Gate 2 decisions and Gate 3 emails). Only a university administrator
  manages accounts (`/admin/users`). The inbox and stopped reading jobs are for administrators, being tied to no
  one school. A school HR account with a whole school to edit may create openings in that school only.
- **One place decides.** Every HR page and the JSON API to applications pass through `current_user`, which calls
  `access.authorise` with the address asked for. Something outside an account's schools is answered as "not
  found", so its existence is not given away; something in scope but above the account's level is refused with a
  page that says so. The home page lists only the account's openings. An application cannot be moved into an
  opening the account may not change.
- **Passwords and sessions.** Passwords are stored only as scrypt hashes (standard library; no new dependency).
  A session is a random token in an HttpOnly, SameSite=Lax cookie; the database holds only its hash; it ends
  after eight idle hours, on sign-out, when the account is closed, and (for other browsers) when the password
  changes. Five wrong passwords close sign-in for fifteen minutes. A wrong address and a wrong password get the
  same answer. A form posted from another site is refused.
- **First account.** With no account at all, every HR page leads to `/setup`, where the first university
  administrator is created; the page then closes for good. Other accounts are created by that administrator
  with a temporary password, which the person must change at first sign-in. Nothing is emailed.
- **Who did it.** Actions are written to the audit trail as `user:<id>` and shown on pages by name. What was
  done before accounts existed stays as `user:hr` and reads "HR".
- **Reopening a decision.** A decision the candidate has not yet been informed of can be reopened by an account
  that may approve, with a reason of at least 15 characters. The decision stays on record as REOPENED, its
  letter is dropped, and the application is assessed and decided again. After the candidate has been informed
  it cannot be reopened.
- The public pages are the application form (`/apply`), sign-in, and the health check.

Three gaps left by the first build of part 1 were then closed, at the user's request:

- **A page offers only what the account may do.** Buttons and forms an account's level does not allow are not
  shown; a line says what the account may do instead. `access.authorise` is still what refuses the action.
- **Forgotten password.** `/forgot` emails a one-time link (30 minutes, single use; `password_resets`, token
  stored only as a hash) to the account's own address. The page answers alike for any address. The link is not
  sent to `EMAIL_REDIRECT_TO`: that setting keeps test mail from candidates, and a reset link must reach its
  owner only. With no mail server the page says so and points to an administrator. `PUBLIC_BASE_URL` sets the
  address used in the link once the application is deployed.
- **The interviewer's pages**: part 2 below.

Left for Phase 10: the app still listens only on 127.0.0.1, and HTTPS comes with deployment.

### Part 2: the Section 17 extensions (built 2026-10-09; not yet committed)

- [x] The interview panel's view, driven by `view_field_visibility` (§17.5).
- [x] Highlights (§17.4).
- [x] The university policy layer (§17.7).
- [x] The university dashboard (§17.8).

Migration 0013: `password_resets`, `view_definitions`, `view_field_visibility`, `university_policy_rules`,
`highlight_norms`.

- **The panel's view** (`/interview`, `backend/views.py`). An interviewer account sees only candidates HR has
  short-listed, in the schools it is granted, read-only, and none of the HR pages; its home page is this list.
  Which parts of the record it sees is data, changed by a university administrator at `/admin/views`. **Until
  the university decides (the open question of §17.5), contact details, category and disability status and
  State are withheld, together with the resume file (which carries the contact details) and the assessment's
  check-by-check working (which can name a category relaxation).** What is withheld is not handed to the page
  at all. HR's own view shows everything and is not restricted by the table.
- **Highlights** (`backend/highlights.py`). Worked out each time the record is shown, so never stale: a Ph.D.
  from an institution the reference list marks PREMIER; an h-index or citation count above the norm; a
  first-author paper with an impact factor above the norm. The norms are the university's figures
  (`highlight_norms`, per school or for all), entered at `/hr/policy`; none is built in, so with no norm nothing
  is pointed out. HR may add its own (stored in `candidate_highlights`). A highlight is a pointer from what the
  resume states; no rule or score reads it. Departure from §17.4: automatic highlights are computed, not stored.
- **The university's own criteria** (`backend/policy.py`, `/hr/policy`). Six kinds, each a figure the university
  enters for a post, for one school or all: Master's marks, experience, publications, h-index, citations, and
  "a Ph.D. is required". They are checked by fixed code after the statutory decision, against the post that
  decision places the candidate at, stored in the assessment's `details["policy"]`, and shown beside the
  statutory finding to HR and the panel. **The standing instruction of §17.7 is kept by construction: nothing
  here can change the statutory outcome or the application's state, and nothing is reported for a candidate
  the Regulations find not eligible.** A criterion applies to assessments made after it is added.
- **The dashboard** (`/hr/dashboard`, administrators). Counts by school, by opening with its advertisement, and
  by channel. No candidate is named and nothing is ranked.

Not built: a refresh of the research profile from Scopus or ORCID (§17.3); the HR view is not made configurable
(it shows everything, as §17.5 says it does by default); comparing drives over time needs openings to be given
their advertisement reference, which the dashboard shows but does not chart.

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
| 2026-10-06 | No OCR for now: a scanned resume is reported under Needs attention and a readable copy is asked for. |
| 2026-10-06 | The extraction prompt was changed once, with the user's agreement, for all Phase 4 fields together. |
| 2026-10-06 | An HR upload always stops at Gate 1 for the form answers; they are never taken from the resume. |
| 2026-10-07 | Reversed in part: an HR upload is not held for the form answers. They are still never taken from the resume; a person enters them, and the engine asks for one only when it would change the outcome. |
| 2026-10-06 | The engine answers met, not met or cannot be told. Anything it cannot tell goes to a person; it is never rounded. |
| 2026-10-06 | Until the mentor rules, the open readings are taken as listed under Phase 5, and scores are given as ranges. |
| 2026-10-06 | Disciplines with their own UGC clause (4.2 to 4.6) are chosen as such on the opening and assessed by a person until their rules are loaded. |
| 2026-10-08 | When a Gemini model stays overloaded through its retries, the next model in the pool is tried (reverses the earlier "do not spend the pool" choice for that one case). |
| 2026-10-08 | An application may be withdrawn at any stage; a message quoting an application's reference is correspondence, never a new application. |
| Earlier | Rank and score across uploaded resumes kept in the Phase 0 workbook at the user's request; to be superseded in Phase 5. |
| Earlier | CGPA shown as a percentage using (CGPA − 0.75) × 10, marked as converted. |

## Open questions

| Question | Needed by |
|---|---|
| HR-confirmed school list and which schools have departments (§7.4) | Before go-live (seeded with the document's 21) |
| Regulator for School of Education (NCTE?) and Allied Healthcare (§7.4) | Phase 5 |
| Are category and contact details hidden from the interview panel? (§17.5) Built hidden by default; a university administrator can change it at `/admin/views`. | The university to decide |
| Which college mailbox, and who grants access (a development mailbox is in use for now) | Before go-live |
| Which mail server and sender address candidate emails go out from | Before any email is sent |
| Where the system will be hosted | Phase 10 |
| Does the university assess technical-school posts under AICTE cl. 5.1/5.2 and not UGC cl. 4.1? (mentor sheet, Section 11) | Phase 5 |
| Which later AICTE clarifications apply; they have not been read | Phase 5 |
| Does any relaxation or short-listing score apply to AICTE-governed posts? The Regulation has neither | Phase 5 |
| How to read a CGPA between or above the five points AICTE cl. 7.3 lists | Phase 5 |
| Should resumes be read automatically on arrival once a paid key is in use? | Before go-live |
| Table 2: do impact-factor points add to or replace the 8/10 per paper? | Phase 5 (Research Score) |
| Table 3A: does an M.Tech/M.E. score as Post-Graduation, under S.No. 3, or both? | Phase 5 (short-listing score) |
| How is a CGPA placed in the percentage bands of Table 3A? | Phase 5 |
| Load UGC cl. 4.2 to 4.6 (music, arts, drama, yoga, therapy)? Needed only if those schools recruit through the system | Phase 6 or later |
| Is eligibility counted as on the last date for applications where the advertisement names no other date? (built that way, provisionally, 2026-10-08) | Before results are relied on |
| Who checks and signs the rules transcription sheet (one question was added on 2026-10-06, so the mentor's copy is one behind) | Before Phase 5 results are relied on |
| Does the university apply the UGC cl. 3.11 exclusion to AICTE-governed posts? AICTE's Regulation has none | Before Phase 5 results are relied on |
| HR's institutions list with tiers and other spellings (four workbook rows are loaded) | Phase 6 (highlights) |
| Was an international talk given abroad or in India? Not on most resumes; ask on the form, or at document check? | Phase 5 (Research Score) |
| How accurate are the new fields on real resumes? Read live once; not yet compared with the resumes | Before Phase 5 relies on them |

## Risks

| Risk | Mitigation |
|---|---|
| A UGC table is transcribed wrongly | Source-cited rows; figures cross-checked against the gazette text; tests typed independently of the data; mentor checks the transcription sheet |
| Technical posts assessed on UGC cl. 4.1 instead of AICTE norms | AICTE rules are loaded per discipline group; the engine selects the rule set from the school, and the reading is put to the mentor for confirmation |
| The 2025 draft Regulations are notified mid-project | Rules are versioned data; a new instrument is new rows |
| Free-tier quota blocks testing | Result cache; made-up resumes; the queue runs only when started; paid key before real use |
| The public form has no login, CSRF protection or rate limit | Phase 9; until then the server stays on 127.0.0.1 |
| Research Score needs evidence resumes lack | Labelled as claimed; verified at document check |
| Older commits on GitHub still contain tests naming real candidates (removed from current code in Phase 1) | Rewrite history only if the user asks |
| The new extracted fields have not been measured against the resumes | Each is kept only if the resume states it; a live run on real resumes before Phase 5 uses them |
| SQLite and PostgreSQL behave differently in places | Run the backend tests with `TEST_DATABASE_URL` set before each commit that touches the schema |

## Change log

| Date | Change |
|---|---|
| 2026-10-04 | Plan created after reviewing the Part 1 document and data model. |
| 2026-10-04 | Phase 1 built. PostgreSQL verification left open (not installed on the dev machine). Four detail tables and three state moves added beyond the document. Background running of the Reader moved from Phase 1 to Phase 3. |
| 2026-10-05 | PostgreSQL 16 installed natively (not Docker). Phase 1 verified on it and closed; a missing foreign key in the migration was found and fixed. |
| 2026-10-05 | Phase 2 built from the gazette PDFs. Added `relaxation_rules` and `score_rules` tables. Finding: UGC cl. 4 has no engineering section, so AICTE norms govern the technical schools and must be transcribed before Phase 5 can assess them. Five open questions added. |
| 2026-10-06 | AICTE (Degree) Regulation, 2019 loaded after the user supplied the HR portal's files. `rubric_rules` gained `discipline_group` and `criteria`. Mentor review document added. Differences between the State G.R. and the UGC gazette recorded. Phase 5 must now choose between two rule sets by school, and the home-made ranking's replacement (Table 3A) applies to UGC-governed posts only. |
| 2026-10-06 | Phase 3 built. The rule set is chosen by HR per opening. Reading is queued and started by a person, not automatic. HTMX deferred to Phase 4. Gate 1 (Phase 4) gains: entering form answers for HR uploads, resolving possible duplicates, re-queuing failed jobs. Reading moved off the request into the background after the first version made the page wait for every resume. |
| 2026-10-06 | Phase 4 built. Extraction prompt changed (every resume must be re-read). OCR decided: no. Gate 1 screen, `review_edits` audit table, duplicate resolution, withdraw and job retry added. NET/SET "none" is now checked against the resume text. HR uploads always stop at Gate 1 for the form answers. Institutions seeded from the workbook (four rows) and disciplines mapped to its list. Open: measuring the new fields against the resumes; HR's institutions list; whether a talk was abroad. |
| 2026-10-06 | Phase 5 built: `backend/engine/`, `assessor_service`, `evaluation_results.details`, the Assess button and the working on the application page. Requirements have three answers; unknowns go to MANUAL_REVIEW. Scores are ranges. Five readings are taken provisionally and listed for the mentor. Re-assessment after a correction moves to Phase 6. |
| 2026-10-06 | Phase 5 audited against the gazette files before commit. Fixed: cl. 3.11 on AICTE posts; research-scholar years counted as experience; the three-categories rule not enforced; cl. 4.1 applied to disciplines with their own clause; Table 3A post-doctoral experience. Five new opening choices for UGC cl. 4.2 to 4.6. A time-dependent Phase 3 test fixed. |
| 2026-10-06 | Phase 6 built: Gate 2 (approve, override with justification, send back for correction or re-assessment), `hr_decisions`, the outcome list, the full record on the application page, the Excel download. Gate 1 gains the Bachelor's marks; AICTE openings are no longer asked for NET/SET-only fields. The user chose to go on while the mentor's answers are pending. |
| 2026-10-07 | The new download was compared with the Reader-stage workbook and the 12 resumes. The scored fields agree. Fixed: an award date the model returned as a bare year ("2015") was dropped by the parser (4 of 12 Master's dates; restored from the stored model output); the Ph.D. course name now comes from the education row. Confirmed from the resume text that impact factor, funding amounts, research IDs and "held while studying" are blank because the resumes do not state them, and that authors are listed on only 2 of the 12 resumes (26 papers, all kept). All 12 were filed under a Professor opening, which is why 11 read "meets a lower post". |
| 2026-10-07 | HR uploads no longer stop at Gate 1 for form answers; fields can be reopened before assessment. Applications can be moved to another opening, singly or all at once. |
| 2026-10-07 | Phase 6 checked on the live data and closed. The user moved the 12 applications to an Assistant Professor opening: 11 meet the post, 1 needs a person (no class or marks stated). The experience shown beside an AICTE outcome now counts industry posts. Known and left: a post with no dates gives "at least 0" years; entering post dates at Gate 1 is not built. |
| 2026-10-07 | Phase 7 built: plain-language findings, the per-opening digest, candidate emails drafted on decision, Gate 3 approval, SMTP sending with a redirect safeguard, and optional model rewording behind a fact check. The explanation is written by code and not by the model. No mail server is configured, so nothing can be sent yet. |
| 2026-10-07 | The user set up a sending account and a catch-all address and sent two test copies through Gmail; both arrived. Two things came out of it: the panel did not say where an email would really go (now it does), and a test copy was recorded as sent for good, which would have stopped the real letter later (now a test copy leaves the email approved). |
| 2026-10-07 | Phase 8 built: the inbox channel over IMAP (read-only, on request), fixed-rule sorting with the rule recorded, held applications, and the Google Form channel through the same mailbox by a form script. No model sorts mail, and no opening is created from an email. One development mailbox is used for sending and reading for now; the college mailbox comes later. |
| 2026-10-08 | First live inbox test by the user. Two fixes from it: an emailed resume is no longer assumed to be about its sender (name and address come from the resume; the sender is shown for comparison), and a resume that lists dated teaching posts but states no total is no longer sent to Gate 1 for the total, since the engine counts the posts. The application page now shows teaching years counted from the dated posts. |
| 2026-10-08 | The user's live inbox tests led to five changes: the applicant comes from the resume and not the sender; no stop for a teaching total when posts are dated; the held row shows the message text, the resume, a suggested opening and a reply link; a resume already in the system is pointed out; the read button states its real count. |
| 2026-10-08 | Gap review of the whole application at the user's request, after live tests kept finding gaps. Twelve fixed (see "Gap review"); nine listed for the user's decision. |
| 2026-10-08 | Built at the user's choice: the date eligibility is counted on (per opening; the closing date unless HR names another; migration 0010), reading a resume again, and correcting or adding the posts held. The closing-date default is provisional until the mentor or HR confirms it. The Excel download does not yet show the counting date. |
| 2026-10-09 | Acknowledgement of receipt built. The user agreed that this one letter, which carries no finding, may be sent without a person's approval where the applicant typed their own address; for an emailed resume it is a draft a person approves. Migration 0011 (`email_drafts.kind`). A shared test fixture keeps every test away from a real mail server. |
| 2026-10-09 | From the user's live test of the acknowledgement: the home page and each opening's page now say how many letters (acknowledgements and decision letters) are drafts waiting for approval, with a link, since a waiting draft was easy to miss. Emailed resumes keep the tick; automatic sending for them was considered and left out because the address is read from the resume. Inbox: a duplicate the system refused is shown in the same red style as one waiting for a choice; promotional mailings (a noreply sender anywhere in the address, an unsubscribe header, or marked bulk) with no resume are no longer shown to HR. |
| 2026-10-09 | Phase 9 part 1 built: accounts, sign-in, sessions, the five kinds of account and three levels of grant from §17.6, access decided in one place for every HR page, each action recorded against the person, and reopening an HR decision not yet sent. Migration 0012. The first administrator is created by the user on a one-time setup page. Part 2 (interviewer view, highlights, policy layer, dashboard) is not started. |
| 2026-10-09 | Phase 9 completed in build: pages now offer only what an account may do; a forgotten-password email; and part 2: the interview panel's view driven by `view_field_visibility`, highlights with norms the university sets, the university's own criteria layered on the statutory finding without ever changing it, and the dashboard. Migration 0013. Contact details and category are withheld from the panel until the university decides otherwise. |
| 2026-10-09 | At the user's request the dashboard is the first page for administrators (the bare address takes them there, with what is waiting shown at its top); the openings list is at /hr/openings. An account tied to schools still starts on its openings, having no university-wide dashboard. |
