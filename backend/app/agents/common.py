from __future__ import annotations

import re
from typing import Any

from ..resume_service import normalize_text, resume_text

# A deliberately conservative vocabulary used for deterministic skill detection.
# Only exact, word-boundary matches are treated as evidence.
SKILL_VOCAB: dict[str, list[str]] = {
    "Python": ["python"],
    "TypeScript": ["typescript"],
    "JavaScript": ["javascript", "js"],
    "Java": ["java"],
    "Go": ["golang", "go language"],
    "C++": ["c++", "cpp"],
    "C#": ["c#", "csharp"],
    "Rust": ["rust"],
    "SQL": ["sql"],
    "NoSQL": ["nosql"],
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "Node.js": ["node.js", "nodejs"],
    "FastAPI": ["fastapi"],
    "Django": ["django"],
    "Flask": ["flask"],
    "Spring": ["spring boot"],
    "LangChain": ["langchain"],
    "LangGraph": ["langgraph"],
    "RAG": ["rag", "retrieval-augmented generation", "retrieval augmented generation"],
    "LLM": ["llm", "large language model", "large language models"],
    "OpenAI": ["openai", "gpt-4", "gpt-3", "chatgpt"],
    "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow"],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Spark": ["spark", "pyspark"],
    "Airflow": ["airflow"],
    "dbt": ["dbt"],
    "Kafka": ["kafka"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"],
    "AWS": ["aws", "amazon web services"],
    "GCP": ["gcp", "google cloud"],
    "Azure": ["azure"],
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "PGVector": ["pgvector"],
    "Pinecone": ["pinecone"],
    "Elasticsearch": ["elasticsearch"],
    "GraphQL": ["graphql"],
    "REST": ["rest api", "restful", "restful api"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "microservice"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous delivery"],
    "Git": ["git"],
    "GitHub Actions": ["github actions"],
    "Linux": ["linux"],
    "HTML": ["html"],
    "CSS": ["css"],
    "Tailwind": ["tailwind", "tailwindcss"],
    "Figma": ["figma"],
    "Firebase": ["firebase"],
    "Firestore": ["firestore"],
    "Supabase": ["supabase"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Excel": ["microsoft excel", "excel spreadsheets"],
    "Machine Learning": ["machine learning", "ml models"],
    "Deep Learning": ["deep learning"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision"],
    "MLOps": ["mlops"],
    "ETL": ["etl"],
    "Data Warehousing": ["data warehouse", "data warehousing"],
    "A/B Testing": ["a/b testing", "ab testing"],
    "Unit Testing": ["unit testing", "unit tests"],
    "Agile": ["agile", "scrum"],
    "System Design": ["system design", "distributed systems"],
    "Prompt Engineering": ["prompt engineering"],
    "Vector Search": ["vector search", "vector database", "vector databases"],
    "Hugging Face": ["hugging face", "huggingface"],
    "OpenTelemetry": ["opentelemetry"],
    "Datadog": ["datadog"],
    "Sentry": ["sentry"],
    "Playwright": ["playwright"],
    "Cypress": ["cypress"],
    "Jest": ["jest"],
    "Pytest": ["pytest"],
    "Swift": ["swift"],
    "Kotlin": ["kotlin"],
    "Ruby": ["ruby"],
    "PHP": ["php"],
    "Scala": ["scala"],
    "R": ["r programming", "r language"],
    "MATLAB": ["matlab"],
    "Salesforce": ["salesforce"],
    "SAP": ["sap"],
    "Jira": ["jira"],
    "Confluence": ["confluence"],
    "Looker": ["looker"],
    "Snowflake": ["snowflake"],
    "Databricks": ["databricks"],
}


def extract_skills(text: str) -> list[str]:
    haystack = f" {normalize_text(text)} "
    found: list[str] = []
    for canonical, aliases in SKILL_VOCAB.items():
        for alias in aliases:
            pattern = re.escape(alias.lower())
            if alias.lower() in {"r", "go", "c#", "c++"}:
                matched = re.search(rf"(?<![\w+#]){pattern}(?![\w+#])", haystack)
            else:
                matched = re.search(rf"(?<![\w]){pattern}(?![\w])", haystack)
            if matched:
                found.append(canonical)
                break
    return found


def skill_status(skill: str, resume: str) -> str:
    canonical_aliases = SKILL_VOCAB.get(skill, [skill])
    haystack = f" {normalize_text(resume)} "
    for alias in canonical_aliases:
        if re.search(rf"(?<![\w]){re.escape(alias.lower())}(?![\w])", haystack):
            return "demonstrated"
    return "not_demonstrated"


def quote_in_resume(quote: str, content: dict[str, Any]) -> bool:
    if not quote or len(quote.strip()) < 4:
        return False
    haystack = normalize_text(resume_text(content))
    needle = normalize_text(quote)
    if needle in haystack:
        return True
    # Allow a short excerpt to match even when punctuation differs.
    words = [w for w in re.findall(r"[a-z0-9+#.]+", needle) if len(w) > 2]
    if len(words) < 4:
        return False
    hits = sum(1 for word in words if word in haystack)
    return hits / len(words) >= 0.9


def section_exists(content: dict[str, Any], section: str) -> bool:
    if section not in content:
        return False
    value = content[section]
    return bool(value) if isinstance(value, (list, str, dict)) else True


def summarize_resume_for_prompt(content: dict[str, Any], *, max_chars: int = 12000) -> str:
    import json

    text = json.dumps(content, ensure_ascii=False, indent=1)
    return text[:max_chars]


def clamp_text(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]
