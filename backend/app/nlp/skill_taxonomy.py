"""Canonical skill taxonomy with alias mapping.

This module is the backbone of skill normalisation (PRD section 9). It maps the
many surface forms recruiters and candidates actually write onto a single
canonical identifier, e.g.::

    "ML", "Machine Learning Engineering", "Applied ML" -> machine_learning
    "Postgres", "Postgres DB", "PSQL"                -> postgresql

Design
------
* ``SKILL_TAXONOMY`` is the single source of truth: canonical name, category,
  description and aliases.
* ``normalize_skill`` is a pure function (no I/O, no DB) so it is trivially
  unit-testable and reusable from the API, notebooks and evaluation scripts.
* Unknown terms are *not* dropped. They are normalised with a deterministic
  slug so they can still be tracked and matched literally.
* Matching is boundary aware, so "Go" does not match "Golang"/"Mongo" and
  "R" does not match "React". Abbreviations require a word boundary.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Literal

SkillCategoryLiteral = Literal[
    "programming_language",
    "framework",
    "database",
    "cloud",
    "ai_ml",
    "devops",
    "data_science",
    "frontend",
    "mobile",
    "testing",
    "soft_skill",
    "certification",
    "other",
]

#: Aliases whose surface form is ambiguous and must be matched with word
#: boundaries to avoid false positives inside longer words.
_ABBREVIATIONS: set[str] = {
    "go",
    "r",
    "c",
    "c++",
    "ai",
    "ml",
    "dl",
    "nlp",
    "db",
    "qa",
    "ux",
    "ui",
    "js",
    "ts",
    "aws",
    "gcp",
    "k8s",
    "ci",
    "cd",
    "os",
    "pm",
    "hr",
    "bi",
    "etl",
    "api",
    "gpu",
    "nn",
    "llm",
    "sql",
    "nosql",
    "vc",
    "pr",
}


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    normalized_name: str
    name: str
    category: SkillCategoryLiteral
    description: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    related: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_surface_forms(self) -> tuple[str, ...]:
        return (self.normalized_name.replace("_", " "), self.name, *self.aliases)


def _s(
    normalized_name: str,
    name: str,
    category: SkillCategoryLiteral,
    description: str = "",
    aliases: Iterable[str] = (),
    related: Iterable[str] = (),
) -> SkillDefinition:
    return SkillDefinition(
        normalized_name=normalized_name,
        name=name,
        category=category,
        description=description,
        aliases=tuple(aliases),
        related=tuple(related),
    )


SKILL_TAXONOMY: tuple[SkillDefinition, ...] = (
    # ---------------- Programming languages ----------------
    _s("python", "Python", "programming_language", aliases=("python3", "py3", "cpython")),
    _s("java", "Java", "programming_language"),
    _s("javascript", "JavaScript", "programming_language", aliases=("js", "ecmascript")),
    _s("typescript", "TypeScript", "programming_language", aliases=("ts",)),
    _s("c_sharp", "C#", "programming_language", aliases=("csharp", "c sharp", "dotnet c#")),
    _s("c_plus_plus", "C++", "programming_language", aliases=("cpp", "cplusplus")),
    _s("c_language", "C", "programming_language"),
    _s("go", "Go", "programming_language", aliases=("golang",)),
    _s("rust", "Rust", "programming_language"),
    _s("ruby", "Ruby", "programming_language"),
    _s("php", "PHP", "programming_language"),
    _s("swift", "Swift", "programming_language"),
    _s("kotlin", "Kotlin", "programming_language"),
    _s("scala", "Scala", "programming_language"),
    _s("r_language", "R", "programming_language", aliases=("r programming", "rstudio")),
    _s("matlab", "MATLAB", "programming_language"),
    _s("perl", "Perl", "programming_language"),
    _s("bash", "Bash", "programming_language", aliases=("shell scripting", "shell", "zsh")),
    _s("sql", "SQL", "programming_language", aliases=("ansi sql",)),
    _s("solidity", "Solidity", "programming_language"),
    _s("dart", "Dart", "programming_language"),

    # ---------------- AI / ML ----------------
    _s(
        "machine_learning",
        "Machine Learning",
        "ai_ml",
        "Algorithms that learn from data to make predictions or decisions.",
        aliases=(
            "ml",
            "machine learning engineering",
            "machine learning development",
            "applied machine learning",
            "applied ml",
            "predictive modeling",
            "predictive modelling",
            "ml engineer",
            "ml engineering",
        ),
        related=("deep_learning", "artificial_intelligence", "data_science"),
    ),
    _s(
        "deep_learning",
        "Deep Learning",
        "ai_ml",
        aliases=("dl", "dl engineer", "neural network engineering", "neural networks"),
        related=("machine_learning", "pytorch", "tensorflow"),
    ),
    _s("artificial_intelligence", "Artificial Intelligence", "ai_ml", aliases=("ai",)),
    _s("natural_language_processing", "NLP", "ai_ml", aliases=("nlp", "natural language processing", "text mining")),
    _s("computer_vision", "Computer Vision", "ai_ml", aliases=("cv", "image recognition", "object detection")),
    _s("large_language_models", "Large Language Models", "ai_ml", aliases=("llm", "llms", "genai", "generative ai")),
    _s("pytorch", "PyTorch", "ai_ml", aliases=("torch", "pytorch lightning")),
    _s("tensorflow", "TensorFlow", "ai_ml", aliases=("tf", "keras")),
    _s("scikit_learn", "scikit-learn", "ai_ml", aliases=("sklearn", "sci-kit learn", "sk learn")),
    _s("hugging_face", "Hugging Face", "ai_ml", aliases=("huggingface", "hf", "transformers", "hugging face transformers")),
    _s("langchain", "LangChain", "ai_ml"),
    _s("spacy", "spaCy", "ai_ml", aliases=("spacy nlp",)),
    _s("nltk", "NLTK", "ai_ml"),
    _s("xgboost", "XGBoost", "ai_ml", aliases=("xg boost",)),
    _s("lightgbm", "LightGBM", "ai_ml"),
    _s("catboost", "CatBoost", "ai_ml"),
    _s("opencv", "OpenCV", "ai_ml", aliases=("cv2",)),
    _s("pandas", "pandas", "data_science", aliases=("pd",)),
    _s("numpy", "NumPy", "data_science", aliases=("np",)),
    _s("scipy", "SciPy", "data_science"),
    _s("matplotlib", "Matplotlib", "data_science", aliases=("pyplot",)),
    _s("seaborn", "Seaborn", "data_science", aliases=("sns",)),
    _s("jupyter", "Jupyter", "data_science", aliases=("jupyter notebook", "notebook", "colab", "google colab")),
    _s("data_science", "Data Science", "data_science", aliases=("data scientist",)),
    _s("data_analysis", "Data Analysis", "data_science"),
    _s("data_engineering", "Data Engineering", "data_science", aliases=("data engineer",)),
    _s("mlops", "MLOps", "ai_ml", aliases=("ml ops", "machine learning operations")),
    _s("mlflow", "MLflow", "mlops"),
    _s("feature_engineering", "Feature Engineering", "data_science"),
    _s("model_deployment", "Model Deployment", "ai_ml", aliases=("inference serving", "model serving")),
    _s("recommender_systems", "Recommender Systems", "ai_ml", aliases=("recommendation systems", "recommendation engine")),
    _s("time_series", "Time Series", "data_science", aliases=("time series forecasting", "time series analysis")),
    _s("statistics", "Statistics", "data_science", aliases=("statistical analysis", "applied statistics")),
    _s("bayesian_methods", "Bayesian Methods", "ai_ml", aliases=("bayes", "bayesian inference", "mcmc")),
    _s("reinforcement_learning", "Reinforcement Learning", "ai_ml", aliases=("rl",)),
    _s("generative_ai", "Generative AI", "ai_ml", aliases=("gen ai",)),
    _s("rag", "Retrieval-Augmented Generation", "ai_ml", aliases=("rag pipelines", "retrieval augmented generation")),
    _s("vector_database", "Vector Database", "ai_ml", aliases=("vector db", "vector store", "embedding store")),

    # ---------------- Frameworks / backend ----------------
    _s("fastapi", "FastAPI", "framework", aliases=("fast api",)),
    _s("flask", "Flask", "framework"),
    _s("django", "Django", "framework"),
    _s("express", "Express.js", "framework", aliases=("expressjs", "express js", "express")),
    _s("nestjs", "NestJS", "framework", aliases=("nest js",)),
    _s("spring", "Spring", "framework", aliases=("spring boot", "springboot")),
    _s("nodejs", "Node.js", "framework", aliases=("node", "node js", "nodejs")),
    _s("react", "React", "frontend", aliases=("react.js", "reactjs", "react js")),
    _s("nextjs", "Next.js", "frontend", aliases=("next js", "nextjs")),
    _s("vue", "Vue", "frontend", aliases=("vuejs", "vue.js", "vue js")),
    _s("angular", "Angular", "frontend", aliases=("angularjs", "angular js")),
    _s("svelte", "Svelte", "frontend", aliases=("sveltekit",)),
    _s("tailwind_css", "Tailwind CSS", "frontend", aliases=("tailwind", "tailwindcss")),
    _s("redux", "Redux", "frontend", aliases=("redux toolkit", "rtk")),
    _s("html", "HTML", "frontend", aliases=("html5",)),
    _s("css", "CSS", "frontend", aliases=("css3", "scss", "sass")),
    _s("graphql", "GraphQL", "framework"),
    _s("rest_api", "REST API", "framework", aliases=("rest", "restful", "rest apis", "restful api")),
    _s("grpc", "gRPC", "framework"),
    _s("websockets", "WebSockets", "framework"),
    _s("streamlit", "Streamlit", "framework"),
    _s("gradio", "Gradio", "framework"),
    _s("celery", "Celery", "framework"),
    _s("stream_processing", "Stream Processing", "data_science", aliases=("kafka streaming", "real time streaming")),

    # ---------------- Databases ----------------
    _s("postgresql", "PostgreSQL", "database", aliases=("postgres", "postgre sql", "psql", "postgres db", "postgresql database")),
    _s("mysql", "MySQL", "database", aliases=("my sql", "mariadb")),
    _s("sqlite", "SQLite", "database"),
    _s("mongodb", "MongoDB", "database", aliases=("mongo", "mongo db")),
    _s("redis", "Redis", "database", aliases=("redis cache",)),
    _s("elasticsearch", "Elasticsearch", "database", aliases=("elastic search", "elastic", "opensearch")),
    _s("cassandra", "Cassandra", "database", aliases=("apache cassandra",)),
    _s("dynamodb", "DynamoDB", "database", aliases=("dynamo db", "aws dynamodb")),
    _s("oracle_database", "Oracle", "database", aliases=("oracle db", "oracle database", "pl/sql", "plsql")),
    _s("mssql", "Microsoft SQL Server", "database", aliases=("sql server", "ms sql", "microsoft sql server")),
    _s("snowflake", "Snowflake", "database"),
    _s("bigquery", "BigQuery", "database", aliases=("big query",)),
    _s("redshift", "Amazon Redshift", "database", aliases=("redshift",)),
    _s("clickhouse", "ClickHouse", "database"),
    _s("neo4j", "Neo4j", "database", aliases=("neo 4j",)),
    _s("data_warehouse", "Data Warehouse", "database", aliases=("data warehousing", "dwh")),
    _s("data_lake", "Data Lake", "database", aliases=("data lakehouse", "lakehouse")),

    # ---------------- Cloud / DevOps ----------------
    _s("aws", "AWS", "cloud", "Amazon Web Services", aliases=("amazon web services", "aws cloud")),
    _s("azure", "Azure", "cloud", aliases=("microsoft azure", "azure cloud")),
    _s("gcp", "Google Cloud", "cloud", aliases=("google cloud platform", "google cloud")),
    _s("docker", "Docker", "devops", aliases=("docker compose", "containerization", "containers")),
    _s("kubernetes", "Kubernetes", "devops", aliases=("k8s", "kube")),
    _s("terraform", "Terraform", "devops", aliases=("hashicorp terraform", "iac")),
    _s("ansible", "Ansible", "devops"),
    _s("jenkins", "Jenkins", "devops"),
    _s("github_actions", "GitHub Actions", "devops", aliases=("gh actions", "github action")),
    _s("gitlab_ci", "GitLab CI", "devops", aliases=("gitlab", "gitlab ci/cd")),
    _s("circleci", "CircleCI", "devops", aliases=("circle ci",)),
    _s("linux", "Linux", "devops", aliases=("unix", "ubuntu", "centos", "debian")),
    _s("nginx", "Nginx", "devops"),
    _s("apache_spark", "Apache Spark", "data_science", aliases=("spark", "pyspark", "apache spark")),
    _s("hadoop", "Hadoop", "data_science", aliases=("hdfs", "mapreduce")),
    _s("airflow", "Apache Airflow", "data_science", aliases=("airflow",)),
    _s("dbt", "dbt", "data_science", aliases=("data build tool",)),
    _s("kafka", "Apache Kafka", "data_science", aliases=("kafka",)),
    _s("rabbitmq", "RabbitMQ", "devops"),
    _s("prometheus", "Prometheus", "devops"),
    _s("grafana", "Grafana", "devops"),
    _s("datadog", "Datadog", "devops"),
    _s("git", "Git", "devops", aliases=("github", "gitlab", "bitbucket", "version control")),
    _s("ci_cd", "CI/CD", "devops", aliases=("ci cd", "continuous integration", "continuous delivery", "continuous deployment")),
    _s("serverless", "Serverless", "devops", aliases=("lambda", "aws lambda", "cloud functions")),
    _s("microservices", "Microservices", "framework", aliases=("microservice architecture",)),
    _s("linux_kernel", "Linux Kernel", "devops"),

    # ---------------- Mobile ----------------
    _s("android", "Android", "mobile", aliases=("android studio",)),
    _s("ios", "iOS", "mobile", aliases=("swiftui", "xcode")),
    _s("flutter", "Flutter", "mobile"),
    _s("react_native", "React Native", "mobile", aliases=("react-native",)),
    _s("jetpack", "Jetpack", "mobile", aliases=("jetpack compose",)),

    # ---------------- Testing ----------------
    _s("pytest", "pytest", "testing"),
    _s("unit_testing", "Unit Testing", "testing", aliases=("unit tests",)),
    _s("integration_testing", "Integration Testing", "testing", aliases=("integration tests",)),
    _s("selenium", "Selenium", "testing"),
    _s("cypress", "Cypress", "testing"),
    _s("playwright", "Playwright", "testing"),
    _s("jest", "Jest", "testing"),
    _s("junit", "JUnit", "testing"),
    _s("test_automation", "Test Automation", "testing", aliases=("automated testing", "automation testing")),

    # ---------------- Certifications ----------------
    _s("aws_certified_ml", "AWS Certified Machine Learning", "certification", aliases=("aws certified machine learning specialty", "aws ml certification")),
    _s("aws_solutions_architect", "AWS Certified Solutions Architect", "certification", aliases=("aws solutions architect",)),
    _s("microsoft_azure_ai", "Microsoft Azure AI", "certification", aliases=("azure ai engineer", "microsoft azure ai engineer", "azure ai", "az-900", "ai-102")),
    _s("google_cloud_certified", "Google Cloud Certified", "certification", aliases=("google cloud certification", "gcp professional", "google professional cloud architect")),
    _s("huawei_ai_certification", "Huawei AI Certification", "certification", aliases=("huawei certification", "huawei hciai")),
    _s("databricks_certified", "Databricks Certified", "certification", aliases=("databricks certification",)),
    _s("tensorflow_developer", "TensorFlow Developer Certificate", "certification", aliases=("tensorflow developer",)),
    _s("cfa", "CFA", "certification", aliases=("chartered financial analyst",)),
    _s("pmp", "PMP", "certification", aliases=("project management professional",)),
    _s("scrum_master", "Certified ScrumMaster", "certification", aliases=("csm", "scrum master", "scrum")),
    _s("istqb", "ISTQB", "certification", aliases=("istqb certified",)),
    _s("ccna", "CCNA", "certification", aliases=("cisco certified network associate",)),
    _s("cissp", "CISSP", "certification"),

    # ---------------- Soft skills ----------------
    _s("communication", "Communication", "soft_skill", aliases=("verbal communication", "written communication", "communication skills")),
    _s("leadership", "Leadership", "soft_skill", aliases=("team leadership", "leadership skills")),
    _s("teamwork", "Teamwork", "soft_skill", aliases=("collaboration", "team work", "collaborative")),
    _s("problem_solving", "Problem Solving", "soft_skill", aliases=("problem solving skills", "analytical thinking", "critical thinking", "critical analysis")),
    _s("time_management", "Time Management", "soft_skill", aliases=("time management skills",)),
    _s("adaptability", "Adaptability", "soft_skill", aliases=("flexibility",)),
    _s("attention_to_detail", "Attention to Detail", "soft_skill", aliases=("detail oriented", "detail-oriented")),
    _s("presentation", "Presentation", "soft_skill", aliases=("presentations", "public speaking")),
    _s("mentoring", "Mentoring", "soft_skill", aliases=("coaching", "mentorship")),
    _s("project_management", "Project Management", "soft_skill", aliases=("project management skills", "pmp")),
    _s("stakeholder_management", "Stakeholder Management", "soft_skill", aliases=("stakeholder engagement",)),
    _s("agile", "Agile", "soft_skill", aliases=("agile methodologies", "agile methodology", "safe", "scrum", "kanban")),
)


# --------------------------------------------------------------------------- #
# Lookup indexes
# --------------------------------------------------------------------------- #
def _build_alias_index() -> dict[str, SkillDefinition]:
    """Map every surface form to its definition.

    Built in two passes because ``aliases`` and especially ``related`` overlap
    with other skills' real names. ``deep_learning`` lists ``pytorch`` and
    ``tensorflow`` under ``related``, and it is declared before them, so a
    single pass with ``setdefault`` made ``normalize_skill("PyTorch")`` return
    *Deep Learning* - silently merging three distinct requirements into one and
    reporting PyTorch as missing from every resume that lists it.

    Pass 1 registers each skill's own identity, which always wins. Pass 2 adds
    aliases and related terms only where nothing is registered yet.
    """
    index: dict[str, SkillDefinition] = {}

    for definition in SKILL_TAXONOMY:
        for key in (definition.normalized_name, definition.normalized_name.replace("_", " "), definition.name):
            cleaned = normalize_text(key)
            if cleaned:
                index[cleaned] = definition

    for definition in SKILL_TAXONOMY:
        for key in (*definition.aliases, *definition.related):
            cleaned = normalize_text(key)
            if cleaned and cleaned not in index:
                index[cleaned] = definition

    return index


def normalize_text(value: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9+#.\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


@lru_cache(maxsize=1)
def alias_index() -> dict[str, SkillDefinition]:
    return _build_alias_index()


@lru_cache(maxsize=1)
def _alias_pattern() -> re.Pattern[str]:
    """Single alternation regex of every alias, longest first."""
    keys = sorted(alias_index().keys(), key=len, reverse=True)
    keys = [re.escape(k) for k in keys]
    return re.compile(r"(?<![a-z0-9])(" + "|".join(keys) + r")(?![a-z0-9])")


@lru_cache(maxsize=1)
def skill_by_normalized_name() -> dict[str, SkillDefinition]:
    return {d.normalized_name: d for d in SKILL_TAXONOMY}


def lookup(term: str) -> SkillDefinition | None:
    """Resolve a single surface form to its canonical skill, or ``None``."""
    key = normalize_text(term)
    if not key:
        return None
    definition = alias_index().get(key)
    if definition:
        return definition
    # Tolerate simple plural / possessive variants ("pythons", "python's").
    stripped = re.sub(r"'s$", "", key)
    if stripped != key:
        return alias_index().get(stripped)
    for suffix in ("s", "es", "js"):
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            trimmed = key[: -len(suffix)]
            found = alias_index().get(trimmed) or alias_index().get(trimmed + suffix[:1])
            if found:
                return found
    return None


def slugify_unknown(term: str) -> str:
    """Deterministic normalised id for a skill outside the taxonomy."""
    key = normalize_text(term)
    key = re.sub(r"\.net$", " dotnet", key)
    key = re.sub(r"[^a-z0-9+# ]", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key.replace(" ", "_") or "unknown"


def normalize_skill(term: str) -> tuple[str, str | None, str]:
    """Normalise a raw skill string.

    Returns ``(normalized_name, category, display_name)``. Unknown skills get a
    slug with category ``"other"`` so nothing is silently lost.
    """
    definition = lookup(term)
    if definition:
        return definition.normalized_name, definition.category, definition.name
    return slugify_unknown(term), "other", term.strip()


def _regex_for_forms(forms: Iterable[str]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(normalize_text(f)) for f in sorted(forms, key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(" + alternation + r")(?![a-z0-9])")


def find_skill_mentions(text: str) -> list[tuple[str, str, int, int]]:
    """Find all known skill mentions in ``text``.

    Returns a list of ``(surface_form, normalized_name, start, end)`` tuples in
    order of appearance. Overlapping aliases are resolved by keeping the
    longest match at each position.
    """
    if not text:
        return []
    normalized = normalize_text(text)
    pattern = _alias_pattern()
    index = alias_index()
    out: list[tuple[str, str, int, int]] = []
    position = 0
    while position < len(normalized):
        match = pattern.search(normalized, position)
        if not match:
            break
        surface = match.group(1)
        definition = index.get(surface)
        if definition:
            out.append((surface, definition.normalized_name, match.start(), match.end()))
        position = match.end()
    return out


__all__ = [
    "SKILL_TAXONOMY",
    "SkillDefinition",
    "find_skill_mentions",
    "lookup",
    "normalize_skill",
    "normalize_text",
    "skill_by_normalized_name",
    "slugify_unknown",
]
