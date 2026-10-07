# RecruitAI — Implementation Plan

The living plan for taking RecruitAI from the current Reader stage to the full
system described in *RecruitAI — The Academic Hiring Agent (Final, Part 1)*
and its data-model workbook. Section numbers below (§) refer to that document.

**This file is the single place the plan is tracked.** When scope, order or a
decision changes, this file is edited in the same commit and the change is
noted in the [Change log](#change-log).

- Last updated: 2026-10-07
- Current phase: **Phase 6 — HR dashboard and Gate 2** (done and merged). Next: Phase 7, reporting and Gate 3

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
| 2026-10-06 | No OCR for now: a scanned resume is reported under Needs attention and a readable copy is asked for. |
| 2026-10-06 | The extraction prompt was changed once, with the user's agreement, for all Phase 4 fields together. |
| 2026-10-06 | An HR upload always stops at Gate 1 for the form answers; they are never taken from the resume. |
| 2026-10-07 | Reversed in part: an HR upload is not held for the form answers. They are still never taken from the resume; a person enters them, and the engine asks for one only when it would change the outcome. |
| 2026-10-06 | The engine answers met, not met or cannot be told. Anything it cannot tell goes to a person; it is never rounded. |
| 2026-10-06 | Until the mentor rules, the open readings are taken as listed under Phase 5, and scores are given as ranges. |
| 2026-10-06 | Disciplines with their own UGC clause (4.2 to 4.6) are chosen as such on the opening and assessed by a person until their rules are loaded. |
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
| Does the university assess technical-school posts under AICTE cl. 5.1/5.2 and not UGC cl. 4.1? (mentor sheet, Section 11) | Phase 5 |
| Which later AICTE clarifications apply; they have not been read | Phase 5 |
| Does any relaxation or short-listing score apply to AICTE-governed posts? The Regulation has neither | Phase 5 |
| How to read a CGPA between or above the five points AICTE cl. 7.3 lists | Phase 5 |
| Should resumes be read automatically on arrival once a paid key is in use? | Before go-live |
| Table 2: do impact-factor points add to or replace the 8/10 per paper? | Phase 5 (Research Score) |
| Table 3A: does an M.Tech/M.E. score as Post-Graduation, under S.No. 3, or both? | Phase 5 (short-listing score) |
| How is a CGPA placed in the percentage bands of Table 3A? | Phase 5 |
| Load UGC cl. 4.2 to 4.6 (music, arts, drama, yoga, therapy)? Needed only if those schools recruit through the system | Phase 6 or later |
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
