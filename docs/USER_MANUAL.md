# RecruitAI user manual

For the people who use RecruitAI day to day: HR staff, administrators and interview panel members.
How to install and run it is in the [README](../README.md).

RecruitAI checks each faculty application against the UGC and AICTE minimum qualifications and
tells you, with the clause and gazette page, whether the candidate meets them. **It never selects
anyone.** It gives a short-list for interview; the Selection Committee decides. Every finding is
approved or changed by a person, and no candidate receives anything you have not approved.

---

## 1. Signing in

- Open the address you were given (on the university computer: http://127.0.0.1:8000) and sign in
  with your email address and password.
- The first time, you will be asked to change the temporary password you were given. Passwords
  need at least 10 characters.
- After five wrong passwords, signing in is closed for 15 minutes.
- You are signed out after 8 hours without use. Use **Sign out** in the menu under your name when
  you leave the computer.
- Forgot your password? Use **Forgot password?** on the sign-in page; a link valid for 30 minutes is
  emailed to you. If email is not set up, ask a university administrator for a temporary password.

**What you see depends on your account.** A school or department account sees only its own
openings. "View" can look; "View and edit" can also upload, read, correct and assess; "View, edit
and approve" can also record decisions and approve letters. A page shows only the buttons your
account may use.

---

## 2. The path of an application

```
Received → Read → (Field check, if needed) → Assessed → HR decision → Letter approved → Contacted
```

| Status you see | What it means | What to do |
|---|---|---|
| Waiting to be read | In the reading queue | Run the queue (section 4) |
| Fields to check | The reader was unsure of some fields | Check them (section 5) |
| Ready to be assessed | Read and checked | Assess the opening (section 6) |
| Meets the post / Short-listed | Every requirement met | Approve or override (section 7) |
| Re-categorised | Meets a lower post, not the one applied for | Approve or override |
| Not eligible | A requirement is plainly not met | Approve or override |
| Needs a decision | The rules could not settle it from the resume | Decide yourself (section 7) |
| Decided | HR has recorded a decision | Approve the letter (section 8) |
| Unreadable | A scanned or damaged file | Ask the applicant for a readable PDF or DOCX |

---

## 3. Openings

Every application belongs to an opening: one post in one school (and department).

**Create one:** **Openings → New opening.** Choose the school, department, designation and **the
rules that apply**. The form suggests a rule set from the school; check it. For engineering,
technology, management, pharmacy and other technical schools choose the matching **AICTE**
discipline; for arts, sciences, commerce, humanities and law choose **UGC**. This choice decides
which regulation every applicant is judged under.

Also set the **closing date**, and an **eligibility date** if the advertisement names one.
Experience, degrees and publications are counted up to the eligibility date, or the closing date
if none is given.

**On the opening's page** you find:

- **Its application form**: the link to give applicants, with **Copy**.
- **Upload**: add resumes you already hold (several at once).
- The list of applications, in the order they arrived. It is never a ranking.
- **Digest**, **Emails to candidates**, **Download as Excel**.
- **Edit this opening**, **Close this opening** / **Reopen this opening**.

Changing an opening's rule set or eligibility date sends every undecided application back to be
assessed again. Decisions already recorded are kept.

---

## 4. Getting applications in

| Channel | How it arrives |
|---|---|
| Application form | The applicant fills it in at the opening's link and uploads a resume |
| HR upload | **Upload** on the opening's page; you will be asked for category and study leave later, only if they matter |
| Email | Applicants email a resume and name the opening reference (e.g. OPN-00003) in the subject. **Inbox → Check inbox** collects them |
| Google Form | Responses reach the same mailbox; see `docs/GOOGLE_FORM_SETUP.md` |

**Inbox.** An emailed resume that names no opening waits under **Waiting for you**. Read the message,
open the resume, choose the opening and press **File under this opening**, or **Set aside**. A reply
to one of our letters is attached to that application, not filed as a new one. The mailbox is only
read, never changed.

**Reading.** New applications wait in the reading queue on the **Openings** page. Press **Process
queue** to read them. Each resume is one request to the language model, so the queue never runs
on its own. If the model is busy, the application is tried again later; if it keeps failing it
appears under **Needs attention** with **Try again**.

Duplicates are caught: the same email cannot apply twice to one opening, and the same file is never
taken twice. If an uploaded resume looks like a person already on record, you are asked: **Same
person: join the records** or **Different person: keep apart**.

---

## 5. Field check (Gate 1)

When the reader is unsure, the application shows **Fields to check**. Press **Check the fields**.

- Only the uncertain fields are shown, each with the reason and the line of the resume the reader
  used. **Open the resume** to read the original.
- Enter or confirm the value, or tick **not stated; leave empty** when the resume really does not
  say. Never guess: an empty field is sent to a person later, which is the safe outcome.
- Press **Save and finish the check**. Every change is recorded with the value before and after.

**Category, State, disability and study leave** always come from the applicant or from you, never
from the resume. For uploads and emailed resumes, the application page lists what is not on record
and offers **Enter them now**. You only need them when the page says they would change the outcome.

Other corrections: **Open these fields** reopens any field; **Save posts** corrects the posts held
and their dates; **Read this resume again** re-reads it from the start.

---

## 6. Assessment

On the opening's page press **Assess N read application(s)**. No language model is used: the
rules are applied by fixed code, the same way every time.

The application page then shows, under **Assessment**:

- the outcome, in one plain sentence;
- every requirement, with **Met**, **Not met** or **Open** (cannot be told), the figures compared,
  the **clause** and the **gazette page**;
- for a candidate who missed the post applied for, each lower post that was checked.

**Open** never means "failed". It means the resume does not say enough, for example a SET with no
State, or a degree with no class or marks. The page names exactly what is missing. Scores (the
Research Score and the short-listing score) are shown as a range when the resume leaves details
out; they are marked as claimed until documents are checked.

The **university's own criteria** (if any are set under **University criteria**) are shown beside
the statutory finding. They never change it.

---

## 7. HR decision (Gate 2)

Needs a "View, edit and approve" account. On the application page press **Decide**:

- **Approve as assessed**: accept the engine's finding.
- **Or override it** (or **Decide it yourself** when the finding is "Needs a decision"): choose the
  outcome and post, and write the reason (at least 15 characters). The reason is kept with the
  decision and is never sent to the candidate. The engine's finding stays on record beside yours.
- **Send back**: either to correct named fields first (the fields that would settle it are ticked
  for you), or to be assessed again as it stands.

A decision not yet sent to the candidate can be undone with **Reopen the decision** and a reason.
Once the candidate has been told, it cannot.

---

## 8. Letters to candidates (Gate 3)

Recording a decision drafts a letter: short-listed for the post, short-listed for a lower post, or
not meeting the minimum qualifications (with the reasons, clause and page, and seven days to reply
with documents).

- On the application page press **Review the email**. Edit the subject and text if needed, then
  **Save changes**.
- Where you overrode the engine, the letter has a part marked `[HR: ...]` that you must write
  yourself before it can be approved.
- **Reword with the language model** offers smoother wording (one model request). It is kept only
  if every reference, post, clause and figure is unchanged.
- **Approve** (or **Approve and send** when mail is set up). Approving alone sends nothing until a
  mail server is configured. Changing an approved letter needs approval again.
- **Emails to candidates** on the opening's page lists every letter. Tick the ones you approve and
  press **Approve the ticked emails**.

**Test mode.** While the system is being tried out, a yellow **Test mode** box shows the one address
every email goes to instead of the candidate. In test mode no candidate is ever written to.

An acknowledgement of receipt goes automatically to applicants who used the application form or
the Google Form. It says nothing about eligibility.

---

## 9. Reports

- **Digest** (opening page): every application grouped by where it stands, with its finding and
  decision. Print it or save it as PDF from the browser.
- **Download as Excel** (opening page): the full record of every application, with findings,
  clauses and decisions. Listed in order of receipt, with no rank.
- **Dashboard** (administrators): counts by school, opening and channel. No candidate is named.

---

## 10. Interview panel

Panel members sign in and see **Short-listed candidates**: only the candidates HR has short-listed,
in the schools they are granted, read-only. Contact details, category, disability status and State
are hidden, as is the resume file, until the university decides otherwise. Highlights (a Ph.D. from
a premier institute, strong citations) are pointers only; they score nothing.

---

## 11. For administrators

**Accounts.** **Accounts → Add an account.** Choose the kind (university administrator, HR
administrator, school HR, department HR, interviewer) and give a temporary password; the person
changes it at first sign-in. Then **Grant** a school (or one department) and a level. Accounts can
be closed and reopened; nothing is deleted. **What the interview panel sees** sets which parts of a
record the panel can view.

**University criteria.** Add figures the university asks for over the statutory minimum (Master's
marks, experience, publications, h-index, citations, Ph.D. required), for one school or all. They
are reported beside the statutory finding and never change it. Highlight norms are set on the same
page.

**Backups.** Double-click `backup_recruitai.bat`, or schedule it nightly in Windows Task Scheduler
(Action: start `backup_recruitai.bat` with the argument `scheduled`, so it does not wait for a key
press). Backups go to the `backups` folder; the last 14 are kept. With the encryption key set they
are encrypted. **Copy backups to another drive regularly**: a backup on the same disk does not
survive that disk failing.

**Restoring a backup** (with an IT person; this replaces the current data):

1. Stop the application.
2. `python -m backend.backup --open backups\<file> restore` unpacks it into a `restore` folder.
3. `pg_restore --clean --if-exists -d recruitai restore\database.dump` (add `-U` and `-h` as for
   your database).
4. Copy the files in `restore\resumes` into `storage\resumes`.
5. Start the application, check it, then delete the `restore` folder: it holds personal data.

**The encryption key.** `DATA_ENCRYPTION_KEY` in `.env` protects stored resumes and backups. Keep a
copy away from the computer (for example, sealed with the university's IT office). **If it is lost,
encrypted resumes and backups cannot be opened by anyone.** If it is missing or wrong, resumes
cannot be opened or read and the pages say so; nothing is lost, and putting the right key back fixes
it. Resumes stored before the key was set are encrypted with
`python -m backend.storage --encrypt-existing`.

**The log.** `logs\recruitai.log` records events and errors (ids, states and the kind of error,
never names, addresses or resume text). Look there first when something fails.

---

## 12. Good practice

- Read the clause and the figures before approving. The engine is a careful assistant, not the
  authority; you are.
- When a finding says **Open**, look at the original resume or ask the candidate. Do not guess.
- Keep test mode on until the university has confirmed the mail account and the letters.
- Sign out when you leave the computer. Pages are not kept by the browser after you sign out.
