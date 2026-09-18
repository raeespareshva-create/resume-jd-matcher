import io, json, math, os, re
from collections import Counter
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import DateTime, Float, String, Text, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from docx import Document
from pypdf import PdfReader

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://resume_matcher:resume_matcher@localhost:5432/resume_matcher")
engine = create_engine(DATABASE_URL); Session = sessionmaker(bind=engine)
class Base(DeclarativeBase): pass
class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    candidate_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resume_filename: Mapped[str] = mapped_column(String(255))
    resume_text: Mapped[str] = mapped_column(Text)
    jd_text: Mapped[str] = mapped_column(Text)
    mandatory_weight: Mapped[float] = mapped_column(Float, default=.7)
    result: Mapped[dict] = mapped_column(JSONB)
Base.metadata.create_all(engine)

app = FastAPI(title="Resume JD Matcher")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["*"])

def extract(upload: UploadFile, data: bytes) -> str:
    name = (upload.filename or "").lower()
    try:
        if name.endswith(".pdf"): return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        if name.endswith(".docx"):
            doc = Document(io.BytesIO(data))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            # Many resume templates put skill "tags"/pills or two-column layouts inside
            # tables — plain .paragraphs misses that text entirely, so read tables too.
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            parts.append(cell.text)
            # Text inside text boxes / shapes (some templates use these for skill tags)
            # lives in the raw XML rather than doc.paragraphs or doc.tables.
            try:
                from docx.oxml.ns import qn
                for shape_text in doc.element.body.iter(qn('w:t')):
                    if shape_text.text and shape_text.text.strip():
                        parts.append(shape_text.text)
            except Exception:
                pass
            seen = set(); unique_parts = []
            for p in parts:
                if p not in seen:
                    seen.add(p); unique_parts.append(p)
            return "\n".join(unique_parts)
    except Exception as e: raise HTTPException(422, f"Unable to read document: {e}")
    raise HTTPException(415, "Only PDF and DOCX files are supported")

SCHEMA = """Return ONLY valid JSON matching this schema, and nothing else (no markdown fences, no commentary):
{
 "candidate":{"name":string|null,"total_years_experience":number|null,"relevant_years_experience":number|null,"roles":[string]},
 "mandatory_criteria":[{"requirement":string,"jd_requirement":string,"resume_evidence":string,"status":"MEETS|DOES_NOT_MEET|NOT_CLEARLY_MENTIONED|NEEDS_VERIFICATION","years_required":number|null,"years_found":number|null,"disqualifier":boolean}],
 "technical_skills":[{"tool":string,"required":"MANDATORY|PREFERRED|IMPORTANT","found_in_resume":boolean,"experience":string,"evidence":string,"status":"MATCHED|MISSING|NOT_CLEARLY_MENTIONED|NEEDS_VERIFICATION"}],
 "summary":{"satisfied":[string],"not_satisfied":[string],"missing_tools":[string],"verification_needed":[string],"narrative":string}
}"""

def heuristic(resume: str, jd: str, note: str = "No usable API key: only literal keyword matching was performed.") -> dict:
    skills = re.findall(r"(?:Python|SQL|PySpark|Snowflake|AWS|Apache Kafka|Kafka|Airflow|Azure|GCP|Java|Scala|Docker|Kubernetes)", jd, re.I)
    unique = list(dict.fromkeys(x.title() if x.lower() != "sql" else "SQL" for x in skills))
    tech=[]
    for s in unique:
        found = bool(re.search(r"\b" + re.escape(s) + r"\b", resume, re.I))
        tech.append({"tool":s,"required":"IMPORTANT","found_in_resume":found,"experience":"Not Clearly Mentioned","evidence":s + " explicitly appears in resume." if found else "Not mentioned in resume.","status":"MATCHED" if found else "MISSING"})
    return {"candidate":{"name":None,"total_years_experience":None,"relevant_years_experience":None,"roles":[]},"mandatory_criteria":[],"technical_skills":tech,"summary":{"satisfied":[],"not_satisfied":[],"missing_tools":[x["tool"] for x in tech if not x["found_in_resume"]],"verification_needed":[note],"narrative":"Limited fallback analysis: only explicit keyword matching was performed (no AI, no vector search)."}}

# ---------------- Pure-Python TF-IDF vector retrieval (no external embeddings API) ----------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9+#.]{2,}", text.lower())

def _chunk(text: str, max_len: int = 220) -> list[str]:
    raw = re.split(r"[\n\r]+", text)
    chunks: list[str] = []
    for line in raw:
        line = line.strip(" \t•-*|;,")
        if not line:
            continue
        if len(line) > max_len:
            for i in range(0, len(line), max_len):
                chunks.append(line[i:i+max_len])
        else:
            chunks.append(line)
    seen: set[str] = set(); out: list[str] = []
    for c in chunks:
        low = c.lower()
        if low not in seen:
            seen.add(low); out.append(c)
    return out[:200]

def _tfidf_vectors(token_lists: list[list[str]]) -> list[dict[str, float]]:
    df: Counter = Counter()
    for tokens in token_lists:
        for t in set(tokens):
            df[t] += 1
    n = len(token_lists)
    vectors: list[dict[str, float]] = []
    for tokens in token_lists:
        if not tokens:
            vectors.append({}); continue
        tf = Counter(tokens)
        vec = {}
        for t, c in tf.items():
            idf = math.log((n + 1) / (df[t] + 1)) + 1
            vec[t] = (c / len(tokens)) * idf
        vectors.append(vec)
    return vectors

def _sparse_cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b: return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v*v for v in a.values())); nb = math.sqrt(sum(v*v for v in b.values()))
    return dot / (na*nb) if na and nb else 0.0

def retrieve_evidence(resume: str, jd: str, top_k: int = 4) -> str:
    """For each JD line, retrieve the most similar resume excerpts using TF-IDF cosine
    similarity — real vector search, computed locally, no external embeddings API needed.
    This is what lets 'Kafka' in a resume skill tag match 'Apache Kafka' in a JD line."""
    resume_chunks = _chunk(resume)
    jd_lines = [l for l in _chunk(jd, max_len=300) if len(l) > 3]
    if not resume_chunks or not jd_lines:
        return ""
    all_tokens = [_tokenize(c) for c in resume_chunks] + [_tokenize(l) for l in jd_lines]
    vectors = _tfidf_vectors(all_tokens)
    resume_vecs = vectors[:len(resume_chunks)]
    jd_vecs = vectors[len(resume_chunks):]
    blocks = []
    for line, lvec in zip(jd_lines, jd_vecs):
        scored = sorted(zip(resume_chunks, resume_vecs), key=lambda cv: -_sparse_cosine(lvec, cv[1]))
        top = [c for c, _ in scored[:top_k]]
        blocks.append(f'- JD line: "{line}"\n  Closest resume excerpts (TF-IDF vector similarity search): ' + " | ".join(top))
    return "\n".join(blocks)

def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)

def analyze(resume: str, jd: str) -> dict:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return heuristic(resume, jd, "No GROQ_API_KEY is set: only literal keyword matching was performed.")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        evidence = retrieve_evidence(resume, jd)

        prompt = f"""You are an evidence-first recruitment analysis engine. Analyze ONLY the two documents below. Never infer, estimate, or fabricate skills, dates, tenure, credentials, domains, projects, or seniority. First identify explicit mandatory requirements: words such as must, required, minimum, mandatory, essential, and hard constraints. Then assess technical skills. If a resume phrases a skill differently than the JD (e.g. a skill tag "Kafka" vs a JD requirement "Apache Kafka", or "Postgres" vs "PostgreSQL"), treat it as a match when the retrieved evidence below supports it — that evidence was found via vector similarity search specifically to catch such rephrasings. Do NOT match unrelated tools just because they sound similar. If years are not explicitly attributable to a skill, use null / NOT_CLEARLY_MENTIONED. Quote concise exact evidence with role/project/section where possible. A missing mandatory item or unknown proof is a potential disqualification.

JOB DESCRIPTION:
{jd}

RESUME:
{resume}

SEMANTICALLY RETRIEVED EVIDENCE (JD line -> most relevant resume excerpts, found via vector similarity search):
{evidence}

{SCHEMA}"""
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_json(response.choices[0].message.content)

    except Exception as e:
        msg = str(e)
        if "401" in msg or "invalid_api_key" in msg or "Authentication" in msg:
            note = "The GROQ_API_KEY set is invalid or expired: only literal keyword matching was performed."
        elif "429" in msg or "rate_limit" in msg:
            note = "Groq free-tier rate limit hit: only literal keyword matching was performed. Try again shortly."
        else:
            note = f"AI analysis failed ({type(e).__name__}): only literal keyword matching was performed."
        return heuristic(resume, jd, note)

def scored(result: dict, weight: float) -> dict:
    mandatory=result.get("mandatory_criteria",[]); technical=result.get("technical_skills",[])
    meets=sum(x.get("status")=="MEETS" for x in mandatory)
    matched=sum(x.get("status")=="MATCHED" for x in technical)
    mscore=round(100*meets/len(mandatory),1) if mandatory else None
    tscore=round(100*matched/len(technical),1) if technical else None
    overall=round((mscore if mscore is not None else 0)*weight+(tscore if tscore is not None else 0)*(1-weight),1)
    failed=[x["requirement"] for x in mandatory if x.get("status") in ("DOES_NOT_MEET","NOT_CLEARLY_MENTIONED")]
    result["scores"]={"mandatory_criteria_match":mscore,"technical_skills_match":tscore,"overall_match":overall,"mandatory_weight":weight,"potential_disqualification":bool(failed),"failed_or_unproven_mandatory":failed}
    return result

def serialize(a: Analysis): return {"id":str(a.id),"created_at":a.created_at.isoformat(),"candidate_name":a.candidate_name,"resume_filename":a.resume_filename,"result":a.result}
@app.post("/api/analyses")
async def create_analysis(resume: UploadFile=File(...), jd_text: str=Form(""), jd_file: UploadFile|None=File(None), mandatory_weight: float=Form(.7)):
    if not 0 <= mandatory_weight <= 1: raise HTTPException(422,"mandatory_weight must be between 0 and 1")
    resume_text=extract(resume,await resume.read())
    if jd_file: jd_text=extract(jd_file,await jd_file.read())
    if not resume_text.strip() or not jd_text.strip(): raise HTTPException(422,"Resume and job description must contain readable text")
    result=scored(analyze(resume_text,jd_text),mandatory_weight)
    with Session() as s:
        row=Analysis(resume_filename=resume.filename or "resume",resume_text=resume_text,jd_text=jd_text,mandatory_weight=mandatory_weight,candidate_name=result.get("candidate",{}).get("name"),result=result);s.add(row);s.commit();s.refresh(row);return serialize(row)
@app.get("/api/analyses")
def history():
    with Session() as s: return [serialize(x) for x in s.scalars(select(Analysis).order_by(Analysis.created_at.desc()).limit(30))]
@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: UUID):
    with Session() as s:
        row=s.get(Analysis,analysis_id)
        if not row: raise HTTPException(404,"Analysis not found")
        return serialize(row)

# ---- Follow-up Q&A, grounded in the stored resume + JD text for this analysis ----
class ChatTurn(BaseModel):
    role: Literal["user","assistant"]
    content: str

class AskRequest(BaseModel):
    question: str
    history: list[ChatTurn] = []

@app.post("/api/analyses/{analysis_id}/ask")
def ask_about_analysis(analysis_id: UUID, body: AskRequest):
    with Session() as s:
        row = s.get(Analysis, analysis_id)
        if not row: raise HTTPException(404, "Analysis not found")

    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return {"answer": "Set a valid GROQ_API_KEY environment variable to enable follow-up Q&A. Right now only the initial keyword-based analysis is available."}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        system = f"""You are answering follow-up questions about ONE specific candidate screening. Use ONLY the job description and resume text below plus the prior analysis JSON as your source of truth. Never invent facts not present in these documents. If the answer is not clearly supported by the text, say so explicitly rather than guessing. Keep answers concise and cite the relevant resume/JD phrase briefly when useful.

JOB DESCRIPTION:
{row.jd_text}

RESUME:
{row.resume_text}

PRIOR STRUCTURED ANALYSIS (JSON):
{json.dumps(row.result)}
"""
        messages = [{"role":"system","content":system}]
        for turn in body.history[-12:]:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role":"user","content": body.question})

        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL","llama-3.3-70b-versatile"),
            temperature=0.2,
            messages=messages,
        )
        return {"answer": response.choices[0].message.content}
    except Exception as e:
        return {"answer": f"Couldn't get an answer right now (the AI service returned an error: {type(e).__name__}). Check that your GROQ_API_KEY is valid."}