#!/usr/bin/env python3
"""
Regenerate missing apply folder outputs from storage.

Finds roles that have application_plan.json but no corresponding
human-readable output in output/apply/, and regenerates them.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import RoleStorage
from config import get_output_dir, ConfigError


def sanitize_filename(name: str) -> str:
    """Convert string to safe filename."""
    safe = re.sub(r'[^\w\s-]', '', name.lower())
    safe = re.sub(r'[-\s]+', '-', safe).strip('-')
    return safe[:100]


def find_existing_apply_files(apply_dir: Path) -> set[str]:
    """Build a set of normalized filenames from all apply subdirectories."""
    existing = set()
    if not apply_dir.exists():
        return existing

    for subdir in apply_dir.iterdir():
        if subdir.is_dir():
            for f in subdir.glob("*.json"):
                # Normalize: lowercase, strip extension
                existing.add(f.stem.lower())

    return existing


def regenerate_missing():
    """Find and regenerate missing apply folder outputs."""
    try:
        output_dir = get_output_dir()
    except ConfigError:
        output_dir = Path(__file__).parent.parent / "output"

    storage = RoleStorage(output_dir)
    apply_dir = output_dir / "apply"

    # Get existing apply files
    existing_files = find_existing_apply_files(apply_dir)
    print(f"[REGEN] Found {len(existing_files)} existing apply files")

    # Create output directory for regenerated files
    regen_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    regen_dir = apply_dir / f"{regen_timestamp}_regenerated"

    # Find roles with application plans
    role_ids = storage.list_roles()

    missing = []
    regenerated = 0

    for role_id in role_ids:
        # Check if has application plan
        plan = storage.load_application_plan(role_id)
        if not plan:
            continue

        if not plan.cover_letter_text and not plan.recruiter_message:
            continue

        # Load posting for company/title
        posting = storage.load_job_posting(role_id)
        if not posting:
            print(f"[REGEN] Warning: {role_id} has plan but no posting")
            continue

        company = posting.extracted_fields.get("company", "Unknown")
        title = posting.extracted_fields.get("title", "Unknown")

        # Check if exists in apply folder
        expected_filename = sanitize_filename(f"{company}-{title}")

        if expected_filename in existing_files:
            continue

        # Missing - needs regeneration
        missing.append({
            "role_id": role_id,
            "company": company,
            "title": title,
            "expected_filename": expected_filename,
        })

    print(f"\n[REGEN] Found {len(missing)} roles missing from apply folder:")
    for m in missing:
        print(f"  - {m['company']} - {m['title']} ({m['role_id']})")

    if not missing:
        print("[REGEN] Nothing to regenerate!")
        return

    # Confirm before regenerating
    response = input(f"\nRegenerate {len(missing)} files to {regen_dir.name}/? [y/N] ")
    if response.lower() != 'y':
        print("[REGEN] Aborted")
        return

    regen_dir.mkdir(parents=True, exist_ok=True)

    for m in missing:
        role_id = m["role_id"]

        posting = storage.load_job_posting(role_id)
        evaluation = storage.load_evaluation(role_id)
        plan = storage.load_application_plan(role_id)

        if not all([posting, evaluation, plan]):
            print(f"[REGEN] Skipping {role_id} - missing data")
            continue

        extracted = posting.extracted_fields

        role_data = {
            "metadata": {
                "company": extracted.get("company"),
                "title": extracted.get("title"),
                "location": extracted.get("location"),
                "job_url": extracted.get("job_url"),
                "posted_age": extracted.get("posted_age"),
                "role_id": role_id,
                "regenerated_at": datetime.now().isoformat(),
            },
            "job_description": extracted.get("description", ""),
            "stage1_evaluation": {
                "role_fit_score": evaluation.role_fit_score,
                "final_decision": evaluation.final_decision,
                "confidence_signal": evaluation.confidence_signal,
                "risk_level": evaluation.risk_level,
                "seniority_level": evaluation.seniority_level,
                "remote_status": evaluation.remote_status,
                "role_classification": evaluation.role_classification,
                "key_requirements": evaluation.key_requirements,
                "concerns": evaluation.concerns,
                "summary": evaluation.summary,
            },
            "stage2_writing": {
                "cover_letter": plan.cover_letter_text,
                "recruiter_message": plan.recruiter_message,
                "generated_at": plan.generated_at,
            },
            "tracking": {
                "application_date": None,
                "follow_up_date": None,
                "status": "pending",
                "notes": "",
            }
        }

        filename = sanitize_filename(f"{m['company']}-{m['title']}")
        filepath = regen_dir / f"{filename}.json"

        with open(filepath, "w") as f:
            json.dump(role_data, f, indent=2)

        print(f"[REGEN] Created: {filepath.name}")
        regenerated += 1

    print(f"\n[REGEN] Done! Regenerated {regenerated} files to {regen_dir}")


if __name__ == "__main__":
    regenerate_missing()
