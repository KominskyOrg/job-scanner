"""
Job Evaluator using OpenAI API for LLM-based job screening.
Uses Structured Outputs for guaranteed schema compliance.

SCHEMA CONTRACT
---------------
This evaluator expects JSON matching this exact schema.
Schema is enforced by OpenAI Structured Outputs API.
Validation is kept as defense-in-depth.

The prompt in config/prompt.txt MUST request this exact schema.
If you modify one, you MUST modify the other.
"""

import json
import hashlib
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from openai import OpenAI

from config import get_enum_values, validate_enum


# Model configuration - single source of truth
EVALUATION_MODEL = "gpt-4o"
EVALUATION_TEMPERATURE = 0.3
EVALUATION_MAX_TOKENS = 1024


def _get_allowed_values(enum_name: str) -> set[str]:
    """Get allowed values for an enum from config."""
    try:
        return set(get_enum_values(enum_name))
    except Exception:
        # Fallback to hardcoded values if config fails
        fallbacks = {
            "final_decision": {"APPLY", "CONSIDER", "SKIP"},
            "risk_level": {"low", "medium", "high"},
            "confidence_signal": {"HIGH", "MEDIUM", "LOW"},
            "seniority_level": {"Junior", "Mid", "Senior", "Staff", "Principal", "Unknown"},
            "remote_status": {"Remote", "Hybrid", "Onsite", "Unknown"},
        }
        return fallbacks.get(enum_name, set())

REQUIRED_FIELDS = [
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

# JSON Schema for OpenAI Structured Outputs
EVALUATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "job_evaluation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "role_fit_score": {
                    "type": "integer",
                    "description": "Score from 1-10 indicating role fit"
                },
                "role_classification": {
                    "type": "string",
                    "description": "Classification of the role type"
                },
                "seniority_level": {
                    "type": "string",
                    "enum": ["Junior", "Mid", "Senior", "Staff", "Principal", "Unknown"]
                },
                "remote_status": {
                    "type": "string",
                    "enum": ["Remote", "Hybrid", "Onsite", "Unknown"]
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"]
                },
                "final_decision": {
                    "type": "string",
                    "enum": ["APPLY", "CONSIDER", "SKIP"]
                },
                "confidence_signal": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW"]
                },
                "key_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10
                },
                "concerns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10
                },
                "summary": {
                    "type": "string",
                    "description": "One sentence assessment"
                }
            },
            "required": [
                "role_fit_score", "role_classification", "seniority_level",
                "remote_status", "risk_level", "final_decision",
                "confidence_signal", "key_requirements", "concerns", "summary"
            ],
            "additionalProperties": False
        }
    }
}


@dataclass
class JobEvaluation:
    """Structured evaluation result from LLM."""
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
    is_valid: bool = True
    error: Optional[str] = None


class JobEvaluator:
    """Evaluates job descriptions using OpenAI API with Structured Outputs."""

    def __init__(self, prompt_path: Path, profile_path: Path):
        self.prompt_path = prompt_path
        self.profile_path = profile_path
        self.prompt_template = self._load_prompt(prompt_path)
        self.profile_template = self._load_profile(profile_path)
        self.prompt_version, self.prompt_hash = self._get_prompt_version()
        self.profile_version, self.profile_hash = self._get_profile_version()
        self.client = OpenAI()

    def _load_prompt(self, prompt_path: Path) -> str:
        """Load the screening prompt template."""
        with open(prompt_path) as f:
            return f.read()

    def _load_profile(self, profile_path: Path) -> str:
        """Load the screening profile."""
        try:
            with open(profile_path) as f:
                content = f.read()
            if not content.strip():
                raise ValueError("Profile file is empty")
            return content
        except Exception as e:
            # Will be handled in evaluate_job
            self._profile_load_error = str(e)
            return ""

    def _get_prompt_version(self) -> tuple[str, str]:
        """Extract version from prompt and compute content hash."""
        version = "unknown"

        # Look for version header: # VERSION: X.Y
        for line in self.prompt_template.split('\n')[:5]:
            if line.strip().upper().startswith('# VERSION:'):
                version = line.split(':', 1)[1].strip()
                break

        # Compute hash of prompt content
        prompt_hash = hashlib.sha256(self.prompt_template.encode()).hexdigest()[:12]

        return version, prompt_hash

    def _get_profile_version(self) -> tuple[str, str]:
        """Extract version from profile and compute content hash."""
        version = "unknown"

        for line in self.profile_template.split('\n')[:5]:
            if 'VERSION:' in line.upper():
                version = line.split(':', 1)[1].strip()
                break

        profile_hash = hashlib.sha256(self.profile_template.encode()).hexdigest()[:12]
        return version, profile_hash

    def evaluate_job(self, job_description: str, retry: bool = True) -> tuple["JobEvaluation", dict]:
        """
        Send job description to OpenAI for evaluation.
        Returns (evaluation, metadata) tuple.
        """
        print("[EVALUATOR] Sending job to OpenAI for evaluation...")

        eval_timestamp = datetime.now().isoformat()
        decision_path = []

        # Initialize metadata with trace fields
        metadata = {
            "model": EVALUATION_MODEL,
            "temperature": EVALUATION_TEMPERATURE,
            "timestamp": eval_timestamp,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "profile_version": self.profile_version,
            "profile_hash": self.profile_hash,
            "job_description_hash": hashlib.sha256(job_description.encode()).hexdigest()[:12],
            "latency_ms": None,
            "token_usage": None,
            "decision_path": decision_path,
            "status": "success",
        }

        # Fail closed when profile missing
        if hasattr(self, '_profile_load_error'):
            metadata["status"] = "profile_error"
            metadata["error"] = self._profile_load_error
            return self._create_invalid_evaluation(f"Profile error: {self._profile_load_error}"), metadata

        # Build prompt in three blocks with explicit labels
        full_prompt = f"""CANDIDATE PROFILE
=================
THESE ARE HARD CONSTRAINTS AND PRIORITIES. TREAT AS BINDING RULES.

{self.profile_template}

SCREENING INSTRUCTIONS
======================
{self.prompt_template}

JOB DESCRIPTION
===============
{job_description}
"""

        start_time = time.time()

        try:
            response = self.client.chat.completions.create(
                model=EVALUATION_MODEL,
                messages=[
                    {"role": "user", "content": full_prompt}
                ],
                max_tokens=EVALUATION_MAX_TOKENS,
                temperature=EVALUATION_TEMPERATURE,
                response_format=EVALUATION_SCHEMA,
            )

            # Capture latency
            metadata["latency_ms"] = int((time.time() - start_time) * 1000)

            # Capture token usage
            if response.usage:
                metadata["token_usage"] = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            response_text = response.choices[0].message.content
            print("[EVALUATOR] Received response from OpenAI")
            decision_path.append("api_success")

            evaluation = self._parse_and_validate(response_text, decision_path)
            return evaluation, metadata

        except Exception as e:
            metadata["latency_ms"] = int((time.time() - start_time) * 1000)
            metadata["status"] = "error"
            metadata["error"] = str(e)
            decision_path.append("api_error")
            print(f"[EVALUATOR] API error: {e}")

            if retry:
                print("[EVALUATOR] Retrying once...")
                return self.evaluate_job(job_description, retry=False)

            return self._create_invalid_evaluation(f"API error: {e}"), metadata

    def _parse_and_validate(self, response_text: str, decision_path: list) -> JobEvaluation:
        """
        Parse and validate LLM response.
        Structured Outputs guarantees valid JSON format.
        Validation kept as defense-in-depth.
        """
        # Direct parse - structured outputs guarantee valid JSON format
        try:
            data = json.loads(response_text)
            decision_path.append("json_parsed")
        except json.JSONDecodeError as e:
            # Should not happen with structured outputs
            decision_path.append("json_parse_error")
            return self._create_invalid_evaluation(f"JSON parse error: {e}")

        # Validation as defense-in-depth
        validation_error = self._validate_schema(data)
        if validation_error:
            decision_path.append("validation_failed")
            return self._create_invalid_evaluation(validation_error)
        decision_path.append("schema_valid")

        # Normalize values
        data = self._normalize_values(data)

        # Track score in decision path
        score = data["role_fit_score"]
        decision_path.append(f"score_{score}")

        # Track decision in path
        decision = data["final_decision"]
        decision_path.append(f"decision_{decision.lower()}")

        return JobEvaluation(
            role_fit_score=score,
            role_classification=data["role_classification"],
            seniority_level=data["seniority_level"],
            remote_status=data["remote_status"],
            risk_level=data["risk_level"],
            final_decision=decision,
            confidence_signal=data["confidence_signal"],
            key_requirements=data["key_requirements"],
            concerns=data["concerns"],
            summary=data["summary"],
            is_valid=True,
        )

    def _validate_schema(self, data: dict) -> Optional[str]:
        """Validate all required fields exist and have valid values (defense-in-depth)."""
        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in data:
                return f"Missing required field: {field}"

        # Validate score range
        score = data.get("role_fit_score")
        if not isinstance(score, int) or score < 1 or score > 10:
            return f"Invalid role_fit_score: {score} (must be integer 1-10)"

        # Validate enum fields using config
        decision = data.get("final_decision", "").upper()
        allowed_decisions = _get_allowed_values("final_decision")
        if decision not in allowed_decisions:
            return f"Invalid final_decision: {decision}"

        risk = data.get("risk_level", "").lower()
        allowed_risk = _get_allowed_values("risk_level")
        if risk not in allowed_risk:
            return f"Invalid risk_level: {risk}"

        confidence = data.get("confidence_signal", "").upper()
        allowed_confidence = _get_allowed_values("confidence_signal")
        if confidence not in allowed_confidence:
            return f"Invalid confidence_signal: {confidence}"

        # Validate arrays
        key_reqs = data.get("key_requirements")
        if not isinstance(key_reqs, list) or len(key_reqs) < 1:
            return "key_requirements must be a non-empty array"

        concerns = data.get("concerns")
        if not isinstance(concerns, list) or len(concerns) < 1:
            return "concerns must be a non-empty array"

        return None

    def _normalize_values(self, data: dict) -> dict:
        """Normalize values to canonical form based on config enum definitions."""
        # Normalize to defined cases per config
        data["final_decision"] = str(data["final_decision"]).upper()
        data["risk_level"] = str(data["risk_level"]).lower()
        data["confidence_signal"] = str(data["confidence_signal"]).upper()
        data["role_fit_score"] = int(data["role_fit_score"])

        # Normalize seniority (title case) - validate against config
        allowed_seniority = _get_allowed_values("seniority_level")
        seniority = str(data.get("seniority_level", "Unknown")).title()
        data["seniority_level"] = seniority if seniority in allowed_seniority else "Unknown"

        # Normalize remote status (title case) - validate against config
        allowed_remote = _get_allowed_values("remote_status")
        remote = str(data.get("remote_status", "Unknown")).title()
        data["remote_status"] = remote if remote in allowed_remote else "Unknown"

        return data

    def _create_invalid_evaluation(self, error: str) -> JobEvaluation:
        """Create an invalid evaluation result."""
        print(f"[EVALUATOR] Validation failed: {error}")

        return JobEvaluation(
            role_fit_score=1,
            role_classification="Unknown",
            seniority_level="Unknown",
            remote_status="Unknown",
            risk_level="high",
            final_decision="SKIP",
            confidence_signal="LOW",
            key_requirements=["Evaluation failed"],
            concerns=[f"Evaluation failed: {error}"],
            summary="Evaluation failed - marked for manual review",
            is_valid=False,
            error=error,
        )

    def to_dict(self, evaluation: JobEvaluation) -> dict:
        """Convert evaluation to dictionary for JSON output."""
        return asdict(evaluation)
