"""
Storage manager for job scanner pipeline.
Handles per-role directory storage and legacy data migration.

Directory structure:
    output/roles/{role_id}/
    ├── job_posting.json
    ├── evaluation.json
    ├── application_plan.json (if Stage 2 run)
    └── pipeline_state.json
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import get_output_dir, get_scrape_setting, ConfigError
from models import (
    JobPosting, Evaluation, ApplicationPlan, PipelineState,
    extract_linkedin_job_id, generate_canonical_id, generate_role_id,
    generate_display_name, get_default_next_action
)


class RoleStorage:
    """
    Manages storage of role data in per-role directories.
    Primary storage is per-role directory objects.
    scan-results.json is derived only, never primary write target.
    """

    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize storage manager.

        Args:
            output_dir: Output directory path. If None, reads from config.
        """
        if output_dir is None:
            try:
                output_dir = get_output_dir()
            except ConfigError:
                # Fallback if config not available
                output_dir = Path(__file__).parent.parent / "output"

        self.output_dir = Path(output_dir)
        self.roles_dir = self.output_dir / "roles"
        self.quarantine_dir = self.output_dir / "quarantine"
        self.needs_attention_dir = self.output_dir / "needs_attention"

        # In-memory index for fast dedupe checks (canonical_id → role_id)
        self._role_index: dict[str, str] = {}

        # Ensure directories exist
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.needs_attention_dir.mkdir(parents=True, exist_ok=True)

        # Build index from existing roles
        self._build_role_index()

    def _build_role_index(self):
        """Build in-memory index from existing roles."""
        for role_id in self._list_role_dirs():
            posting = self.load_job_posting(role_id)
            if posting and posting.canonical_id:
                self._role_index[posting.canonical_id] = role_id

    def _list_role_dirs(self) -> list[str]:
        """List role directories without loading postings (for index building)."""
        if not self.roles_dir.exists():
            return []
        return [
            d.name for d in self.roles_dir.iterdir()
            if d.is_dir() and (d / "job_posting.json").exists()
        ]

    def role_exists(self, role_id: str) -> bool:
        """Check if a role directory exists (index first, then filesystem)."""
        # Fast path: check in-memory index
        if role_id in self._role_index.values():
            return True
        # Slow path: filesystem check
        return (self.roles_dir / role_id).exists()

    def get_role_dir(self, role_id: str) -> Path:
        """Get the directory path for a role."""
        role_dir = self.roles_dir / role_id
        role_dir.mkdir(parents=True, exist_ok=True)
        return role_dir

    def save_job_posting(self, posting: JobPosting) -> Path:
        """
        Save a JobPosting to storage.

        Args:
            posting: The JobPosting object to save

        Returns:
            Path to the saved file
        """
        role_dir = self.get_role_dir(posting.role_id)
        filepath = role_dir / "job_posting.json"

        with open(filepath, "w") as f:
            json.dump(posting.to_dict(), f, indent=2)

        # Update in-memory index
        if posting.canonical_id:
            self._role_index[posting.canonical_id] = posting.role_id

        print(f"[STORAGE] Saved job_posting.json for {posting.role_id}")
        return filepath

    def save_evaluation(self, evaluation: Evaluation) -> Path:
        """
        Save an Evaluation to storage.

        Args:
            evaluation: The Evaluation object to save

        Returns:
            Path to the saved file
        """
        role_dir = self.get_role_dir(evaluation.role_id)
        filepath = role_dir / "evaluation.json"

        with open(filepath, "w") as f:
            json.dump(evaluation.to_dict(), f, indent=2)

        print(f"[STORAGE] Saved evaluation.json for {evaluation.role_id}")
        return filepath

    def save_application_plan(self, plan: ApplicationPlan) -> Path:
        """
        Save an ApplicationPlan to storage.

        Args:
            plan: The ApplicationPlan object to save

        Returns:
            Path to the saved file
        """
        role_dir = self.get_role_dir(plan.role_id)
        filepath = role_dir / "application_plan.json"

        with open(filepath, "w") as f:
            json.dump(plan.to_dict(), f, indent=2)

        print(f"[STORAGE] Saved application_plan.json for {plan.role_id}")
        return filepath

    def save_pipeline_state(self, state: PipelineState) -> Path:
        """
        Save a PipelineState to storage.

        Args:
            state: The PipelineState object to save

        Returns:
            Path to the saved file
        """
        role_dir = self.get_role_dir(state.role_id)
        filepath = role_dir / "pipeline_state.json"

        with open(filepath, "w") as f:
            json.dump(state.to_dict(), f, indent=2)

        print(f"[STORAGE] Saved pipeline_state.json for {state.role_id}")
        return filepath

    def load_job_posting(self, role_id: str) -> Optional[JobPosting]:
        """Load a JobPosting from storage."""
        filepath = self.roles_dir / role_id / "job_posting.json"
        if not filepath.exists():
            return None

        with open(filepath) as f:
            data = json.load(f)
        return JobPosting.from_dict(data)

    def load_evaluation(self, role_id: str) -> Optional[Evaluation]:
        """Load an Evaluation from storage."""
        filepath = self.roles_dir / role_id / "evaluation.json"
        if not filepath.exists():
            return None

        with open(filepath) as f:
            data = json.load(f)
        return Evaluation.from_dict(data)

    def load_application_plan(self, role_id: str) -> Optional[ApplicationPlan]:
        """Load an ApplicationPlan from storage."""
        filepath = self.roles_dir / role_id / "application_plan.json"
        if not filepath.exists():
            return None

        with open(filepath) as f:
            data = json.load(f)
        return ApplicationPlan.from_dict(data)

    def load_pipeline_state(self, role_id: str) -> Optional[PipelineState]:
        """Load a PipelineState from storage."""
        filepath = self.roles_dir / role_id / "pipeline_state.json"
        if not filepath.exists():
            return None

        with open(filepath) as f:
            data = json.load(f)
        return PipelineState.from_dict(data)

    def list_roles(self) -> list[str]:
        """
        List all role IDs in storage.

        Returns:
            List of role_id strings
        """
        if not self.roles_dir.exists():
            return []

        return [
            d.name for d in self.roles_dir.iterdir()
            if d.is_dir() and (d / "job_posting.json").exists()
        ]

    def get_role_summary(self, role_id: str) -> Optional[dict]:
        """
        Get a summary of a role for scan-results generation.

        Returns:
            Dictionary with role summary or None if role doesn't exist
        """
        posting = self.load_job_posting(role_id)
        evaluation = self.load_evaluation(role_id)
        plan = self.load_application_plan(role_id)
        state = self.load_pipeline_state(role_id)

        if not posting:
            return None

        extracted = posting.extracted_fields
        summary = {
            "role_id": role_id,
            "company": extracted.get("company", ""),
            "title": extracted.get("title", ""),
            "location": extracted.get("location", ""),
            "job_url": extracted.get("job_url", ""),
            "posted_age": extracted.get("posted_age"),
            "source": posting.source,
            "display_name": posting.display_name,
        }

        if evaluation:
            summary["evaluation"] = {
                "role_fit_score": evaluation.role_fit_score,
                "final_decision": evaluation.final_decision,
                "confidence_signal": evaluation.confidence_signal,
                "risk_level": evaluation.risk_level,
                "seniority_level": evaluation.seniority_level,
                "remote_status": evaluation.remote_status,
                "role_classification": evaluation.role_classification,
                "summary": evaluation.summary,
                "is_valid": evaluation.is_valid,
            }

        if plan:
            summary["stage2"] = {
                "has_cover_letter": plan.cover_letter_text is not None,
                "has_recruiter_message": plan.recruiter_message is not None,
                "generated_at": plan.generated_at,
            }

        if state:
            summary["pipeline_state"] = {
                "status": state.status,
                "outreach_status": state.outreach_status,
                "next_action": state.next_action,
            }

        return summary

    def generate_scan_results(self) -> dict:
        """
        Generate scan-results.json content from role objects.
        This is derived data, not primary storage.

        Returns:
            Dictionary in scan-results.json format
        """
        roles = self.list_roles()
        jobs = []
        failed = []

        stats = {
            "total_roles": 0,
            "valid_evaluated": 0,
            "apply_count": 0,
            "consider_count": 0,
            "skip_count": 0,
            "failed_count": 0,
        }

        for role_id in roles:
            summary = self.get_role_summary(role_id)
            if not summary:
                continue

            stats["total_roles"] += 1

            eval_data = summary.get("evaluation", {})
            is_valid = eval_data.get("is_valid", True)

            if not is_valid:
                failed.append(summary)
                stats["failed_count"] += 1
            else:
                jobs.append(summary)
                stats["valid_evaluated"] += 1

                decision = eval_data.get("final_decision", "SKIP")
                if decision == "APPLY":
                    stats["apply_count"] += 1
                elif decision == "CONSIDER":
                    stats["consider_count"] += 1
                else:
                    stats["skip_count"] += 1

        return {
            "generated_at": datetime.now().isoformat(),
            "source": "derived_from_roles",
            "jobs": jobs,
            "failed": failed,
            "stats": stats,
        }

    def save_scan_results(self) -> Path:
        """
        Regenerate and save scan-results.json from role objects.

        Returns:
            Path to saved file
        """
        results = self.generate_scan_results()
        filepath = self.output_dir / "scan-results.json"

        # Load existing scans to append
        all_results = {"scans": []}
        if filepath.exists():
            try:
                with open(filepath) as f:
                    all_results = json.load(f)
            except Exception:
                pass

        # Add derived results as a new "scan"
        all_results["derived"] = results

        with open(filepath, "w") as f:
            json.dump(all_results, f, indent=2)

        print(f"[STORAGE] Regenerated scan-results.json")
        return filepath

    def quarantine(self, data: dict, reason: str) -> Path:
        """
        Move invalid data to quarantine directory.

        Args:
            data: The data to quarantine
            reason: Reason for quarantine

        Returns:
            Path to quarantined file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{reason}_{timestamp}.json"
        filepath = self.quarantine_dir / filename

        quarantine_data = {
            "quarantined_at": datetime.now().isoformat(),
            "reason": reason,
            "data": data,
        }

        with open(filepath, "w") as f:
            json.dump(quarantine_data, f, indent=2)

        print(f"[STORAGE] Quarantined data: {reason}")
        return filepath

    def needs_attention(self, data: dict, role_id: str, reason: str) -> Path:
        """
        Move recoverable issues to needs_attention directory.

        Args:
            data: The data that needs attention
            role_id: The role ID if available
            reason: Reason for attention needed

        Returns:
            Path to needs_attention file
        """
        filename = f"{role_id}_{reason}.json" if role_id else f"{reason}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.needs_attention_dir / filename

        attention_data = {
            "flagged_at": datetime.now().isoformat(),
            "reason": reason,
            "role_id": role_id,
            "data": data,
        }

        with open(filepath, "w") as f:
            json.dump(attention_data, f, indent=2)

        print(f"[STORAGE] Flagged for attention: {reason}")
        return filepath


def migrate_legacy_data(output_dir: Optional[Path] = None) -> dict:
    """
    Migrate legacy scan-results.json and job-descriptions.json to new role-based storage.

    Args:
        output_dir: Output directory path. If None, reads from config.

    Returns:
        Migration summary dictionary
    """
    if output_dir is None:
        try:
            output_dir = get_output_dir()
        except ConfigError:
            output_dir = Path(__file__).parent.parent / "output"

    output_dir = Path(output_dir)
    storage = RoleStorage(output_dir)

    # Migration summary
    summary = {
        "started_at": datetime.now().isoformat(),
        "migrated_count": 0,
        "skipped_count": 0,
        "quarantine_count": 0,
        "needs_attention_count": 0,
        "errors": [],
    }

    # Load legacy files
    scan_results_path = output_dir / "scan-results.json"
    descriptions_path = output_dir / "job-descriptions.json"

    if not scan_results_path.exists():
        print("[MIGRATION] No legacy scan-results.json found")
        return summary

    print("[MIGRATION] Starting legacy data migration...")

    try:
        with open(scan_results_path) as f:
            scan_results = json.load(f)
    except Exception as e:
        summary["errors"].append(f"Failed to load scan-results.json: {e}")
        return summary

    # Load descriptions
    descriptions = {}
    if descriptions_path.exists():
        try:
            with open(descriptions_path) as f:
                descriptions = json.load(f)
        except Exception:
            pass

    # Get min description length from config
    try:
        min_length = get_scrape_setting("min_description_length")
    except ConfigError:
        min_length = 100

    # Count total roles
    total_roles = 0
    for scan in scan_results.get("scans", []):
        total_roles += len(scan.get("jobs", []))
        total_roles += len(scan.get("failed", []))

    print(f"[MIGRATION] Found {total_roles} roles in scan-results.json")

    # Process each scan
    for scan in scan_results.get("scans", []):
        scan_date = scan.get("scan_date", "")

        # Process valid jobs
        for job in scan.get("jobs", []):
            result = _migrate_single_job(
                job, descriptions, storage, scan_date, min_length, summary
            )

        # Process failed jobs
        for job in scan.get("failed", []):
            result = _migrate_single_job(
                job, descriptions, storage, scan_date, min_length, summary, is_failed=True
            )

    summary["completed_at"] = datetime.now().isoformat()

    # Print summary
    print("\n" + "=" * 50)
    print("[MIGRATION] Complete:")
    print(f"  - migrated_count: {summary['migrated_count']}")
    print(f"  - skipped_count: {summary['skipped_count']} (already exist)")
    print(f"  - quarantine_count: {summary['quarantine_count']}")
    print(f"  - needs_attention_count: {summary['needs_attention_count']}")
    print("[MIGRATION] Legacy files preserved")
    print("=" * 50 + "\n")

    return summary


def _migrate_single_job(
    job: dict,
    descriptions: dict,
    storage: RoleStorage,
    scan_date: str,
    min_length: int,
    summary: dict,
    is_failed: bool = False
) -> bool:
    """
    Migrate a single job entry to new storage format.

    Returns:
        True if migrated, False if skipped/failed
    """
    job_url = job.get("job_url", "")

    # Extract job ID
    job_id = extract_linkedin_job_id(job_url)

    if not job_id:
        storage.quarantine(job, "no_job_id")
        summary["quarantine_count"] += 1
        return False

    # Generate canonical ID and role ID
    canonical_id = generate_canonical_id("linkedin_scrape", job_id)
    role_id, role_id_full = generate_role_id(canonical_id)

    # Check if already exists (idempotent)
    if storage.role_exists(role_id):
        print(f"[MIGRATION] Skipping {role_id}, already exists")
        summary["skipped_count"] += 1
        return False

    # Get description
    description = descriptions.get(job_url, "")

    # Handle missing or short descriptions
    if not description:
        storage.needs_attention(job, role_id, "missing_description")
        summary["needs_attention_count"] += 1
        return False

    missing_fields = []
    if len(description) < min_length:
        missing_fields.append("description_too_short")
        print(f"[MIGRATION] Warning: {role_id} has short description ({len(description)} chars)")

    # Create JobPosting
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    display_name = generate_display_name(company, title)

    posting = JobPosting(
        schema_version="1.0",
        role_id=role_id,
        role_id_full=role_id_full,
        canonical_id=canonical_id,
        display_name=display_name,
        source="linkedin_scrape",
        run_id="migration",
        extracted_fields={
            "company": company,
            "title": title,
            "location": job.get("location", ""),
            "job_url": job_url,
            "description": description,
            "posted_age": job.get("posted_age"),
        },
        scrape_diagnostics={
            "scraped_at": scan_date,
            "extraction_method": "legacy_migration",
            "missing_fields": missing_fields,
            "description_length": len(description),
        },
        migrated_from_version="legacy",
        migrated_at=datetime.now().isoformat(),
    )

    storage.save_job_posting(posting)

    # Create Evaluation from legacy data
    eval_data = job.get("evaluation", {})
    evaluation = Evaluation(
        schema_version="1.0",
        role_id=role_id,
        run_id="migration",
        role_fit_score=eval_data.get("role_fit_score", 0),
        role_classification=eval_data.get("role_classification", ""),
        seniority_level=eval_data.get("seniority_level", "Unknown"),
        remote_status=eval_data.get("remote_status", "Unknown"),
        risk_level=eval_data.get("risk_level", "high"),
        final_decision=eval_data.get("final_decision", "SKIP"),
        confidence_signal=eval_data.get("confidence_signal", "LOW").upper(),
        key_requirements=eval_data.get("key_requirements", []),
        concerns=eval_data.get("concerns", []),
        summary=eval_data.get("summary", ""),
        is_valid=not is_failed and eval_data.get("is_valid", True),
        error=eval_data.get("error"),
        evaluated_at=scan_date,
        evaluator_metadata=eval_data.get("_metadata", {}),
    )

    storage.save_evaluation(evaluation)

    # Create PipelineState
    final_decision = eval_data.get("final_decision", "SKIP")
    state = PipelineState(
        schema_version="1.0",
        role_id=role_id,
        status="pending",
        next_action=get_default_next_action(final_decision),
    )

    storage.save_pipeline_state(state)

    # Migrate Stage 2 outputs if they exist
    stage2 = job.get("stage2")
    if stage2:
        plan = ApplicationPlan(
            schema_version="1.0",
            role_id=role_id,
            cover_letter_text=stage2.get("cover_letter"),
            recruiter_message=stage2.get("recruiter_message"),
            generated_at=stage2.get("generated_at"),
        )
        storage.save_application_plan(plan)

    summary["migrated_count"] += 1
    print(f"[MIGRATION] Migrated {display_name} -> {role_id}")
    return True


def check_and_run_migration(output_dir: Optional[Path] = None) -> bool:
    """
    Check if migration is needed and run if so.
    Called on pipeline startup.

    Args:
        output_dir: Output directory path

    Returns:
        True if migration was run, False otherwise
    """
    if output_dir is None:
        try:
            output_dir = get_output_dir()
        except ConfigError:
            output_dir = Path(__file__).parent.parent / "output"

    output_dir = Path(output_dir)
    scan_results_path = output_dir / "scan-results.json"
    roles_dir = output_dir / "roles"

    # Check if legacy data exists and roles don't
    has_legacy = scan_results_path.exists()
    has_roles = roles_dir.exists() and any(roles_dir.iterdir())

    if has_legacy and not has_roles:
        print("[STORAGE] Legacy data detected, running migration...")
        migrate_legacy_data(output_dir)
        return True

    return False
