"""
Schema Validator for job scanner pipeline.
Validates data objects against config-defined schemas.

Week 1: Basic validation without jsonschema library.
Week 2: Add jsonschema library for formal validation.
"""

from typing import Any

from config import get_enum_values, get_threshold, ConfigError


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


def validate_stage1_evaluation(data: dict) -> tuple[bool, list[str]]:
    """
    Validate a Stage 1 evaluation result before passing to Stage 2.

    Args:
        data: The evaluation dictionary to validate

    Returns:
        Tuple of (is_valid, error_list)
        - is_valid: True if validation passed
        - error_list: List of validation error messages
    """
    errors = []

    # Required fields for Stage 1 evaluation
    required_fields = [
        "role_fit_score",
        "role_classification",
        "seniority_level",
        "remote_status",
        "risk_level",
        "final_decision",
        "confidence_signal",
        "key_requirements",
        "concerns",
        "summary",
    ]

    # Check required fields
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if errors:
        return False, errors

    # Validate score range
    score = data.get("role_fit_score")
    if not isinstance(score, int) or score < 1 or score > 10:
        errors.append(f"Invalid role_fit_score: {score} (must be integer 1-10)")

    # Validate enum fields against config
    enum_validations = [
        ("final_decision", data.get("final_decision", "").upper()),
        ("confidence_signal", data.get("confidence_signal", "").upper()),
        ("risk_level", data.get("risk_level", "").lower()),
        ("seniority_level", data.get("seniority_level", "")),
        ("remote_status", data.get("remote_status", "")),
    ]

    for enum_name, value in enum_validations:
        try:
            allowed = get_enum_values(enum_name)
            if value not in allowed:
                errors.append(f"Invalid {enum_name}: '{value}' (allowed: {allowed})")
        except ConfigError as e:
            errors.append(f"Config error validating {enum_name}: {e}")

    # Validate arrays
    key_reqs = data.get("key_requirements")
    if not isinstance(key_reqs, list):
        errors.append("key_requirements must be an array")
    elif len(key_reqs) < 1:
        errors.append("key_requirements must have at least 1 item")

    concerns = data.get("concerns")
    if not isinstance(concerns, list):
        errors.append("concerns must be an array")
    elif len(concerns) < 1:
        errors.append("concerns must have at least 1 item")

    # Validate summary is non-empty string
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary must be a non-empty string")

    # Validate role_classification is non-empty string
    classification = data.get("role_classification")
    if not isinstance(classification, str) or not classification.strip():
        errors.append("role_classification must be a non-empty string")

    return len(errors) == 0, errors


def validate_object(obj_type: str, data: dict) -> tuple[bool, list[str]]:
    """
    General schema validation for different object types.

    Args:
        obj_type: Type of object ('job_posting', 'evaluation', 'application_plan', 'pipeline_state')
        data: The data dictionary to validate

    Returns:
        Tuple of (is_valid, error_list)
    """
    validators = {
        "stage1_evaluation": validate_stage1_evaluation,
        "evaluation": validate_stage1_evaluation,  # Alias
        "job_posting": _validate_job_posting,
        "application_plan": _validate_application_plan,
        "pipeline_state": _validate_pipeline_state,
    }

    validator = validators.get(obj_type)
    if not validator:
        return False, [f"Unknown object type: {obj_type}"]

    return validator(data)


def _validate_job_posting(data: dict) -> tuple[bool, list[str]]:
    """Validate a JobPosting object."""
    errors = []

    required_fields = [
        "schema_version",
        "role_id",
        "canonical_id",
        "source",
        "extracted_fields",
    ]

    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if errors:
        return False, errors

    # Validate source enum
    source = data.get("source", "")
    try:
        allowed_sources = get_enum_values("source")
        if source not in allowed_sources:
            errors.append(f"Invalid source: '{source}' (allowed: {allowed_sources})")
    except ConfigError:
        pass  # Don't fail if config unavailable

    # Validate extracted_fields has required sub-fields
    extracted = data.get("extracted_fields", {})
    required_extracted = ["company", "title", "job_url"]
    for field in required_extracted:
        if field not in extracted or not extracted[field]:
            errors.append(f"Missing or empty extracted_fields.{field}")

    return len(errors) == 0, errors


def _validate_application_plan(data: dict) -> tuple[bool, list[str]]:
    """Validate an ApplicationPlan object."""
    errors = []

    required_fields = ["schema_version", "role_id"]

    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # At least one of cover_letter or recruiter_message should exist
    has_cover = data.get("cover_letter_text") is not None
    has_recruiter = data.get("recruiter_message") is not None

    if not has_cover and not has_recruiter:
        errors.append("ApplicationPlan must have cover_letter_text or recruiter_message")

    return len(errors) == 0, errors


def _validate_pipeline_state(data: dict) -> tuple[bool, list[str]]:
    """Validate a PipelineState object."""
    errors = []
    warnings = []

    required_fields = ["schema_version", "role_id", "status"]

    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if errors:
        return False, errors

    # Validate status enum
    status = data.get("status", "")
    try:
        allowed_statuses = get_enum_values("pipeline_status")
        if status not in allowed_statuses:
            errors.append(f"Invalid status: '{status}' (allowed: {allowed_statuses})")
    except ConfigError:
        pass

    # Validate outreach_status enum
    outreach = data.get("outreach_status", "not_started")
    try:
        allowed_outreach = get_enum_values("outreach_status")
        if outreach not in allowed_outreach:
            errors.append(f"Invalid outreach_status: '{outreach}'")
    except ConfigError:
        pass

    # Validate referral_status enum
    referral = data.get("referral_status", "not_started")
    try:
        allowed_referral = get_enum_values("referral_status")
        if referral not in allowed_referral:
            errors.append(f"Invalid referral_status: '{referral}'")
    except ConfigError:
        pass

    # State machine validation (warn, don't fail in Week 1)
    # Rule: If status == "applied" → application_date must exist
    if data.get("status") == "applied" and not data.get("application_date"):
        warnings.append("status is 'applied' but application_date is missing")

    # Rule: If outreach_status == "sent" → next_action_due_date should exist
    if data.get("outreach_status") == "sent" and not data.get("next_action_due_date"):
        warnings.append("outreach_status is 'sent' but next_action_due_date is missing")

    # Log warnings but don't fail
    for warning in warnings:
        print(f"[VALIDATOR] Warning: {warning}")

    return len(errors) == 0, errors


def validate_decision_threshold_alignment(evaluation: dict) -> tuple[bool, list[str]]:
    """
    Validate that evaluation decision aligns with score thresholds.

    This is a soft validation - it warns about misalignment but doesn't fail.

    Args:
        evaluation: The evaluation dictionary

    Returns:
        Tuple of (aligned, warnings)
    """
    warnings = []

    try:
        apply_min = get_threshold("apply_min_score")
        consider_min = get_threshold("consider_min_score")
    except ConfigError:
        return True, []  # Can't validate without config

    score = evaluation.get("role_fit_score", 0)
    decision = evaluation.get("final_decision", "").upper()

    # Check alignment
    if decision == "APPLY" and score < apply_min:
        warnings.append(f"Decision is APPLY but score {score} < threshold {apply_min}")
    elif decision == "CONSIDER" and (score >= apply_min or score < consider_min):
        if score >= apply_min:
            warnings.append(f"Decision is CONSIDER but score {score} >= APPLY threshold {apply_min}")
        else:
            warnings.append(f"Decision is CONSIDER but score {score} < threshold {consider_min}")
    elif decision == "SKIP" and score >= consider_min:
        warnings.append(f"Decision is SKIP but score {score} >= CONSIDER threshold {consider_min}")

    return len(warnings) == 0, warnings
