# RecruitAI

A web application that screens faculty applications at MIT Art, Design and Technology University
against the statutory minimum qualifications: the UGC Regulations, 2018 (with their 2023 and 2024
amendments) and, for technical schools, the AICTE (Degree) Regulation, 2019.

It does three separate jobs, and keeps them separate on purpose:

1. **A language model reads.** Google Gemini turns each resume into structured fields (degrees,
   marks, NET/SET, Ph.D., posts held, publications) and quotes the line of the resume each value
   came from. Every value is checked against the resume text before it is kept.
2. **Rules decide.** A Python rule engine checks those facts against the regulations, which are
   stored in the database with their clause, gazette page and version. Each requirement is met,
   not met, or cannot be told from the resume; anything that cannot be told goes to a person.
   The language model never does arithmetic and never decides an outcome.
3. **People approve.** Nothing reaches a candidate without a person at three gates: checking
   uncertain fields, approving or overriding the outcome, and approving each letter.

The result is a short-list for interview, never a ranking and never a selection. Selection stays
with the Selection Committee.

The full build plan, every decision and its reason, and the change log are in
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). How to use the pages is in
[docs/USER_MANUAL.md](docs/USER_MANUAL.md).

## What it does

- **Openings.** HR creates an opening for a school, department and post, and chooses the rule set
  (UGC, or an AICTE discipline). Each opening has a link for applicants.
- **Four ways in.** The online application form, HR upload of many resumes, an email inbox
  (read over IMAP, read-only), and a Google Form that reaches the same mailbox.
- **Reading.** A queue reads resumes when a person starts it, with retries and a second model to
  fall back on. Scanned resumes with no text are reported, never sent to the model.
- **Gate 1, field check.** Only the uncertain fields are shown, each with its reason and the quoted
  line. Every correction is recorded.
- **Assessment.** NET/SET/SLET (State validity, Ph.D. exemption), marks with category and pre-1991
  relaxations, experience under cl. 3.11, publications, Ph.D. guidance, the Research Score and the
  short-listing score (as ranges), AICTE First Class from class, marks or grade point. A candidate
  who misses the post applied for is checked for each lower post.
- **Gate 2, HR decision.** Approve, override with a written reason, or send back. An override
  never erases the engine's finding.
- **Gate 3, letters.** Drafted by code from the stored working; sent only after a person approves.
  A test mode sends every email to one address instead of the candidate.
- **Reporting.** A plain-language finding per application, a printable digest per opening, an
  Excel workbook, and a university dashboard that names and ranks no one.
- **Access.** Five kinds of account (university administrator, HR administrator, school HR,
  department HR, interviewer) with view, edit or approve rights per school or department, and an
  audit trail of every action. The interview panel sees only short-listed candidates, without
  contact details or category.
- **Protection.** Stored resumes and backups can be encrypted; the log never holds personal data.

## Setting it up

Needs Python 3.13 and PostgreSQL 16.

1. Install the Python packages:

   ```
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill it in. The essentials are `DATABASE_URL` and
   `GEMINI_API_KEY`; everything else is optional and explained in the file.

3. Create an encryption key for stored resumes and backups (recommended):

   ```
   python -m backend.storage --new-key
   ```

   The key is written into `.env`. **Copy that line somewhere safe, away from this computer.**
   Without it, encrypted resumes and backups cannot be opened.

4. Start the application. On Windows, double-click `run_app.bat`: it brings the database up to
   date, loads the schools and the statutory rules, and starts the server. By hand:

   ```
   alembic upgrade head
   python -m backend.seed
   python -m backend.rules_seed
   uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```

5. Open http://127.0.0.1:8000. The first visit asks for the first university administrator
   account; after that the page closes for good and other accounts are made under **Accounts**.
   Applicants use http://127.0.0.1:8000/apply and need no account.

For a local PostgreSQL in Docker, `docker compose up -d` starts one with throwaway credentials.

The server listens only on this computer (127.0.0.1). Putting it on a network needs HTTPS and a
public address (`PUBLIC_BASE_URL`); that is Phase 10 of the plan and is waiting for the hosting
decision.

## Everyday commands

| Command | What it does |
|---|---|
| `python -m backend.worker` | Read every waiting resume, then stop (`--loop` keeps running) |
| `python -m backend.inbox` | Check the application mailbox once |
| `python -m backend.backup` | Back up the database and resumes (`--list`, `--open FILE DIR`) |
| `backup_recruitai.bat` | The same backup, for a double-click or Windows Task Scheduler |
| `python -m backend.storage --status` | Count encrypted and unencrypted resume files |
| `python -m backend.storage --encrypt-existing` | Encrypt resumes stored before the key was set |
| `python -m backend.institutions FILE.csv` | Load HR's list of institutions and their tiers |
| `python -m backend.rules_seed --write-transcription` | Regenerate the rules transcription sheet |
| `python tools/make_mentor_review_docx.py` | Regenerate the mentor's Word copy of the rules |

Each resume read is one request to the language model. Gemini's free tier allows about 20 requests
a day per model; the default `GEMINI_MODEL=auto` alternates between two models and moves to the
other when one runs out. **The free tier lets Google use what is sent for training**, so a paid key
with suitable terms is needed before real candidates' resumes are read at scale.
`LLM_PROVIDER=fake` runs everything with canned results and no model at all.

## Configuration

Read from the environment and `.env` at start-up. All optional except where noted.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local PostgreSQL | Any SQLAlchemy URL; SQLite works for trying it out |
| `GEMINI_API_KEY` | (empty) | Needed when `LLM_PROVIDER=gemini` |
| `LLM_PROVIDER` | `gemini` | `gemini` or `fake` |
| `GEMINI_MODEL` | `auto` | `auto` rotates `GEMINI_MODEL_POOL`; or name one model |
| `GEMINI_MODEL_POOL` | `gemini-3.5-flash,gemini-3.6-flash` | Models `auto` rotates and fails over between |
| `DATA_ENCRYPTION_KEY` | (empty) | Encrypts stored resumes and backups |
| `STORAGE_DIR` | `storage/resumes` | Where resumes are kept |
| `LOG_DIR` | `logs` | Where `recruitai.log` is written |
| `BACKUP_DIR`, `BACKUP_KEEP` | `backups`, `14` | Where backups go and how many are kept |
| `PG_BIN` | (found automatically) | The PostgreSQL `bin` folder, if `pg_dump` is not found |
| `INSTITUTION_STATE` | `Maharashtra` | The State whose SET/SLET is valid here |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | (empty) | Sending letters; nothing is sent without them |
| `EMAIL_REDIRECT_TO` | (empty) | Test mode: every email goes here, never to a candidate |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | (empty) | The application mailbox |
| `GOOGLE_FORM_SENDERS` | the mailbox's own address | Who a Google Form response may come from |
| `ACKNOWLEDGE_APPLICATIONS` | `true` | Send an acknowledgement of receipt |
| `PUBLIC_BASE_URL` | (empty) | The address used in emailed password-reset links |

Google Form set-up: [docs/GOOGLE_FORM_SETUP.md](docs/GOOGLE_FORM_SETUP.md).

## The statutory rules

The rules live in `backend/rules_data.py`, transcribed from the gazette PDFs (each file's source
address and SHA-256 are recorded). `docs/ugc_rules_transcription.md` and
`docs/UGC_Rules_Mentor_Review.docx` are generated from the same data for a person to check against
the gazette, and a test fails if the sheet and the data ever differ. The transcription was approved
by the project guide in November 2026. Where the Regulations leave a point open (for example,
whether impact-factor points add to a paper's score), the guide's decision is recorded with the
point in `rules_data.OPEN_POINT_DECISIONS` and printed in the sheet. Where a resume leaves a fact
out, the engine reports a range or sends the case to a person rather than guessing.

## Tests

```
python -m pytest -q
```

About a thousand tests, none of which call the language model or a mail server: a fake reader and a
stand-in mail transport are used. The backend tests also run against PostgreSQL:

```
set TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/recruitai_test
python -m pytest tests -k backend -q
```

(the test database is emptied by the tests; never point it at the real one).
`tests/test_extraction_accuracy.py` reads the made-up resumes in `tests/fixtures/synthetic_resumes/`
with the real model and reports accuracy per field. It makes one model request per resume, so it
runs only when asked for:

```
set RUN_LIVE_GEMINI=1
python -m pytest tests/test_extraction_accuracy.py -s -q
```

## Layout

| Folder | What is in it |
|---|---|
| `backend/` | The web application: models, intake, reader, rule engine (`engine/`), gates, emails, access, pages |
| `backend/templates/`, `backend/static/` | The pages and their design system |
| `llm/` | The language-model interface, prompts, the Gemini provider and the fake one |
| `app/` | Resume text extraction and the result cache; also the first Streamlit prototype (`app/dashboard.py`) |
| `migrations/` | Alembic database migrations |
| `docs/` | Plan, user manual, rules transcription, Google Form set-up |
| `tests/` | Automated tests and made-up resumes |
| `tools/` | Helpers for the rules sheet and accuracy scoring |

## Privacy

- Resumes, the database, `.env`, logs, backups and the result cache are personal data and are never
  committed (see `.gitignore`).
- Logs record ids, states and kinds of error, never names, addresses or resume text.
- No real candidate's data appears in tests, fixtures or commits.
- Category, disability status, State and contact details are withheld from the interview panel
  until the university decides otherwise.
