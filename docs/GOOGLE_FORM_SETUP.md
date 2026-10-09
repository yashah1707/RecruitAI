# Taking applications through a Google Form

RecruitAI does not log in to Google Forms, Sheets or Drive. Instead, a short
script attached to the form emails each response, with the uploaded resume, to
the mailbox RecruitAI already reads. That keeps one channel to look after and
needs no Google Cloud project or API key.

One form serves one opening. Make a copy of the form for each opening.

## 1. Sign in as the mailbox account

Create the form while signed in to Google as the account whose mailbox
RecruitAI reads (the `IMAP_USER` in `.env`). A form response is trusted only
when it comes from that address. If the form has to belong to another account,
add that account's address to `.env`:

```
GOOGLE_FORM_SENDERS=forms.owner@example.org
```

## 2. Create the form

Add these questions, with these exact titles, and mark every one **Required**.
The answers are checked the same way as RecruitAI's own application form, so a
choice spelled differently is refused and shown to HR.

| Question title | Type | Choices |
|---|---|---|
| Full name | Short answer | |
| Email | Short answer | |
| Phone | Short answer | |
| State | Dropdown | the States and Union Territories as listed in `backend/lists.py`, and `Outside India` |
| Category | Dropdown | `General`, `SC`, `ST`, `OBC-NCL`, `EWS`, `PwD` |
| Differently abled | Multiple choice | `Yes`, `No` |
| Study leave | Multiple choice | `Yes`, `No`, `Not applicable` |
| Resume | File upload | allow **PDF** and **Document**; maximum 1 file; 10 MB |
| Declaration | Checkboxes | `I declare that the information given is true.` |

A file-upload question makes Google ask applicants to sign in to a Google
account. That is Google's rule, not RecruitAI's.

"Study leave" asks whether study leave was taken for an M.Phil. or Ph.D. while
in service.

## 3. Add the script

In the form, open the three-dot menu, then **Script editor**. Delete what is
there, paste the script below, and change the two lines at the top.

```javascript
// The opening this form is for, as shown on RecruitAI's opening page.
var OPENING = "OPN-00001";
// The mailbox RecruitAI reads.
var MAILBOX = "recruitment@example.org";

var FIELDS = ["Full name", "Email", "Phone", "State", "Category", "Differently abled", "Study leave"];

function onFormSubmit(e) {
  var answers = {};
  var resume = [];
  e.response.getItemResponses().forEach(function (item) {
    var question = item.getItem();
    if (question.getType() == FormApp.ItemType.FILE_UPLOAD) {
      [].concat(item.getResponse()).slice(0, 1).forEach(function (fileId) {
        resume.push(DriveApp.getFileById(fileId).getBlob());
      });
    } else {
      answers[question.getTitle()] = [].concat(item.getResponse()).join(", ");
    }
  });
  var lines = ["Opening: " + OPENING];
  FIELDS.forEach(function (field) {
    lines.push(field + ": " + (answers[field] || ""));
  });
  GmailApp.sendEmail(MAILBOX, "RecruitAI form response " + OPENING, lines.join("\n"), { attachments: resume });
}
```

Save the script.

## 4. Run it on every response

In the script editor, open **Triggers** (the clock icon), then **Add Trigger**:

- Function: `onFormSubmit`
- Event source: **From form**
- Event type: **On form submit**

Save. Google asks you to authorise the script to read the form's uploads and
send email as this account. Allow it.

## 5. Try it

Submit the form once yourself with a made-up resume. An email with the subject
`RecruitAI form response OPN-…` appears in the mailbox. In RecruitAI, open
**Inbox** and press **Check inbox**: the response is filed under the opening
and queued for reading, marked as received by Google Form.

If an answer is not accepted (a State spelled differently, a missing resume),
the message appears under **Waiting for you** on the Inbox page with the reason.

## What is not done this way

- RecruitAI does not read the form's linked spreadsheet. The email is the
  record it takes; the spreadsheet stays as Google's own copy.
- A response is taken once. Editing a response in Google after submitting does
  not change the application.
