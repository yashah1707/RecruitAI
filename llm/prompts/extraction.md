# Extraction prompt

System instructions sent to the model alongside the resume text. Kept in this
file (not inlined in the provider) so prompt iteration does not require
touching provider code.

## Rules given to the model

- The "few-shot examples" section below teaches JSON FORMAT ONLY, using one
  fictional candidate. You MUST NOT copy any name, date, institution,
  subject, or evidence string from those examples into your real answer,
  even if it seems to fit the resume you were actually given. Every `value`
  and every `evidence` string in your real answer must come only from the
  ACTUAL RESUME TEXT supplied to you — not from the examples. If you cannot
  ground a field in the actual resume text, the correct answer is `null`;
  reusing an example's text instead of returning `null` is a serious error,
  because the evidence field exists specifically so a human reviewer can
  trust that a quote came from the real document, not from a template.
- Extract only the fields in the JSON schema. Never compute eligibility,
  never rank, never score, never decide an outcome.
- For every field, if the resume does not clearly state a value, return
  `null` for `value`, `0.0` for `confidence`, and `null` for `evidence`.
  Never infer a plausible-sounding default — this matters most for
  `study_leave_taken` and `phd_regulation`.
- `study_leave_taken` must be `true` ONLY if the resume uses an explicit word
  like "leave", "sabbatical", or "deputation" for the period a qualification
  was pursued. Phrases like "part-time", "concurrent with", "alongside
  full-time duties", or "while teaching" describe leave NOT being taken, or
  say nothing about leave at all — in both cases the correct value is `null`,
  never `true`. Do not treat "the resume doesn't mention leave" as evidence
  that leave was taken; absence of a leave statement is exactly the null case.
- For every non-null `value`, `evidence` must be a verbatim quote copied from
  the resume text — not a paraphrase, not a summary.
- `net_set_status` — these are three distinct qualifications, do not confuse
  them even though all three contain the word "Eligibility Test":
  - `NET`: a NATIONAL test — "UGC-NET", "CSIR-NET", or plain "NET" with no
    state name attached.
  - `SET`: a STATE test — the resume says "State Eligibility Test", "SET",
    or names a specific state's test (e.g. "Maharashtra SET", "K-SET",
    "Karnataka SET"). If you see the words "State Eligibility Test" or the
    standalone letters "SET" next to a state name, the value is `SET`, never
    `NET` — this is true even if the word "test" or "eligibility" elsewhere
    in the resume looks similar to a NET mention.
  - `SLET`: same as SET, just a different regional name for the same
    state-level test ("State Level Eligibility Test").
  - If the resume states a SET or SLET, `set_state` must name the specific
    state the test applies to. If the state cannot be determined, still
    return `net_set_status` but leave `set_state` null rather than guessing.
  - `NONE` STILL REQUIRES EVIDENCE. You cannot quote something that isn't
    there, so quote the heading of the section where a NET/SET would have
    been listed if the candidate had one — the qualifications or education
    heading, verbatim: "EDUCATIONAL QUALIFICATION", "Educational
    Qualifications:-", "Academic Education", "QUALIFICATION :",
    "EDUCATIONAL BACKGROUND", "Educational details:-". That shows you looked
    in the right place and found nothing, which is what makes a `NONE`
    trustworthy rather than merely asserted.
    - Copy the heading exactly as it appears, including its capitalisation
      and any trailing colon or dash.
    - Only if the resume genuinely has no qualifications or education
      section at all should `evidence` be null for a `NONE`.
- `highest_degree` is the highest qualification the candidate has COMPLETED:
  `UG` / `PG` / `PhD` / `Post-Doc` / `Diploma`. A qualification still in
  progress does not raise it -- someone with an M.Tech and an ongoing PhD is
  `PG`, not `PhD`. Use `Diploma` when a polytechnic/technical diploma is the
  highest completed qualification; do not round it up to `UG`.
- `phd_status` records WHERE in the doctoral process the candidate is, using
  exactly one of these values. Map what the resume states; never infer from
  how close to finished it sounds:
  - `COMPLETED` — awarded, "Ph.D. Completed", "awarded 2019", holds the degree.
  - `THESIS_SUBMITTED` — thesis submitted or "Thesis Submitted", awaiting the
    outcome. NOT completed.
  - `REGISTERED` — formally registered/enrolled but not yet actively writing,
    or stated as "Registered".
  - `PURSUING` — ongoing: "Pursuing", "Appearing", "PhD Scholar", "Persuing"
    (a common misspelling), or a stated expected-completion year.
  - `NOT_APPLICABLE` — the resume shows no doctoral study at all. A Ph.D.
    ENTRANCE test (PET) is not doctoral study; that is still NOT_APPLICABLE.
  `has_phd` must be `true` only when `phd_status` is `COMPLETED`, and `false`
  for every other value including `THESIS_SUBMITTED`.
- `teaching_years_raw` — the raw, unadjusted TEACHING total. Never subtract
  for leave or concurrency; that adjustment happens downstream. Decide
  between three answers in this order:
  1. The resume states a total ("TOTAL EXPERIENCE: 12.5", "15 Years of
     Academic Experience", "10+ years of teaching") -> use that number.
  2. No total is stated, but the resume lays out the candidate's work history
     and NONE of the roles are teaching/academic -> return `0`. A fresher
     whose objective is "to begin my academic career" and whose only jobs are
     industry roles genuinely has zero teaching years; that is a fact the
     resume establishes, not a gap.
  3. Teaching or academic roles ARE present but no total is stated ->
     return `null`. Do NOT add up date ranges yourself to manufacture a
     total; a human will supply it.
  Note the difference between 2 and 3: `0` means "this resume shows no
  teaching", `null` means "this resume doesn't say how much".
- `marks_pct` and `cgpa` both describe the marks for ONE degree: the
  candidate's MASTER'S degree (M.Tech / M.E. / M.Sc. / M.A. / M.Com etc.),
  or their Ph.D. if the resume gives marks for that instead. This is the
  qualification faculty eligibility is assessed on, so marks for any other
  level are the wrong answer:
  - IGNORE Bachelor's marks (B.E., B.Tech, B.Sc.) — do not put them here.
  - IGNORE school marks (H.S.C., S.S.C., 10th, 12th, Higher Secondary,
    CBSE, Intermediate) — these are never the right answer.
  - A resume typically lists several qualifications in a table with a
    percentage or CGPA against each. Find the Master's row and use only
    that row's figure.
  - If the resume gives no Master's marks at all, both fields are `null` —
    even when it clearly shows marks for a Bachelor's or for school.
  Then put that one figure on the correct scale, and NEVER convert between
  them (conversion factors differ by university, so converting would be a
  guess about a real person's marks):
  - `marks_pct`: a PERCENTAGE, 0-100 (e.g. "63.56", "First Class 61.12%").
  - `cgpa`: a GRADE POINT, 0-10 (e.g. "8.2 CGPA", "9.13", "8.79/10",
    "GPA: 7.78", "SGPA 7.5"). Indian resumes sometimes write a grade point
    with a stray percent sign, e.g. "8.2% M.E." — a value at or below 10
    next to a degree is a CGPA, not a percentage, so it belongs in `cgpa`.
  - Normally only ONE of the two is filled, because the Master's row states
    the marks in only one of these forms. Fill both only if that same
    Master's row genuinely gives both.
- Publications are split by how far through peer review each work is,
  because eligibility rules require "peer-reviewed or UGC-listed" work.
  Title only in both lists — leave out the journal, conference, year and
  page numbers.
  - `publication_titles` / `publications_count`: work that has CLEARED peer
    review. That means published, in print, or explicitly "accepted".
    "Accepted" counts here even though it is not yet in print — it has
    passed review, which is the substantive bar. "Early Access" / "in
    press" / "online first" also count: those are published ahead of the
    print issue.
  - `publications_in_progress_titles` / `publications_in_progress_count`:
    work that has NOT yet cleared review — "submitted", "under review",
    "communicated", "in preparation", "draft". Do not discard these and do
    not fold them into the counted list; a reviewer needs to see them.
  - Many CVs list the same work TWICE in different formats: once under a
    "Publications" heading and again inside a "Projects" / "Research Work" /
    "Level of participation" table. That is one publication, not two. List
    each distinct title once, in whichever list its status belongs to.
  - If the resume states no status for a work, treat it as published: a
    plain entry in a publications list is normally a published paper.
  - COUNT only authored written works. Exactly these three kinds:
    - journal articles,
    - conference papers,
    - book chapters and authored books.
    Book chapters DO count: they are substantive authored work, and a
    resume that lists them under a separate "Book Chapters" heading is not
    saying they are lesser, only that they are a different format.
  - Do NOT count, in either list:
    - patents, design registrations and copyright registrations (different
      kinds of output, usually under their own "Patents" / "Copyright"
      heading),
    - professional memberships and society fellowships,
    - conferences, workshops, FDPs, seminars or webinars ATTENDED,
    - reviewer, editor, session-chair or committee roles,
    - certifications, awards, projects and funded grants.
    A resume often lists these under headings that sit right next to the
    publications, so read the heading each entry falls under, not just its
    proximity to a journal name.
  - Either list may be empty; its count is then 0.
- Dates are `YYYY-MM-DD`. If only a year or month/year is known, use the
  first day of the known period and lower confidence accordingly.

## Few-shot example 1 of 2 — FORMAT ONLY, fictional candidate, DO NOT COPY

Resume excerpt:
```
Dr. Sampleperson Exampleton
M.Sc. Mathematics, Fictional Reference University, 2010 (62%)
Ph.D. in Mathematics, Fictional Reference University, awarded March 2016
UGC-NET (Mathematical Sciences), June 2012
Assistant Professor, Placeholder College, 2016–present
Publications: 3 papers in peer-reviewed journals
```

Expected extraction (abridged, illustrating the null-vs-grounded contrast):
```json
{
  "candidate_name": {"value": "Sampleperson Exampleton", "confidence": 0.95, "evidence": "Dr. Sampleperson Exampleton"},
  "has_phd": {"value": true, "confidence": 0.97, "evidence": "Ph.D. in Mathematics, Fictional Reference University, awarded March 2016"},
  "phd_award_date": {"value": "2016-03-01", "confidence": 0.85, "evidence": "awarded March 2016"},
  "phd_regulation": {"value": null, "confidence": 0.0, "evidence": null},
  "net_set_status": {"value": "NET", "confidence": 0.95, "evidence": "UGC-NET (Mathematical Sciences), June 2012"},
  "set_state": {"value": null, "confidence": 0.0, "evidence": null},
  "study_leave_taken": {"value": null, "confidence": 0.0, "evidence": null}
}
```

Note `phd_regulation`, `set_state`, and `study_leave_taken` are all null here
— the excerpt never states them, so they must not be guessed even though a
plausible value (e.g. "2016" for `phd_regulation`, matching the award year)
might seem tempting.

## Few-shot example 2 of 2 — FORMAT ONLY, fictional, the `study_leave_taken` trap

This is the case models most often get wrong: a Ph.D. pursued while teaching
full-time, with no leave word anywhere. It is easy to misread "pursued
part-time alongside full-time duties" as implying leave was taken — it means
the opposite (or, at minimum, it is silent on leave), so the correct value is
still `null`, not `true`.

Resume excerpt:
```
Assistant Professor, Placeholder College, 2013-present (11 years, continuous)
Pursued Ph.D. part-time alongside full-time teaching duties, 2016-2021
```

Expected extraction (this field only):
```json
{
  "study_leave_taken": {"value": null, "confidence": 0.0, "evidence": null}
}
```

## Reminder before you answer

The two examples above are fictional and exist only to show JSON format and
null-handling. "Sampleperson Exampleton", "Fictional Reference University",
"Placeholder College", and their dates/evidence text do not appear in the
real resume you are about to process — if your real answer contains any of
that text, or the specific string "UGC-NET (Mathematical Sciences), June
2012", you have copied from the example instead of reading the actual
document, and must redo the field as `null` unless the real resume text
genuinely contains equivalent wording of its own.
