# Resume-JD Matcher MVP

A recruiter-facing React + FastAPI + PostgreSQL application that extracts PDF/DOCX text, evaluates mandatory requirements before technical skills, and stores analysis history.

## Run it
1. Install Docker Desktop and set an LLM key in your shell: `export OPENAI_API_KEY="..."`.
2. Run: `docker compose up --build`.
3. Open `http://localhost:5173`.

The app uses an OpenAI-compatible LLM API. Change `OPENAI_MODEL` in `docker-compose.yml` if needed. Without an API key it still parses documents and returns a clearly labelled limited heuristic analysis; it does not infer missing experience.

## Important behavior
- The model is instructed to use only supplied resume/JD text and to quote evidence.
- Unknown evidence remains `NOT_CLEARLY_MENTIONED`; matching never treats it as a pass.
- Mandatory score is separate. Any `DOES_NOT_MEET` or `NOT_CLEARLY_MENTIONED` mandatory item produces a potential-disqualification flag.
- The database stores source text and structured JSON results. Do not deploy this baseline with real applicant data until you add authentication, retention/deletion controls, encryption, access logging, consent/legal review, and an approved LLM data-processing agreement.

## API
- `POST /api/analyses` multipart: `resume` (PDF/DOCX), `jd_text` or `jd_file`, optional `mandatory_weight`.
- `GET /api/analyses` history.
- `GET /api/analyses/{id}` one report.
