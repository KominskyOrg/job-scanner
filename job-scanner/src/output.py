"""
Output handler for job scan results.
Manages JSON output with incremental writes and duplicate detection.
Handles both Stage 1 evaluations and Stage 2 writing outputs.

NOTE: scan-results.json is now a DERIVED output, not primary storage.
Primary storage is per-role directories managed by storage.py.
This module is kept for backward compatibility during migration.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import asdict

from scraper import JobData
from evaluator import JobEvaluation, JobEvaluator


class ScanOutput:
    """Manages scan results output to JSON file."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)
        self.output_file = output_dir / "scan-results.json"
        self.processed_urls: set[str] = set()
        self._load_existing()

    def _load_existing(self):
        """Load existing results to track processed URLs."""
        if self.output_file.exists():
            try:
                with open(self.output_file) as f:
                    data = json.load(f)
                    for scan in data.get("scans", []):
                        # Load from jobs (valid evaluations)
                        for job in scan.get("jobs", []):
                            url = job.get("job_url")
                            if url:
                                self.processed_urls.add(url)
                        # Load from failed (invalid evaluations)
                        for job in scan.get("failed", []):
                            url = job.get("job_url")
                            if url:
                                self.processed_urls.add(url)
                print(f"[OUTPUT] Loaded {len(self.processed_urls)} previously processed URLs")
            except Exception as e:
                print(f"[OUTPUT] Error loading existing results: {e}")

    def get_processed_urls(self) -> set[str]:
        """Return set of already processed job URLs."""
        return self.processed_urls.copy()

    def create_scan_result(self, search_name: str) -> dict:
        """Create a new scan result structure."""
        return {
            "scan_date": datetime.now().isoformat(),
            "search_name": search_name,
            "jobs": [],           # Valid evaluations only
            "failed": [],         # Invalid evaluations (for manual review)
            "stats": {
                "total_scraped": 0,
                "valid_evaluated": 0,
                "apply_count": 0,
                "consider_count": 0,
                "skip_count": 0,
                "failed_count": 0,
            }
        }

    def add_job_result(self, scan_result: dict, job_data: JobData,
                       evaluation: JobEvaluation, evaluator: JobEvaluator,
                       eval_metadata: Optional[dict] = None):
        """Add a job and its evaluation to scan results."""
        eval_dict = evaluator.to_dict(evaluation)

        # Attach metadata to evaluation if provided
        if eval_metadata:
            eval_dict["_metadata"] = eval_metadata

        job_entry = {
            "company": job_data.company,
            "title": job_data.title,
            "location": job_data.location,
            "job_url": job_data.job_url,
            "posted_age": job_data.posted_age,
            "evaluation": eval_dict,
        }

        self.processed_urls.add(job_data.job_url)
        stats = scan_result["stats"]
        stats["total_scraped"] += 1

        # Separate valid from invalid evaluations
        if not evaluation.is_valid:
            scan_result["failed"].append(job_entry)
            stats["failed_count"] += 1
            print(f"[OUTPUT] Failed: {job_data.title} -> {evaluation.error}")
        else:
            scan_result["jobs"].append(job_entry)
            stats["valid_evaluated"] += 1

            if evaluation.final_decision == "APPLY":
                stats["apply_count"] += 1
            elif evaluation.final_decision == "CONSIDER":
                stats["consider_count"] += 1
            else:
                stats["skip_count"] += 1

            print(f"[OUTPUT] Added: {job_data.title} -> {evaluation.final_decision}")

    def save_scan(self, scan_result: dict):
        """Save scan results to JSON file, appending to existing scans."""
        all_results = {"scans": []}

        # Load existing scans if file exists
        if self.output_file.exists():
            try:
                with open(self.output_file) as f:
                    all_results = json.load(f)
            except Exception:
                pass

        # Append new scan
        all_results["scans"].append(scan_result)

        # Write back
        with open(self.output_file, "w") as f:
            json.dump(all_results, f, indent=2)

        print(f"[OUTPUT] Saved scan to {self.output_file}")
        self._print_summary(scan_result)

    def _print_summary(self, scan_result: dict):
        """Print a summary of the scan results."""
        stats = scan_result["stats"]
        print("\n" + "="*50)
        print(f"SCAN COMPLETE: {scan_result['search_name']}")
        print("="*50)
        print(f"Total scraped:     {stats['total_scraped']}")
        print(f"Valid evaluated:   {stats['valid_evaluated']}")
        print(f"  APPLY:           {stats['apply_count']}")
        print(f"  CONSIDER:        {stats['consider_count']}")
        print(f"  SKIP:            {stats['skip_count']}")
        print(f"Failed (review):   {stats['failed_count']}")
        print("="*50 + "\n")

    def save_incremental(self, scan_result: dict):
        """Save current state incrementally (for crash recovery)."""
        incremental_file = self.output_dir / "scan-in-progress.json"
        with open(incremental_file, "w") as f:
            json.dump(scan_result, f, indent=2)

    def add_stage2_output(self, scan_result: dict, job_url: str,
                          cover_letter: Optional[str],
                          recruiter_message: Optional[str]):
        """Add Stage 2 writing outputs to an existing job entry."""
        # Find the job in the results
        for job in scan_result["jobs"]:
            if job.get("job_url") == job_url:
                job["stage2"] = {
                    "cover_letter": cover_letter,
                    "recruiter_message": recruiter_message,
                    "generated_at": datetime.now().isoformat(),
                }
                print(f"[OUTPUT] Added Stage 2 outputs for: {job.get('title')}")
                return True

        print(f"[OUTPUT] Could not find job to add Stage 2 outputs: {job_url}")
        return False

    def save_apply_role(self, job_entry: dict, job_description: str, run_timestamp: Optional[str] = None):
        """
        Save a per-role file for an APPLY job.
        Creates output/apply/YYYY-MM-DD_HH-MM/company-title.json
        """
        # Use provided timestamp or generate new one
        if run_timestamp:
            run_dir_name = run_timestamp
        else:
            run_dir_name = datetime.now().strftime("%Y-%m-%d_%H-%M")

        apply_dir = self.output_dir / "apply" / run_dir_name
        apply_dir.mkdir(parents=True, exist_ok=True)

        # Create safe filename (no date needed - folder has timestamp)
        company = job_entry.get("company", "unknown")
        title = job_entry.get("title", "unknown")

        safe_name = self._sanitize_filename(f"{company}-{title}")
        filepath = apply_dir / f"{safe_name}.json"

        # Build complete role file
        role_data = {
            "metadata": {
                "company": job_entry.get("company"),
                "title": job_entry.get("title"),
                "location": job_entry.get("location"),
                "job_url": job_entry.get("job_url"),
                "posted_age": job_entry.get("posted_age"),
                "scraped_at": datetime.now().isoformat(),
            },
            "job_description": job_description,
            "stage1_evaluation": job_entry.get("evaluation"),
            "stage2_writing": job_entry.get("stage2"),
            "tracking": {
                "application_date": None,
                "follow_up_date": None,
                "status": "pending",
                "notes": "",
            }
        }

        with open(filepath, "w") as f:
            json.dump(role_data, f, indent=2)

        print(f"[OUTPUT] Saved APPLY role to: {filepath}")
        return filepath

    def _sanitize_filename(self, name: str) -> str:
        """Convert string to safe filename."""
        # Remove or replace unsafe characters
        safe = re.sub(r'[^\w\s-]', '', name.lower())
        safe = re.sub(r'[-\s]+', '-', safe).strip('-')
        return safe[:100]  # Limit length

    def get_apply_roles(self, scan_result: dict) -> list[dict]:
        """Get all APPLY roles from a scan result."""
        return [
            job for job in scan_result.get("jobs", [])
            if job.get("evaluation", {}).get("final_decision") == "APPLY"
        ]

    def regenerate_scan_results(self) -> dict:
        """
        Regenerate scan-results.json from per-role storage.

        NOTE: This is now the preferred way to generate scan-results.json.
        The file is derived from role objects, not a primary write target.

        Returns:
            The regenerated scan results dictionary
        """
        # Import here to avoid circular dependency
        from storage import RoleStorage

        storage = RoleStorage(self.output_dir)
        return storage.generate_scan_results()


def main():
    """Test output module."""
    output_dir = Path(__file__).parent.parent / "output"
    output = ScanOutput(output_dir)

    print(f"[TEST] Output initialized, found {len(output.processed_urls)} existing URLs")
    print(f"[TEST] Output file: {output.output_file}")


if __name__ == "__main__":
    main()
