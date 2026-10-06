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
  - Its `evidence` must be ONLY the degree name and its course/specialisation,
    copied verbatim — nothing else from the row. Leave out marks, percentage,
    CGPA, class/division ("First Class"), year, university, college, city, and
    any other qualification (an ongoing or submitted Ph.D. does not belong in
    the evidence for a `PG`).
    - A row reading `M.Tech. (Computer) First Class 63.56 2015` is quoted as
      `M.Tech. (Computer)`.
    - If the degree and its course are separated by other words in the resume
      (an institution or a year sitting between them), quote the two parts
      joined by ` ... ` — e.g. `M.Tech ... Computer Science & Engineering` —
      rather than quoting the words in between.
    - ALWAYS look for the course/specialisation and include it. It is often
      NOT on the same line as the degree: it may be on the next line, in the
      next table cell, or in a separate "Specialization"/"Branch"/"Subject"
      column of the same row. A row reading `Master of Engineering (M.E)` with
      `Computer Engineering` on the line below is quoted as
      `Master of Engineering (M.E) Computer Engineering`. Quote the degree
      alone only when the resume states no course for it anywhere.
    - Copy the words exactly as the resume spells them, abbreviations
      included; do not expand or tidy them.
    - If the resume gives only the degree with no course, quote just the
      degree.
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
  - The `evidence` for `phd_status` (when it is not `NOT_APPLICABLE`) must be
    ONLY the Ph.D., its course/specialisation, and the word(s) stating the
    status, copied verbatim — e.g. `Ph. D. (Computer Engineering) Appearing`.
    Leave out the university, college, city, guide's name, and years. The
    course may be on a different line or table cell from the words "Ph.D.";
    look for it and include it, joining separated parts with ` ... `.
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

## Detail lists

Besides the fields above, return the plain lists below. These have no confidence
or evidence; instead every text value must be COPIED from the resume, because
each item is checked against the resume text afterwards and an item that
cannot be found there is marked as unverified. Do not tidy, expand, translate
or re-spell anything — formatting is done later by code. Use `null` for any
part the resume does not state; never fill a gap with a plausible guess. List
each item once even if the resume mentions it in two places. Return an empty
list `[]` when the resume has nothing of that kind.

- `education` — one entry for each of the candidate's Bachelor's (`UG`),
  Master's (`PG`) and Ph.D. (`PhD`) qualifications, including a Ph.D. that is
  still in progress. Leave out school (10th/12th) and diplomas. If the
  candidate has two Master's degrees, list both.
  - `degree`: the degree as written ("Master of Engineering (M.E)", "B.Tech").
  - `course`: the specialisation/branch ("Computer Engineering"). It is often
    on a different line or table cell from the degree — find it.
  - `college`: the college/institute attended. `university`: the awarding
    university. If the resume names only one institution, put it in
    `university` and leave `college` null.
  - `marks_pct` (0-100) and `cgpa` (0-10): the marks for THAT degree's own
    row, on whichever scale it is stated; never convert, never borrow a
    figure from another row. `division`: the class/division as written
    ("First Class with Distinction", "First").
  - `completion`: when the degree was completed/awarded, as `YYYY`,
    `YYYY-MM` or `YYYY-MM-DD` — only as precise as the resume states. `null`
    if not completed or not stated.
  - Ph.D. only: `thesis_title` (title or research topic/area), `guide`
    (supervisor's name), `registration` (when registered/enrolled, same
    format as `completion`). `null` for other degrees.
- `publications` — one entry per paper, book chapter or book. Not patents,
  not conferences merely attended.
  - `title`: the paper's title only, without authors, venue or year.
  - `kind`: `JOURNAL`, `CONFERENCE`, `BOOK_CHAPTER`, `BOOK`, or `OTHER`.
  - `venue`: the journal, conference or publisher name. `year`: `YYYY`.
  - `status`: `PUBLISHED`, `ACCEPTED`, `UNDER_REVIEW`, `SUBMITTED` or
    `IN_PREPARATION`. Use `PUBLISHED` unless the resume says otherwise.
  - `indexing`: "Scopus", "SCI", "Web of Science", "UGC CARE" etc., only if
    the resume states it for that paper.
  - `authors`: the author names the resume lists for THAT paper, one name per
    item, in the order written and spelled exactly as written ("S. K.
    Exampleton", "Exampleton S."). Include the candidate. `[]` when the
    resume gives the paper without an author list. Do not count them.
  - `is_first_author`: `true` if the candidate's own name is the first name
    in that author list, `false` if it is there but not first. `null`
    whenever `authors` is `[]`. Never assume the candidate is first.
  - `impact_factor`: the impact factor only if the resume states one for
    that paper or its journal ("IF: 3.2", "Impact Factor 5.01"). Copy the
    number. `null` otherwise; never supply one you happen to know.
- `events` — one entry per FDP, STTP, workshop, seminar, webinar, conference,
  training programme or certification course (NPTEL, Coursera and similar).
  - `kind`: `FDP`, `STTP`, `WORKSHOP`, `SEMINAR`, `WEBINAR`, `CONFERENCE`,
    `TRAINING`, `COURSE`, or `OTHER`.
  - `title`: the name/topic of the event, without the organiser or dates.
  - `role`: `ATTENDED`, `ORGANISED` (coordinator/convener), `RESOURCE_PERSON`
    (delivered the session), `PRESENTED` (presented a paper), or `OTHER`.
  - `organiser`: the institution or body that ran it. `duration`: as stated
    ("5 days", "One Week", "12 weeks"). `year`: `YYYY`.
  - `level`: `INTERNATIONAL`, `NATIONAL`, `STATE` or `UNIVERSITY`, only when
    the resume uses that word for the event ("International Conference
    on ...", "National Level Workshop", "State Level Seminar") or lists it
    under a heading that does. `null` otherwise. A foreign-sounding name or
    a well-known organiser is not a stated level.
- `subjects` — the subjects/courses the candidate has taught, one entry
  each. Not their research areas.
  - `name`: the subject as named in the resume.
  - `level`: `UG`, `PG`, `PhD` or `Diploma`, only when the resume says which
    programme the subject was taught to ("Subjects taught (M.Tech): ...",
    "UG: Data Structures"). `null` when it does not say. Do not guess the
    level from how advanced the subject sounds.
- `skills` — technical skills: programming languages, tools, software,
  platforms, one per item. Not soft skills ("hardworking", "team player").
- `experience` — one entry per job/post held, most recent first. A promotion
  at the same institution (Lecturer, then Assistant Professor) is two entries.
  - `designation`: the post as written. `institution`: the employer's name
    only, without department, city or dates.
  - `kind`: `TEACHING` (any teaching post at a college/university),
    `INDUSTRY`, `RESEARCH` (research fellow, project staff), or `OTHER`.
  - `start` and `end`: as `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, only as precise
    as the resume states. For a current post ("till date", "present",
    "working") set `end` to `PRESENT`. `null` when not stated.
  - `duration`: only if the resume itself states one for that post
    ("3 years 2 months"); copy it. Never calculate it.
  - `concurrent_with_study`: `true` ONLY when the resume says in words that
    this post was held while the candidate was studying for a degree
    ("pursued Ph.D. part-time while working as Assistant Professor",
    "in-service Ph.D."). `null` in every other case. Never work it out by
    comparing the dates of the post with the dates of a degree, and never
    return `false`.
- `achievements` — patents, awards/honours, funded research projects and
  grants, one entry each.
  - `kind`: `PATENT`, `AWARD`, `FUNDED_PROJECT`, `GRANT`, or `OTHER`.
  - `title`: the name of the patent, award or project.
  - `details`: funding agency and amount, patent/application number, or the
    awarding body, as stated. `year`: `YYYY`. `status`: as stated ("Granted",
    "Published", "Filed", "Ongoing", "Completed").
  - `amount`: for a funded project or grant, the amount of money exactly as
    the resume writes it, with its currency and unit ("Rs. 12.5 Lakhs",
    "INR 5,00,000"). Copy it; do not convert it or change the unit. `null`
    when no amount is stated or the entry is not a project or grant.
  - `level`: `INTERNATIONAL`, `NATIONAL`, `STATE` or `UNIVERSITY`, under the
    same rule as for `events`: only when the resume uses the word for this
    award, patent or project. `null` otherwise.
- `guidance` — research/project supervision the candidate has done, one entry
  per statement ("Guided 12 M.E. dissertations", "2 Ph.D. scholars pursuing").
  - `level`: `PHD`, `PG`, `UG`, or `OTHER`. `description`: the statement as
    written. `count`: the number of students only if the resume states it.
- `memberships` — professional body memberships, one per item, as written
  ("Life Member, ISTE", "IEEE Member").
- `email` and `phone` — the candidate's own email address and phone number,
  copied exactly. `null` if not given. If several are listed, the first.
- `state` — the Indian State or Union Territory in the candidate's own
  postal address, only when the address itself names it ("Pune,
  Maharashtra"). Copy the name as written. `null` when the address gives a
  city or PIN code without a State, or there is no address: do not work the
  State out from the city, the PIN code, the college or a SET certificate.
- `research_profile` — what the candidate states about their own research
  record. Every part is `null` unless the resume states it.
  - `scopus_author_id`, `orcid_id`, `google_scholar_id`: the identifier
    itself, copied exactly ("0000-0002-1825-0097"); for Google Scholar, the
    user id or the profile link as written.
  - `total_citations`, `h_index`, `i10_index`: the numbers the resume
    states ("Citations: 214", "h-index: 8"). Copy them; never count
    citations or work out an index yourself.

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
