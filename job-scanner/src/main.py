#!/usr/bin/env python3
"""
Job Scanner CLI - LinkedIn job screening automation.

Stage 1: Scrape and evaluate jobs (--scrape)
Stage 2: Generate cover letters for APPLY roles (--write)

Usage:
    python3 main.py --login     # Open browser for manual login
    python3 main.py --scrape    # Stage 1: Scrape and evaluate
    python3 main.py --write     # Stage 2: Generate writing for APPLY roles
    python3 main.py --test      # Test with single job
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from scraper import LinkedInScraper, JobData
from evaluator import JobEvaluator
from output import ScanOutput
from writer import JobWriter
from storage import RoleStorage, check_and_run_migration
from models import (
    JobPosting, Evaluation, ApplicationPlan, PipelineState,
    extract_linkedin_job_id, generate_canonical_id, generate_role_id,
    generate_display_name, get_default_next_action
)
from schema_validator import validate_stage1_evaluation
from config import get_output_dir, get_threshold, ConfigError
from post_evaluator import (
    validate_apply_gates,
    check_staffing_firm_risk,
    apply_scan_cap,
    load_staffing_config,
    CappedResult,
)

# DEPRECATION: Legacy dual-write for backward compatibility
# Remove after 2-week evaluation period (by 2026-02-03)
LEGACY_DUAL_WRITE = True


def _log_stage2_decision(role_id: str, display_name: str, decision: str, reason: str):
    """Log a one-line Stage 2 gating decision for traceability."""
    print(f"[STAGE2] {role_id[:8]} {display_name}: {decision} ({reason})")


class JobScanner:
    """Main job scanning orchestrator."""

    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.config_dir = self.base_dir / "config"

        # Get output dir from config or fallback
        try:
            self.output_dir = get_output_dir()
        except ConfigError:
            self.output_dir = self.base_dir / "output"

        self.output_dir.mkdir(exist_ok=True)

        # Generate run ID for this session (correlation ID for tracing)
        self.run_id = str(uuid.uuid4())
        print(f"[SCANNER] Run ID: {self.run_id}")

        # Check for and run migration if needed
        check_and_run_migration(self.output_dir)

        # Initialize storage manager
        self.storage = RoleStorage(self.output_dir)

        # Initialize Stage 1 components
        self.scraper = LinkedInScraper(self.config_dir / "searches.json")
        self.evaluator = JobEvaluator(
            prompt_path=self.config_dir / "prompt.txt",
            profile_path=self.config_dir / "profile_screening.txt"
        )
        self.output = ScanOutput(self.output_dir)

        # Initialize Stage 2 writer
        self.writer = JobWriter(self.config_dir / "profile.txt")

        # Store job descriptions for Stage 2 (legacy compatibility)
        self.job_descriptions: dict[str, str] = {}

    async def run_login_mode(self):
        """Open browser for manual LinkedIn login."""
        print("[SCANNER] Starting login mode...")

        await self.scraper.start_browser(headless=False)

        try:
            logged_in = await self.scraper.check_login_status()

            if not logged_in:
                await self.scraper.wait_for_manual_login()

            print("[SCANNER] Session saved to browser_profile/")
            print("[SCANNER] You can now run --scrape to collect jobs.")

            print("\nPress Enter to close browser...")
            await asyncio.get_event_loop().run_in_executor(None, input)

        except Exception as e:
            print(f"[SCANNER] Error: {e}")
            print("[SCANNER] Browser profile may still be saved. Try --scrape.")

        finally:
            await self.scraper.close_browser()

    async def run_scrape(self):
        """Stage 1: Scrape jobs and evaluate with OpenAI."""
        print("[SCANNER] Starting Stage 1: Scrape and Evaluate...")

        # Load config
        config_path = self.config_dir / "searches.json"
        with open(config_path) as f:
            config = json.load(f)

        searches = config.get("searches", [])
        if not searches:
            print("[SCANNER] No searches configured in searches.json")
            return

        await self.scraper.start_browser(headless=False)

        # Create ONE scan result for entire run
        run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        scan_result = self.output.create_scan_result(f"scrape-{run_timestamp}")

        try:
            if not await self.scraper.check_login_status():
                await self.scraper.wait_for_manual_login()

            for search in searches:
                search_name = search.get("name", "unnamed")
                search_url = search.get("url", "")

                if not search_url or "PASTE" in search_url:
                    print(f"[SCANNER] Skipping '{search_name}' - no valid URL")
                    continue

                try:
                    await self._process_search(search_name, search_url, scan_result)
                except Exception as e:
                    print(f"[SCANNER] Search '{search_name}' failed: {e}, moving on...")

        finally:
            await self.scraper.close_browser()

        # Save combined scan at end
        if LEGACY_DUAL_WRITE and scan_result["stats"]["total_scraped"] > 0:
            self.output.save_scan(scan_result)
            self._save_descriptions()

            # Regenerate scan-results.json from storage (derived output)
            self.storage.save_scan_results()

        print("[SCANNER] Stage 1 complete!")

    def _is_canonical_id_stored(self, canonical_id: str) -> bool:
        """Check if a canonical_id is already in storage."""
        role_id, _ = generate_role_id(canonical_id)
        return self.storage.role_exists(role_id)

    async def _process_search(self, search_name: str, search_url: str, scan_result: dict):
        """Process a single search and add jobs to scan_result."""
        print(f"\n[SCANNER] Processing search: {search_name}")

        # Deprecated broad searches (tracked for noise analysis)
        BROAD_SEARCHES = {"software-engineer-infrastructure", "devops-engineer-remote"}

        # Load staffing detection config
        staffing_config = load_staffing_config()

        # Scrape jobs (individual job timeouts handled in scraper)
        jobs = await self.scraper.scrape_search(
            search_name,
            search_url,
            is_stored_callback=self._is_canonical_id_stored
        )

        if not jobs:
            print(f"[SCANNER] No new jobs found for '{search_name}'")
            return

        print(f"[SCANNER] Found {len(jobs)} jobs to evaluate")

        # Collect pending jobs for batch processing (APPLY cap)
        pending_jobs = []

        # Evaluate each job (Stage 1) - no timeout, always complete evaluation
        for i, job in enumerate(jobs):
            print(f"\n[SCANNER] Evaluating job {i+1}/{len(jobs)}: {job.title}")

            try:
                # Skip jobs that failed extraction
                if getattr(job, 'extraction_failed', False):
                    print(f"[SCANNER] Skipping job with failed extraction: {job.title}")
                    continue

                # Extract job ID and generate role identifiers
                job_id = extract_linkedin_job_id(job.job_url)
                if not job_id:
                    print(f"[SCANNER] Could not extract job ID from URL, skipping: {job.job_url}")
                    continue

                canonical_id = generate_canonical_id("linkedin_scrape", job_id)
                role_id, role_id_full = generate_role_id(canonical_id)
                display_name = generate_display_name(job.company, job.title)

                # Evaluate the job
                evaluation, eval_metadata = self.evaluator.evaluate_job(job.description)

                # Track search source for noise analysis
                if search_name in BROAD_SEARCHES:
                    eval_metadata["decision_path"].append("search_noise")

                # Consolidated gate validation (staffing, contract, onsite, role mismatch)
                evaluation, gate_results = validate_apply_gates(
                    evaluation,
                    job.title,
                    job.description,
                    job.company,
                    staffing_config
                )
                eval_metadata.update(gate_results)
                if gate_results.get("post_gates_reasons"):
                    eval_metadata["decision_path"].append("gate_downgrade")

                # Create JobPosting for later saving
                posting = JobPosting(
                    schema_version="1.0",
                    role_id=role_id,
                    role_id_full=role_id_full,
                    canonical_id=canonical_id,
                    display_name=display_name,
                    source="linkedin_scrape",
                    run_id=self.run_id,
                    extracted_fields={
                        "company": job.company,
                        "title": job.title,
                        "location": job.location,
                        "job_url": job.job_url,
                        "description": job.description,
                        "posted_age": job.posted_age,
                    },
                    scrape_diagnostics={
                        "scraped_at": datetime.now().isoformat(),
                        "extraction_method": f"job_id_from_url:{job_id}",
                        "description_length": len(job.description),
                        "missing_fields": [],
                    },
                )

                # Collect for batch processing (Feature B: APPLY cap)
                pending_jobs.append({
                    "role_id": role_id,
                    "evaluation": evaluation,
                    "eval_metadata": eval_metadata,
                    "posting": posting,
                    "job": job,
                    "display_name": display_name,
                })

            except Exception as e:
                print(f"[SCANNER] Failed to evaluate {job.title}: {e}")

        # Feature B: Apply APPLY cap across the batch
        if pending_jobs:
            evaluations_for_cap = [
                (p["role_id"], p["evaluation"]) for p in pending_jobs
            ]
            capped_results = apply_scan_cap(evaluations_for_cap)

            # Build lookup for capped results
            capped_lookup = {r.role_id: r for r in capped_results}

            # Save all jobs with final (possibly capped) evaluations
            for pending in pending_jobs:
                role_id = pending["role_id"]
                capped = capped_lookup.get(role_id)

                if capped and capped.was_capped:
                    # Update evaluation and metadata
                    pending["evaluation"] = capped.evaluation
                    pending["eval_metadata"]["decision_path"].append("capped_apply")

                evaluation = pending["evaluation"]
                eval_metadata = pending["eval_metadata"]
                posting = pending["posting"]
                job = pending["job"]
                display_name = pending["display_name"]

                # Save JobPosting
                self.storage.save_job_posting(posting)

                # Create and save Evaluation
                eval_obj = Evaluation(
                    schema_version="1.0",
                    role_id=role_id,
                    run_id=self.run_id,
                    role_fit_score=evaluation.role_fit_score,
                    role_classification=evaluation.role_classification,
                    seniority_level=evaluation.seniority_level,
                    remote_status=evaluation.remote_status,
                    risk_level=evaluation.risk_level,
                    final_decision=evaluation.final_decision,
                    confidence_signal=evaluation.confidence_signal.upper(),
                    key_requirements=evaluation.key_requirements,
                    concerns=evaluation.concerns,
                    summary=evaluation.summary,
                    is_valid=evaluation.is_valid,
                    error=evaluation.error,
                    evaluated_at=datetime.now().isoformat(),
                    evaluator_metadata=eval_metadata,
                )
                self.storage.save_evaluation(eval_obj)

                # Create and save PipelineState with default next_action
                state = PipelineState(
                    schema_version="1.0",
                    role_id=role_id,
                    status="pending",
                    next_action=get_default_next_action(evaluation.final_decision),
                )
                self.storage.save_pipeline_state(state)

                # Also add to legacy scan_result for backward compatibility
                if LEGACY_DUAL_WRITE:
                    self.output.add_job_result(scan_result, job, evaluation, self.evaluator, eval_metadata)

                    # Store description for potential Stage 2 use (legacy)
                    self.job_descriptions[job.job_url] = job.description

                print(f"[SCANNER] Saved role {role_id}: {display_name} -> {evaluation.final_decision}")

            # Save incrementally for crash recovery (legacy) - once after batch
            if LEGACY_DUAL_WRITE:
                self.output.save_incremental(scan_result)

    def _save_descriptions(self):
        """Save job descriptions for Stage 2 use."""
        desc_file = self.output_dir / "job-descriptions.json"
        existing = {}

        if desc_file.exists():
            try:
                with open(desc_file) as f:
                    existing = json.load(f)
            except Exception:
                pass

        existing.update(self.job_descriptions)

        with open(desc_file, "w") as f:
            json.dump(existing, f, indent=2)

    def _load_descriptions(self) -> dict[str, str]:
        """Load saved job descriptions."""
        desc_file = self.output_dir / "job-descriptions.json"
        if desc_file.exists():
            with open(desc_file) as f:
                return json.load(f)
        return {}

    def run_write(self, max_roles: int = 3):
        """Stage 2: Generate cover letters and recruiter messages for APPLY roles."""
        print("[SCANNER] Starting Stage 2: Generate Writing...")

        # Create run timestamp for organizing output folder
        run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")

        # Get eligible roles from storage (new flow)
        eligible_roles = self._get_eligible_roles_from_storage()

        if not eligible_roles:
            # Fallback to legacy scan results
            print("[SCANNER] No roles found in storage, trying legacy scan-results.json...")
            eligible_roles = self._get_eligible_roles_legacy()

        if not eligible_roles:
            print("[SCANNER] No eligible roles found. Run --scrape first.")
            return

        # Rank roles by priority
        ranked_roles = self._rank_roles(eligible_roles)

        print(f"[SCANNER] Found {len(ranked_roles)} eligible roles (max {max_roles} will be processed)")

        # Load job descriptions (for legacy compatibility)
        descriptions = self._load_descriptions()

        # Process roles up to cap
        processed = 0
        skipped_validation = 0

        for i, job in enumerate(ranked_roles):
            if processed >= max_roles:
                print(f"\n[SCANNER] Reached cap of {max_roles} roles. Stopping.")
                break

            title = job.get("title", "Unknown")
            company = job.get("company", "Unknown")
            job_url = job.get("job_url", "")
            role_id = job.get("role_id", "")
            rank = job.get("apply_rank", i + 1)

            print(f"\n[SCANNER] Processing rank {rank}: {title} at {company}")

            # Skip if already has Stage 2 output
            display_name = job.get("display_name", f"{company}-{title}")
            if role_id:
                existing_plan = self.storage.load_application_plan(role_id)
                if existing_plan and (existing_plan.cover_letter_text or existing_plan.recruiter_message):
                    _log_stage2_decision(role_id, display_name, "SKIPPED", "already_has_output")
                    continue
            elif job.get("stage2"):
                _log_stage2_decision(role_id or "unknown", display_name, "SKIPPED", "already_has_output")
                continue

            # Validate Stage 1 evaluation before Stage 2
            evaluation = job.get("evaluation", {})
            is_valid, errors = validate_stage1_evaluation(evaluation)
            if not is_valid:
                _log_stage2_decision(role_id or "unknown", display_name, "SKIPPED", f"validation_failed: {errors}")
                skipped_validation += 1
                continue

            # Get job description
            description = ""
            if role_id:
                posting = self.storage.load_job_posting(role_id)
                if posting:
                    description = posting.extracted_fields.get("description", "")

            if not description:
                description = descriptions.get(job_url, "")

            if not description:
                print(f"[SCANNER] Skipping - no job description found")
                continue

            # Run Stage 2 writer
            force_write = job.get("force_write", False)
            writer_output = self.writer.generate(description, evaluation, job, force_write)

            # Log skip reason if applicable
            if writer_output.skip_reason:
                _log_stage2_decision(role_id or "unknown", display_name, "SKIPPED", writer_output.skip_reason)
                continue

            # Save ApplicationPlan to new storage
            if role_id:
                plan = ApplicationPlan(
                    schema_version="1.0",
                    role_id=role_id,
                    cover_letter_text=writer_output.cover_letter,
                    recruiter_message=writer_output.recruiter_message,
                    generated_at=datetime.now().isoformat(),
                )
                self.storage.save_application_plan(plan)

            # Log success with details about what was generated
            outputs = []
            if writer_output.cover_letter:
                outputs.append("cover_letter")
            if writer_output.recruiter_message:
                outputs.append("recruiter_message")
            _log_stage2_decision(role_id or "unknown", display_name, "GENERATED", "+".join(outputs) if outputs else "none")

            # Also save to legacy format for backward compatibility
            if LEGACY_DUAL_WRITE:
                self._save_legacy_stage2(job, job_url, description, writer_output, run_timestamp)

            processed += 1

        print(f"\n[SCANNER] Stage 2 complete! Processed {processed} roles.")
        if skipped_validation > 0:
            print(f"[SCANNER] Skipped {skipped_validation} roles due to validation errors.")
        if processed > 0:
            print(f"[SCANNER] Check output/apply/{run_timestamp}/ for individual role files")

    def _get_eligible_roles_from_storage(self) -> list[dict]:
        """
        Get roles eligible for Stage 2 writing from new storage format.
        Includes: APPLY roles + CONSIDER roles with force_write=true (future)
        """
        eligible = []
        role_ids = self.storage.list_roles()

        for role_id in role_ids:
            evaluation = self.storage.load_evaluation(role_id)
            if not evaluation:
                continue

            if not evaluation.is_valid:
                continue

            # APPLY roles are always eligible
            if evaluation.final_decision == "APPLY":
                posting = self.storage.load_job_posting(role_id)
                if posting:
                    eligible.append({
                        "role_id": role_id,
                        "company": posting.extracted_fields.get("company", ""),
                        "title": posting.extracted_fields.get("title", ""),
                        "location": posting.extracted_fields.get("location", ""),
                        "job_url": posting.extracted_fields.get("job_url", ""),
                        "posted_age": posting.extracted_fields.get("posted_age"),
                        "display_name": posting.display_name,
                        "evaluation": evaluation.to_dict(),
                    })

        return eligible

    def _get_eligible_roles_legacy(self) -> list[dict]:
        """Legacy method to get eligible roles from scan-results.json."""
        results_file = self.output_dir / "scan-results.json"
        if not results_file.exists():
            return []

        with open(results_file) as f:
            all_results = json.load(f)

        if not all_results.get("scans"):
            return []

        latest_scan = all_results["scans"][-1]
        return self._get_eligible_roles(latest_scan)

    def _save_legacy_stage2(self, job: dict, job_url: str, description: str,
                           writer_output, run_timestamp: str):
        """Save Stage 2 output to legacy format for backward compatibility."""
        # Build complete job_entry for save_apply_role
        # This ensures we always save to the apply folder, regardless of URL matching
        job_entry = {
            "company": job.get("company", "Unknown"),
            "title": job.get("title", "Unknown"),
            "location": job.get("location", ""),
            "job_url": job_url,
            "posted_age": job.get("posted_age"),
            "evaluation": job.get("evaluation", {}),
            "stage2": {
                "cover_letter": writer_output.cover_letter,
                "recruiter_message": writer_output.recruiter_message,
                "generated_at": datetime.now().isoformat(),
            }
        }

        # Always save per-role file to apply folder
        self.output.save_apply_role(job_entry, description, run_timestamp)

        # Try to update scan-results.json (best-effort, URL matching may fail)
        results_file = self.output_dir / "scan-results.json"
        if results_file.exists():
            try:
                with open(results_file) as f:
                    all_results = json.load(f)

                if all_results.get("scans"):
                    latest_scan = all_results["scans"][-1]
                    self.output.add_stage2_output(
                        latest_scan,
                        job_url,
                        writer_output.cover_letter,
                        writer_output.recruiter_message
                    )

                    with open(results_file, "w") as f:
                        json.dump(all_results, f, indent=2)
            except Exception as e:
                print(f"[SCANNER] Warning: Could not update scan-results.json: {e}")

    def _get_eligible_roles(self, scan_result: dict) -> list[dict]:
        """
        Get roles eligible for Stage 2 writing.
        Includes: APPLY roles + CONSIDER roles with force_write=true
        """
        eligible = []
        for job in scan_result.get("jobs", []):
            eval_data = job.get("evaluation", {})
            decision = eval_data.get("final_decision", "")

            # APPLY roles are always eligible
            if decision == "APPLY":
                eligible.append(job)
            # CONSIDER roles with force_write override
            elif decision == "CONSIDER" and job.get("force_write"):
                print(f"[SCANNER] Including CONSIDER with force_write: {job.get('title')}")
                eligible.append(job)

        return eligible

    def _rank_roles(self, roles: list[dict]) -> list[dict]:
        """
        Rank eligible roles by priority.
        Sort by: role_fit_score desc, risk_level (low first), confidence (high first)
        """
        risk_order = {"low": 0, "medium": 1, "high": 2}
        confidence_order = {"high": 0, "medium": 1, "low": 2}

        def sort_key(job):
            eval_data = job.get("evaluation", {})
            score = eval_data.get("role_fit_score", 0)
            risk = risk_order.get(eval_data.get("risk_level", "high"), 2)
            confidence = confidence_order.get(eval_data.get("confidence_signal", "low"), 2)
            return (-score, risk, confidence)

        ranked = sorted(roles, key=sort_key)

        # Assign rank numbers
        for i, job in enumerate(ranked):
            job["apply_rank"] = i + 1

        return ranked

    async def run_test_mode(self):
        """Test scraping and evaluation with single job."""
        print("[SCANNER] Starting test mode...")

        await self.scraper.start_browser(headless=False)

        try:
            if not await self.scraper.check_login_status():
                await self.scraper.wait_for_manual_login()

            config_path = self.config_dir / "searches.json"
            with open(config_path) as f:
                config = json.load(f)

            searches = config.get("searches", [])
            if not searches:
                print("[SCANNER] No searches configured")
                return

            search = searches[0]
            search_url = search.get("url", "")

            if not search_url or "PASTE" in search_url:
                print("[SCANNER] No valid search URL configured")
                return

            if await self.scraper.navigate_to_search(search_url):
                job = await self.scraper.extract_job_data(0)

                if job:
                    print(f"\n{'='*50}")
                    print(f"Company: {job.company}")
                    print(f"Title: {job.title}")
                    print(f"Location: {job.location}")
                    print(f"URL: {job.job_url}")
                    print(f"{'='*50}")
                    print(f"Description preview: {job.description[:300]}...")
                    print(f"{'='*50}")

                    # Stage 1: Evaluate
                    print("\n[SCANNER] Stage 1: Evaluating...")
                    evaluation, eval_metadata = self.evaluator.evaluate_job(job.description)
                    eval_dict = self.evaluator.to_dict(evaluation)

                    print("\n[SCANNER] Evaluation result:")
                    print(json.dumps(eval_dict, indent=2))

                    print("\n[SCANNER] Evaluation metadata:")
                    print(json.dumps(eval_metadata, indent=2))

                    # Stage 2: Write (if APPLY)
                    if evaluation.final_decision == "APPLY":
                        print("\n[SCANNER] Stage 2: Generating writing...")
                        job_metadata = {
                            "company": job.company,
                            "title": job.title,
                            "location": job.location,
                        }
                        writer_output = self.writer.generate(
                            job.description, eval_dict, job_metadata
                        )

                        if writer_output.cover_letter:
                            print("\n[SCANNER] Cover Letter:")
                            print("-" * 50)
                            print(writer_output.cover_letter)

                        if writer_output.recruiter_message:
                            print("\n[SCANNER] Recruiter Message:")
                            print("-" * 50)
                            print(writer_output.recruiter_message)

        finally:
            await self.scraper.close_browser()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="LinkedIn Job Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workflow:
  1. python3 main.py --login     # Login to LinkedIn (once)
  2. python3 main.py --scrape    # Stage 1: Scrape and evaluate
  3. python3 main.py --write     # Stage 2: Generate writing for APPLY roles
  4. Check output/apply/ for individual role files
        """
    )

    parser.add_argument("--login", action="store_true",
                        help="Open browser for manual LinkedIn login")
    parser.add_argument("--scrape", action="store_true",
                        help="Stage 1: Scrape jobs and evaluate")
    parser.add_argument("--write", action="store_true",
                        help="Stage 2: Generate writing for APPLY roles")
    parser.add_argument("--max", type=int, default=3,
                        help="Max roles to process in Stage 2 (default: 3)")
    parser.add_argument("--test", action="store_true",
                        help="Test with single job (both stages)")

    args = parser.parse_args()

    scanner = JobScanner()

    if args.login:
        asyncio.run(scanner.run_login_mode())
    elif args.scrape:
        asyncio.run(scanner.run_scrape())
    elif args.write:
        scanner.run_write(max_roles=args.max)
    elif args.test:
        asyncio.run(scanner.run_test_mode())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
