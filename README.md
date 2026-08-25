# RecruitAI — Resume → Excel Dashboard (MVP slice)

A local, single-user tool: upload faculty resumes, get back an Excel workbook
with extracted candidate fields, one row per candidate — ready to sort and
filter by hand. This is the first vertical slice of the Reader Agent from the
full RecruitAI design, built standalone.

**What this does not do:** compute eligibility, do arithmetic, rank or score
candidates, or infer school/department/designation from resume text. See
`RecruitAI_LLM_Layer_Implementation_Plan.md` for the full constraints this
slice was built against.

## Setup

1. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Install [Ollama](https://ollama.com) and pull the local model:

   ```bash
   ollama pull qwen3.5:4b
   ```

3. Make sure the Ollama server is running (it starts automatically on most
   installs; otherwise run `ollama serve`).

## Running the dashboard

```bash
streamlit run app/dashboard.py
```

Upload one or more `.pdf` / `.docx` resumes, click **Extract fields**, review
the preview table, then **Download Excel workbook**.

### Running without a model (offline demo)

Set the provider to the canned fake provider — no Ollama call is made:

```bash
LLM_PROVIDER=fake streamlit run app/dashboard.py
```

(On Windows PowerShell: `$env:LLM_PROVIDER = "fake"; streamlit run app/dashboard.py`)

### Running with Gemini instead of a local model

```bash
LLM_PROVIDER=gemini GEMINI_API_KEY=your-key-here streamlit run app/dashboard.py
```

**Read this before using it on real candidate data**: Gemini's free tier
permits Google to use submitted prompts for model training. The dashboard
shows a warning banner and every extraction call logs a warning when this
provider is active, but nothing is blocked — that decision is yours to make,
not the app's. Get a free API key at https://aistudio.google.com/apikey.

**Free-tier quotas are per-model and per-day**, and each resume is one
request — so a 12-resume batch costs 12. Measured on a real free-tier key:
`gemini-3.5-flash` and `gemini-3.6-flash` get **20 requests/day each**, and
the `-lite` variants get 500/day.

Because those allowances are *separate*, `GEMINI_MODEL=auto` (the default)
alternates between the two flash models by date and automatically fails over
to the other one the moment a daily quota runs out — roughly 40 requests/day
with no manual switching. When every model in the pool is exhausted the app
stops with a clear message rather than retrying pointlessly.

The `-lite` models are deliberately **not** in the rotation pool: they have
far more quota but were measurably wrong on NET/SET status and PhD status on
real resumes, which are the two highest-risk fields here. Use them via
`GEMINI_MODEL=gemini-3.5-flash-lite` for UI/plumbing testing where accuracy
doesn't matter, not for runs that count.

## Configuration

Read once at startup from environment variables, all optional:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `fake`, or `gemini` |
| `LLM_MODEL` | `qwen3.5:4b` | Ollama model tag |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `LLM_TIMEOUT_SECONDS` | `240` | Per-call timeout (CPU inference is slow) |
| `GEMINI_API_KEY` | *(empty)* | Required when `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | `auto` | `auto` rotates the pool daily; or pin a model name |
| `GEMINI_MODEL_POOL` | `gemini-3.5-flash,gemini-3.6-flash` | Models `auto` rotates through and fails over between |
| `GEMINI_TIMEOUT_SECONDS` | `60` | Per-call timeout for Gemini |
| `CONFIDENCE_THRESHOLD` | `0.7` | Below this, a field routes its row to `needs_review` |

## Tests

```bash
pytest tests/test_confidence_routing.py tests/test_excel_output.py -q
```

These run with zero model calls. `tests/test_extraction_accuracy.py` runs the
synthetic fixture set (`tests/fixtures/synthetic_resumes/`) through the real
Ollama model and prints a per-field accuracy report — it skips automatically
if no Ollama server is reachable:

```bash
python -m pytest tests/test_extraction_accuracy.py -s -q
```

## Notes

- No PII (raw resume text or extracted values) is ever logged to console or
  disk — only field-level confidence and routing decisions. Synthetic fixture
  resumes are entirely fabricated names/data.
- PDF text is read with PyMuPDF, falling back to pdfplumber. Measured over
  23 real resumes, PyMuPDF was never worse and was far better on layouts
  that drop inter-word spaces (three files went from ~180-250 glued tokens
  per 1000 down to under 6). Scanned/image PDFs have no text layer at all
  and fail cleanly with a `parse_error` — there is no OCR in this slice.
- Each run's results live only in the downloaded `.xlsx` — there's no
  database in this slice.
- Column names mirror the eventual `extracted_data` schema so a later move to
  Postgres is a rename, not a redesign.
