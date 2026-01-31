"""
Domain models for job scanner pipeline.
Contains dataclasses for all pipeline objects and role ID generation functions.
"""

import re
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, parse_qs


def extract_linkedin_job_id(url: str) -> Optional[str]:
    """
    Extract LinkedIn job ID from URL.

    Handles:
    - /jobs/view/4318547133/
    - ?currentJobId=4318547133
    - ?jobId=4318547133

    Args:
        url: LinkedIn job URL

    Returns:
        Job ID string or None if not found
    """
    if not url:
        return None

    # Pattern 1: /jobs/view/{id}/
    path_match = re.search(r'/jobs/view/(\d+)', url)
    if path_match:
        return path_match.group(1)

    # Pattern 2: Query param currentJobId={id}
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    if 'currentJobId' in params:
        return params['currentJobId'][0]

    # Pattern 3: Query param jobId={id}
    if 'jobId' in params:
        return params['jobId'][0]

    return None


def generate_canonical_id(source: str, job_id: str, url: Optional[str] = None) -> str:
    """
    Build canonical ID string based on source.

    IMPORTANT: Different sources generate different canonical_ids even for
    the same underlying job posting. This is intentional:
    - linkedin_scrape:job:123 != company_careers:url:abc123
    - Allows tracking how you found the same role
    - Manual merge capability may be added later if duplicates discovered

    This is a product decision: accept Week 1, revisit if problematic.

    Canonical ID Format by Source:
    | Source | Format | Example |
    |--------|--------|---------|
    | linkedin_scrape | linkedin:job:{id} | linkedin:job:4318547133 |
    | linkedin_manual | linkedin:job:{id} | linkedin:job:4318547133 |
    | company_careers | careers:url:{sha256(normalized_url)[:16]} | careers:url:a1b2c3d4e5f6g7h8 |
    | job_board | board:{board_name}:{posting_id} | board:lever:abc123 |
    | recruiter_inbound | recruiter:{message_id}:{url_hash[:8]} | recruiter:msg123:a1b2c3d4 |
    | referral | referral:{url_hash[:16]} | referral:a1b2c3d4e5f6g7h8 |

    Args:
        source: Source type (linkedin_scrape, linkedin_manual, etc.)
        job_id: The extracted job ID
        url: Optional URL for sources that need it

    Returns:
        Canonical ID string
    """
    if source in ("linkedin_scrape", "linkedin_manual"):
        return f"linkedin:job:{job_id}"

    elif source == "company_careers":
        if url:
            normalized = _normalize_url_for_hash(url)
            url_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
            return f"careers:url:{url_hash}"
        return f"careers:id:{job_id}"

    elif source == "job_board":
        # job_id format expected: "board_name:posting_id"
        return f"board:{job_id}"

    elif source == "recruiter_inbound":
        if url:
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
            return f"recruiter:{job_id}:{url_hash}"
        return f"recruiter:{job_id}"

    elif source == "referral":
        if url:
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
            return f"referral:{url_hash}"
        return f"referral:{job_id}"

    # Fallback
    return f"{source}:{job_id}"


def generate_role_id(canonical_id: str) -> tuple[str, str]:
    """
    Hash canonical ID to create role_id.

    Args:
        canonical_id: The canonical ID string (e.g., "linkedin:job:4318547133")

    Returns:
        Tuple of (role_id_short, role_id_full)
        - role_id_short: First 12 characters (used as folder name)
        - role_id_full: Full SHA256 hash
    """
    full_hash = hashlib.sha256(canonical_id.encode()).hexdigest()
    short_hash = full_hash[:12]
    return short_hash, full_hash


def generate_display_name(company: str, title: str) -> str:
    """
    Create slug for logs/dashboards.

    Args:
        company: Company name
        title: Job title

    Returns:
        Slugified display name (e.g., 'assured-staff-cloud-infra')
    """
    # Combine and slugify
    combined = f"{company}-{title}"

    # Convert to lowercase
    slug = combined.lower()

    # Remove special characters, keep alphanumeric and spaces
    slug = re.sub(r'[^\w\s-]', '', slug)

    # Replace whitespace with hyphens
    slug = re.sub(r'[-\s]+', '-', slug)

    # Trim hyphens from ends
    slug = slug.strip('-')

    # Truncate to reasonable length
    return slug[:50]


def _normalize_url_for_hash(url: str) -> str:
    """
    Normalize URL for hashing (not for identity).

    - Lowercase scheme and host only
    - Remove fragment
    - Strip known tracking params
    - Sort query params for determinism
    - Keep path case as-is
    """
    parsed = urlparse(url)

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Keep path as-is (don't lowercase)
    path = parsed.path

    # Parse and filter query params
    params = parse_qs(parsed.query, keep_blank_values=True)

    # Remove known tracking params
    tracking_params = {
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'ref', 'referer', 'referrer', 'source', 'fbclid', 'gclid', 'mc_cid',
        'mc_eid', 'trk', 'trackingId', 'lipi', 'refId',
    }

    filtered_params = {
        k: v for k, v in params.items()
        if k.lower() not in tracking_params
    }

    # Sort params for determinism
    sorted_params = sorted(filtered_params.items())
    query = '&'.join(f"{k}={v[0]}" for k, v in sorted_params if v)

    # Reconstruct without fragment
    if query:
        return f"{scheme}://{netloc}{path}?{query}"
    return f"{scheme}://{netloc}{path}"


@dataclass
class JobPosting:
    """
    Represents a scraped job posting.
    Stored in output/roles/{role_id}/job_posting.json
    """
    schema_version: str
    role_id: str
    role_id_full: str
    canonical_id: str
    display_name: str
    source: str
    run_id: str
    extracted_fields: dict  # company, title, location, job_url, description, posted_age
    scrape_diagnostics: dict  # selector_used, missing_fields, description_length, scraped_at, extraction_method
    raw_html: Optional[str] = None
    migrated_from_version: Optional[str] = None
    migrated_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "role_id_full": self.role_id_full,
            "canonical_id": self.canonical_id,
            "display_name": self.display_name,
            "source": self.source,
            "run_id": self.run_id,
            "extracted_fields": self.extracted_fields,
            "scrape_diagnostics": self.scrape_diagnostics,
            "raw_html": self.raw_html,
            "migrated_from_version": self.migrated_from_version,
            "migrated_at": self.migrated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JobPosting":
        """Create from dictionary."""
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            role_id=data["role_id"],
            role_id_full=data.get("role_id_full", ""),
            canonical_id=data.get("canonical_id", ""),
            display_name=data.get("display_name", ""),
            source=data.get("source", "linkedin_scrape"),
            run_id=data.get("run_id", ""),
            extracted_fields=data.get("extracted_fields", {}),
            scrape_diagnostics=data.get("scrape_diagnostics", {}),
            raw_html=data.get("raw_html"),
            migrated_from_version=data.get("migrated_from_version"),
            migrated_at=data.get("migrated_at"),
        )


@dataclass
class Evaluation:
    """
    Represents a Stage 1 evaluation result.
    Stored in output/roles/{role_id}/evaluation.json
    """
    schema_version: str
    role_id: str
    run_id: str
    role_fit_score: int
    role_classification: str
    seniority_level: str
    remote_status: str
    risk_level: str
    final_decision: str
    confidence_signal: str
    key_requirements: list[str]
    concerns: list[str]
    summary: str
    # Top-tier targeting fields (populated in Week 2)
    must_haves_matched: list[str] = field(default_factory=list)
    dealbreakers: list[str] = field(default_factory=list)
    resume_angles: list[str] = field(default_factory=list)
    outreach_angle: str = ""
    # Validity tracking
    is_valid: bool = True
    error: Optional[str] = None
    evaluated_at: str = ""
    evaluator_metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "run_id": self.run_id,
            "role_fit_score": self.role_fit_score,
            "role_classification": self.role_classification,
            "seniority_level": self.seniority_level,
            "remote_status": self.remote_status,
            "risk_level": self.risk_level,
            "final_decision": self.final_decision,
            "confidence_signal": self.confidence_signal,
            "key_requirements": self.key_requirements,
            "concerns": self.concerns,
            "summary": self.summary,
            "must_haves_matched": self.must_haves_matched,
            "dealbreakers": self.dealbreakers,
            "resume_angles": self.resume_angles,
            "outreach_angle": self.outreach_angle,
            "is_valid": self.is_valid,
            "error": self.error,
            "evaluated_at": self.evaluated_at,
            "evaluator_metadata": self.evaluator_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Evaluation":
        """Create from dictionary."""
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            role_id=data.get("role_id", ""),
            run_id=data.get("run_id", ""),
            role_fit_score=data.get("role_fit_score", 0),
            role_classification=data.get("role_classification", ""),
            seniority_level=data.get("seniority_level", "Unknown"),
            remote_status=data.get("remote_status", "Unknown"),
            risk_level=data.get("risk_level", "high"),
            final_decision=data.get("final_decision", "SKIP"),
            confidence_signal=data.get("confidence_signal", "LOW"),
            key_requirements=data.get("key_requirements", []),
            concerns=data.get("concerns", []),
            summary=data.get("summary", ""),
            must_haves_matched=data.get("must_haves_matched", []),
            dealbreakers=data.get("dealbreakers", []),
            resume_angles=data.get("resume_angles", []),
            outreach_angle=data.get("outreach_angle", ""),
            is_valid=data.get("is_valid", True),
            error=data.get("error"),
            evaluated_at=data.get("evaluated_at", ""),
            evaluator_metadata=data.get("evaluator_metadata", {}),
        )


@dataclass
class ApplicationPlan:
    """
    Represents Stage 2 writing outputs.
    Stored in output/roles/{role_id}/application_plan.json
    """
    schema_version: str
    role_id: str
    cover_letter_text: Optional[str] = None
    recruiter_message: Optional[str] = None
    generated_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "cover_letter_text": self.cover_letter_text,
            "recruiter_message": self.recruiter_message,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ApplicationPlan":
        """Create from dictionary."""
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            role_id=data.get("role_id", ""),
            cover_letter_text=data.get("cover_letter_text"),
            recruiter_message=data.get("recruiter_message"),
            generated_at=data.get("generated_at"),
        )


@dataclass
class PipelineState:
    """
    Represents the workflow state for a role.
    Stored in output/roles/{role_id}/pipeline_state.json
    """
    schema_version: str
    role_id: str
    outreach_status: str = "not_started"
    referral_status: str = "not_started"
    next_action: str = ""
    next_action_due_date: Optional[str] = None
    application_date: Optional[str] = None
    status: str = "pending"
    contacts: dict = field(default_factory=lambda: {
        "recruiter": None,
        "hiring_manager": None,
        "referral_targets": []
    })

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "outreach_status": self.outreach_status,
            "referral_status": self.referral_status,
            "next_action": self.next_action,
            "next_action_due_date": self.next_action_due_date,
            "application_date": self.application_date,
            "status": self.status,
            "contacts": self.contacts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineState":
        """Create from dictionary."""
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            role_id=data.get("role_id", ""),
            outreach_status=data.get("outreach_status", "not_started"),
            referral_status=data.get("referral_status", "not_started"),
            next_action=data.get("next_action", ""),
            next_action_due_date=data.get("next_action_due_date"),
            application_date=data.get("application_date"),
            status=data.get("status", "pending"),
            contacts=data.get("contacts", {
                "recruiter": None,
                "hiring_manager": None,
                "referral_targets": []
            }),
        )


def get_default_next_action(final_decision: str) -> str:
    """
    Get the default next_action based on final_decision.

    Args:
        final_decision: The evaluation decision (APPLY, CONSIDER, SKIP)

    Returns:
        Default next action string
    """
    actions = {
        "APPLY": "Find 3 contacts",
        "CONSIDER": "Review manually",
        "SKIP": "",
    }
    return actions.get(final_decision.upper(), "")
