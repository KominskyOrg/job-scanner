"""
Post-evaluation validation and filtering for APPLY decisions.

This module runs after initial LLM evaluation to apply deterministic gates
that reduce noise and ensure APPLY decisions meet strict criteria.

Features:
- APPLY gates: Seniority, role type, concerns, ownership language
- APPLY cap: Max 20% of roles per scan can be APPLY
- Staffing firm detection: Downgrade staffing/contracting roles
"""

import json
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from evaluator import JobEvaluation


# Ownership language patterns (must appear in summary or key_requirements)
OWNERSHIP_PATTERNS = [
    r"\bowns?\b",
    r"\bdesigns?\b",
    r"\barchitects?\b",
    r"\bleads?\b",
    r"\bresponsible for\b",
    r"\bdrives?\b",
    r"\bdefines?\b",
]

# Valid seniority levels for APPLY
APPLY_SENIORITY_LEVELS = {"Senior", "Staff", "Principal"}

# Role type keywords (for classification and title fallback)
ROLE_TYPE_KEYWORDS = {"platform", "infrastructure", "sre"}

# Contract detection patterns
CONTRACT_SKIP_PATTERNS = [
    r"\bcontract-to-hire\b", r"\bc2h\b", r"\b1099\b", r"\bcorp-to-corp\b",
    r"\bw2 contract\b", r"\bfixed-term\b", r"\btemporary position\b",
    r"\bduration:\s*\d+\s*months?\b", r"\bstaff augmentation\b"
]

CONTRACT_CONSIDER_PATTERNS = [
    r"\bcontract\b", r"\bcontractor\b"
]

CONTRACT_ALLOW_PATTERNS = [
    r"\bfull-time\b", r"\bpermanent\b", r"\bdirect hire\b", r"\bsalaried\b",
    r"\bfte\b", r"\bfull time\b"
]

# Onsite/location detection patterns
ONSITE_SKIP_PATTERNS = [
    r"\brelocation required\b", r"\bmust relocate\b",
    r"\bonsite required\b", r"\bin-office required\b"
]

ONSITE_CONSIDER_PATTERNS = [
    r"\bonsite\b", r"\bin-office\b", r"\bon site\b", r"\bhybrid\b",
    r"\b[2-5] days?\s*(a week|per week|in office|onsite)\b",
    r"\bthree days\b", r"\bfour days\b", r"\bfive days\b",
    r"\bmust be located in\b", r"\bmust live in\b"
]

REMOTE_POSITIVE_PATTERNS = [
    r"\bfully remote\b", r"\bremote first\b", r"\b100% remote\b",
    r"\bwork from anywhere\b", r"\bremote-friendly\b"
]

# Role mismatch detection patterns
ROLE_NEGATIVE_PATTERNS = [
    r"\bproduct features\b", r"\bfeature delivery\b", r"\broadmap\b",
    r"\bcustomer features\b", r"\bui\b", r"\breact\b", r"\bmobile\b",
    r"\bfrontend\b", r"\bfront end\b", r"\bfull stack\b", r"\bfullstack\b",
    r"\bsales engineer\b", r"\bsolutions engineer\b", r"\bcustomer success\b"
]

ROLE_POSITIVE_PATTERNS = [
    r"\bkubernetes\b", r"\bterraform\b", r"\beks\b", r"\bsre\b",
    r"\bon-call\b", r"\bincident response\b", r"\bobservability\b",
    r"\bdatadog\b", r"\bplatform\b", r"\binfrastructure\b", r"\breliability\b",
    r"\bhelm\b", r"\bargocd\b", r"\bgitops\b"
]


def _check_ownership_language(text: str) -> bool:
    """Check if text contains ownership language patterns."""
    text_lower = text.lower()
    for pattern in OWNERSHIP_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def _check_role_type(role_classification: str, job_title: str) -> bool:
    """
    Check if role type is valid for APPLY.

    Either role_classification or job_title must contain Platform/Infrastructure/SRE.
    Job title check is a fallback for LLM classification variance.
    """
    classification_lower = role_classification.lower()
    title_lower = job_title.lower()

    for keyword in ROLE_TYPE_KEYWORDS:
        if keyword in classification_lower or keyword in title_lower:
            return True
    return False


def _downgrade_evaluation(evaluation: JobEvaluation, new_decision: str) -> JobEvaluation:
    """Create new evaluation with downgraded decision."""
    return JobEvaluation(
        role_fit_score=evaluation.role_fit_score,
        role_classification=evaluation.role_classification,
        seniority_level=evaluation.seniority_level,
        remote_status=evaluation.remote_status,
        risk_level=evaluation.risk_level,
        final_decision=new_decision,
        confidence_signal=evaluation.confidence_signal,
        key_requirements=evaluation.key_requirements,
        concerns=evaluation.concerns,
        summary=evaluation.summary,
        is_valid=evaluation.is_valid,
        error=evaluation.error,
    )


def check_contract_terms(job_description: str, evaluation: JobEvaluation) -> tuple[JobEvaluation, list[str]]:
    """
    Check for contract/temp work signals.
    Skip patterns -> SKIP
    Consider patterns without allow signals -> CONSIDER
    """
    if evaluation.final_decision == "SKIP":
        return evaluation, []

    desc_lower = job_description.lower()
    reasons = []

    # Check for allow signals first
    has_allow = any(re.search(p, desc_lower) for p in CONTRACT_ALLOW_PATTERNS)

    # High certainty skip patterns
    for pattern in CONTRACT_SKIP_PATTERNS:
        if re.search(pattern, desc_lower):
            reasons.append(f"contract:{pattern.strip(r'\\b')}")
            # Force SKIP even from APPLY
            if evaluation.final_decision == "APPLY":
                evaluation = _downgrade_evaluation(evaluation, "SKIP")
            return evaluation, reasons

    # Medium certainty patterns (only if no allow signals)
    if not has_allow:
        for pattern in CONTRACT_CONSIDER_PATTERNS:
            if re.search(pattern, desc_lower):
                reasons.append(f"contract:{pattern.strip(r'\\b')}")
                if evaluation.final_decision == "APPLY":
                    evaluation = _downgrade_evaluation(evaluation, "CONSIDER")
                return evaluation, reasons

    return evaluation, reasons


def check_onsite_terms(job_description: str, evaluation: JobEvaluation) -> tuple[JobEvaluation, list[str]]:
    """
    Check for onsite/hybrid requirements.
    Extract from JD first, use LLM remote_status as fallback.
    """
    if evaluation.final_decision == "SKIP":
        return evaluation, []

    desc_lower = job_description.lower()
    reasons = []

    # Check for strong remote signals first
    has_remote_positive = any(re.search(p, desc_lower) for p in REMOTE_POSITIVE_PATTERNS)
    if has_remote_positive:
        return evaluation, []

    # Skip patterns - force SKIP
    for pattern in ONSITE_SKIP_PATTERNS:
        if re.search(pattern, desc_lower):
            reasons.append(f"onsite:{pattern.strip(r'\\b')}")
            if evaluation.final_decision == "APPLY":
                evaluation = _downgrade_evaluation(evaluation, "SKIP")
            return evaluation, reasons

    # LLM says Onsite -> SKIP
    if evaluation.remote_status == "Onsite":
        reasons.append("onsite:llm_detected_onsite")
        if evaluation.final_decision == "APPLY":
            evaluation = _downgrade_evaluation(evaluation, "SKIP")
        return evaluation, reasons

    # Consider patterns -> CONSIDER
    for pattern in ONSITE_CONSIDER_PATTERNS:
        if re.search(pattern, desc_lower):
            reasons.append(f"onsite:{pattern.strip(r'\\b')}")
            if evaluation.final_decision == "APPLY":
                evaluation = _downgrade_evaluation(evaluation, "CONSIDER")
            return evaluation, reasons

    return evaluation, reasons


def check_role_mismatch(job_description: str, evaluation: JobEvaluation, job_title: str) -> tuple[JobEvaluation, list[str]]:
    """
    Check for role type mismatch.
    Strong positives override title-based concerns.
    Strong negatives without positives -> SKIP.
    """
    if evaluation.final_decision == "SKIP":
        return evaluation, []

    desc_lower = job_description.lower()
    reasons = []

    # Count positive and negative signals
    positive_count = sum(1 for p in ROLE_POSITIVE_PATTERNS if re.search(p, desc_lower))
    negative_matches = [p for p in ROLE_NEGATIVE_PATTERNS if re.search(p, desc_lower)]

    # Strong positives present -> don't skip based on title alone
    if positive_count >= 3:
        return evaluation, []

    # Check for role classification mismatch
    classification = evaluation.role_classification.lower()
    is_platform_role = any(k in classification for k in ["platform", "infrastructure", "sre", "reliability"])

    # Negative patterns without strong positives
    if negative_matches and not is_platform_role and positive_count < 2:
        reasons.extend([f"role_mismatch:{p.strip(r'\\b')}" for p in negative_matches[:2]])
        if evaluation.final_decision == "APPLY":
            evaluation = _downgrade_evaluation(evaluation, "SKIP")
        return evaluation, reasons

    return evaluation, reasons


def _check_existing_gates(
    evaluation: JobEvaluation,
    job_title: str
) -> tuple[JobEvaluation, list[str]]:
    """
    Check existing APPLY gates (seniority, role type, ownership).

    Gates:
    1. Seniority must be Senior, Staff, or Principal
    2. Role type must include Platform, Infrastructure, or SRE
    3. Concerns must not include "unclear ownership"
    4. Ownership language must appear in summary or key_requirements
    """
    if evaluation.final_decision != "APPLY":
        return evaluation, []

    gate_failures = []

    # Gate 1: Seniority level
    if evaluation.seniority_level not in APPLY_SENIORITY_LEVELS:
        gate_failures.append(f"seniority:{evaluation.seniority_level}")

    # Gate 2: Role type (classification or title fallback)
    if not _check_role_type(evaluation.role_classification, job_title):
        gate_failures.append(f"role_type:{evaluation.role_classification}")

    # Gate 3: No "unclear ownership" in concerns
    concerns_lower = [c.lower() for c in evaluation.concerns]
    if any("unclear ownership" in c for c in concerns_lower):
        gate_failures.append("concern:unclear_ownership")

    # Gate 4: Ownership language required
    combined_text = evaluation.summary + " " + " ".join(evaluation.key_requirements)
    if not _check_ownership_language(combined_text):
        gate_failures.append("missing_ownership_language")

    # Downgrade if any gates failed
    if gate_failures:
        evaluation = _downgrade_evaluation(evaluation, "CONSIDER")

    return evaluation, gate_failures


def validate_apply_gates(
    evaluation: JobEvaluation,
    job_title: str,
    job_description: str = "",
    company_name: str = "",
    staffing_config: dict = None
) -> tuple[JobEvaluation, dict]:
    """
    Validate evaluation against all deterministic gates.
    Returns (evaluation, gate_results) where gate_results contains all reasons.

    Args:
        evaluation: The LLM evaluation result
        job_title: The job title (for role type fallback)
        job_description: Full job description text (for pattern matching)
        company_name: Company name (for staffing detection)
        staffing_config: staffing_detection config from pipeline.json

    Returns:
        Tuple of (possibly modified evaluation, dict of gate results)
    """
    pre_gates_decision = evaluation.final_decision
    all_reasons = {}

    # Existing gates (seniority, role type, ownership)
    evaluation, gate_reasons = _check_existing_gates(evaluation, job_title)
    if gate_reasons:
        all_reasons["gate_failures"] = gate_reasons

    # Staffing firm check (if config provided)
    if staffing_config and job_description:
        evaluation, staffing_reasons = check_staffing_firm_risk(
            evaluation, company_name, job_description, staffing_config
        )
        if staffing_reasons:
            all_reasons["staffing_reasons"] = staffing_reasons

    # New gates (only if job_description provided)
    if job_description:
        evaluation, contract_reasons = check_contract_terms(job_description, evaluation)
        if contract_reasons:
            all_reasons["contract_reasons"] = contract_reasons

        evaluation, onsite_reasons = check_onsite_terms(job_description, evaluation)
        if onsite_reasons:
            all_reasons["onsite_reasons"] = onsite_reasons

        evaluation, role_reasons = check_role_mismatch(job_description, evaluation, job_title)
        if role_reasons:
            all_reasons["role_mismatch_reasons"] = role_reasons

    # Build combined reasons list with prefixes
    combined_reasons = []
    for key, reasons in all_reasons.items():
        if key not in ("pre_gates_final_decision", "post_gates_final_decision", "post_gates_reasons"):
            combined_reasons.extend(reasons)

    all_reasons["post_gates_reasons"] = combined_reasons
    all_reasons["pre_gates_final_decision"] = pre_gates_decision
    all_reasons["post_gates_final_decision"] = evaluation.final_decision

    return evaluation, all_reasons


def check_staffing_firm_risk(
    evaluation: JobEvaluation,
    company_name: str,
    job_description: str,
    config: dict
) -> tuple[JobEvaluation, list[str]]:
    """
    Check if job is from a staffing/recruiting firm.

    If detected, sets risk_level to "high" and downgrades APPLY to CONSIDER.

    Args:
        evaluation: The LLM evaluation result
        company_name: The company name
        job_description: The full job description text
        config: staffing_detection config from pipeline.json

    Returns:
        Tuple of (possibly modified evaluation, list of detection reasons)
    """
    detection_reasons = []

    company_indicators = config.get("company_indicators", [])
    description_phrases = config.get("description_phrases", [])

    company_lower = company_name.lower()
    description_lower = job_description.lower()

    # Check company name
    for indicator in company_indicators:
        if indicator.lower() in company_lower:
            detection_reasons.append(f"company:{indicator}")
            break  # One match is enough

    # Check description phrases
    for phrase in description_phrases:
        if phrase.lower() in description_lower:
            detection_reasons.append(f"description:{phrase}")
            break  # One match is enough

    # Apply changes if detected
    if detection_reasons:
        new_decision = evaluation.final_decision
        if evaluation.final_decision == "APPLY":
            new_decision = "CONSIDER"

        evaluation = JobEvaluation(
            role_fit_score=evaluation.role_fit_score,
            role_classification=evaluation.role_classification,
            seniority_level=evaluation.seniority_level,
            remote_status=evaluation.remote_status,
            risk_level="high",  # Always set high for staffing
            final_decision=new_decision,
            confidence_signal=evaluation.confidence_signal,
            key_requirements=evaluation.key_requirements,
            concerns=evaluation.concerns,
            summary=evaluation.summary,
            is_valid=evaluation.is_valid,
            error=evaluation.error,
        )

    return evaluation, detection_reasons


@dataclass
class CappedResult:
    """Result of applying APPLY cap to evaluations."""
    role_id: str
    evaluation: JobEvaluation
    was_capped: bool


def apply_scan_cap(
    evaluations: list[tuple[str, JobEvaluation]],
    cap_percentage: float = 0.20
) -> list[CappedResult]:
    """
    Apply APPLY cap to a batch of evaluations.

    Maximum 20% of roles can be APPLY. If over cap, sort by:
    1. confidence_signal (HIGH first)
    2. role_fit_score (descending)

    Keep top N as APPLY, downgrade rest to CONSIDER.

    Args:
        evaluations: List of (role_id, evaluation) tuples
        cap_percentage: Maximum percentage of APPLY (default 0.20)

    Returns:
        List of CappedResult with was_capped flag
    """
    if not evaluations:
        return []

    # Separate APPLY from non-APPLY
    apply_roles = []
    non_apply_roles = []

    for role_id, evaluation in evaluations:
        if evaluation.final_decision == "APPLY":
            apply_roles.append((role_id, evaluation))
        else:
            non_apply_roles.append((role_id, evaluation))

    # Calculate max APPLY allowed
    total = len(evaluations)
    max_apply = max(1, int(total * cap_percentage))

    results = []

    # If under cap, no changes needed
    if len(apply_roles) <= max_apply:
        for role_id, evaluation in apply_roles:
            results.append(CappedResult(role_id, evaluation, was_capped=False))
        for role_id, evaluation in non_apply_roles:
            results.append(CappedResult(role_id, evaluation, was_capped=False))
        return results

    # Sort APPLY roles by priority
    confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    def sort_key(item):
        _, evaluation = item
        conf = confidence_order.get(evaluation.confidence_signal.upper(), 2)
        return (conf, -evaluation.role_fit_score)

    sorted_apply = sorted(apply_roles, key=sort_key)

    # Keep top N as APPLY
    keep_apply = sorted_apply[:max_apply]
    downgrade_apply = sorted_apply[max_apply:]

    # Add kept APPLY roles
    for role_id, evaluation in keep_apply:
        results.append(CappedResult(role_id, evaluation, was_capped=False))

    # Add downgraded roles
    for role_id, evaluation in downgrade_apply:
        downgraded = JobEvaluation(
            role_fit_score=evaluation.role_fit_score,
            role_classification=evaluation.role_classification,
            seniority_level=evaluation.seniority_level,
            remote_status=evaluation.remote_status,
            risk_level=evaluation.risk_level,
            final_decision="CONSIDER",  # Downgraded
            confidence_signal=evaluation.confidence_signal,
            key_requirements=evaluation.key_requirements,
            concerns=evaluation.concerns,
            summary=evaluation.summary,
            is_valid=evaluation.is_valid,
            error=evaluation.error,
        )
        results.append(CappedResult(role_id, downgraded, was_capped=True))

    # Add non-APPLY roles unchanged
    for role_id, evaluation in non_apply_roles:
        results.append(CappedResult(role_id, evaluation, was_capped=False))

    return results


def load_staffing_config(config_path: Optional[Path] = None) -> dict:
    """
    Load staffing detection config from pipeline.json.

    Args:
        config_path: Path to pipeline.json (defaults to config/pipeline.json)

    Returns:
        staffing_detection config dict, or empty dict if not found
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "pipeline.json"

    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("staffing_detection", {})
    except Exception:
        return {}
