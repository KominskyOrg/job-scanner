"""
Stage 2 Writer - Generates cover letters and recruiter messages.
Only runs on APPLY roles. Does not re-score or override Stage 1 decisions.

RESPONSIBILITIES:
- Generate high-fidelity cover letters for APPLY roles
- Generate recruiter messages for high-confidence APPLY roles
- Never re-evaluate role fit
- Never modify Stage 1 outputs

GATING THRESHOLDS (from config/pipeline.json):
- Cover letter:      role_fit_score >= cover_letter_min_score   (or force_write)
- Recruiter message: role_fit_score >= recruiter_message_min_score AND confidence == "HIGH" (or force_write)
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from openai import OpenAI

from config import get_threshold


def _get_cover_letter_min_score() -> int:
    """Get cover letter minimum score from config."""
    try:
        return int(get_threshold("cover_letter_min_score"))
    except Exception:
        return 8  # Fallback


def _get_recruiter_message_min_score() -> int:
    """Get recruiter message minimum score from config."""
    try:
        return int(get_threshold("recruiter_message_min_score"))
    except Exception:
        return 9  # Fallback


@dataclass
class WriterOutput:
    """Stage 2 writing outputs."""
    cover_letter: Optional[str] = None
    recruiter_message: Optional[str] = None
    cover_letter_generated: bool = False
    recruiter_message_generated: bool = False
    skip_reason: Optional[str] = None  # Structured skip reason


class JobWriter:
    """Generates application materials for APPLY roles."""

    def __init__(self, profile_path: Path):
        self.candidate_profile = self._load_profile(profile_path)
        self.client = OpenAI()

    def _load_profile(self, profile_path: Path) -> str:
        """Load candidate profile from config."""
        if profile_path.exists():
            with open(profile_path) as f:
                return f.read()
        return ""

    def should_generate(self, evaluation: dict, force_write: bool = False) -> tuple[bool, bool, Optional[str]]:
        """
        Determine what to generate based on Stage 1 evaluation.
        Returns (should_cover_letter, should_recruiter_message, skip_reason)

        Gating rules (from config/pipeline.json):
        - Cover letter:      role_fit_score >= cover_letter_min_score   (or force_write)
        - Recruiter message: role_fit_score >= recruiter_message_min_score AND confidence == "HIGH" (or force_write)
        """
        decision = evaluation.get("final_decision", "")
        confidence = evaluation.get("confidence_signal", "").upper()  # Normalize to uppercase
        score = evaluation.get("role_fit_score", 0)

        # Get thresholds from config
        cover_letter_min = _get_cover_letter_min_score()
        recruiter_message_min = _get_recruiter_message_min_score()

        # Gate 1: Must be APPLY decision
        if decision != "APPLY":
            return False, False, "final_decision_not_apply"

        # Gate 2: Score threshold for cover letter
        if score < cover_letter_min and not force_write:
            return False, False, "score_below_cover_threshold"

        # Cover letter passes - check recruiter message
        should_cover = True

        # Gate 3: Score + confidence threshold for recruiter message
        if force_write:
            should_recruiter = True
        elif score >= recruiter_message_min and confidence == "HIGH":
            should_recruiter = True
        else:
            should_recruiter = False

        return should_cover, should_recruiter, None

    def generate(self, job_description: str, evaluation: dict,
                 job_metadata: dict, force_write: bool = False,
                 context: Optional[dict] = None) -> WriterOutput:
        """
        Generate application materials for an APPLY role.
        Respects score-based gating unless force_write is True.

        Args:
            job_description: Full job description text
            evaluation: Stage 1 evaluation dict
            job_metadata: Job metadata (company, title, location, etc.)
            force_write: Override score thresholds
            context: Optional dict with stage2_mode, thresholds for prompt context
        """
        should_cover, should_recruiter, skip_reason = self.should_generate(evaluation, force_write)

        output = WriterOutput()

        if not should_cover:
            output.skip_reason = skip_reason
            score = evaluation.get("role_fit_score", 0)
            print(f"[WRITER] Skipping generation: {skip_reason} (score={score})")
            return output

        # Generate cover letter
        score = evaluation.get("role_fit_score", 0)
        print(f"[WRITER] Generating cover letter (score={score})...")
        output.cover_letter = self._generate_cover_letter(
            job_description, evaluation, job_metadata
        )
        output.cover_letter_generated = output.cover_letter is not None

        # Generate recruiter message if score meets threshold AND high confidence (or force_write)
        recruiter_message_min = _get_recruiter_message_min_score()
        if should_recruiter:
            confidence = evaluation.get("confidence_signal", "")
            print(f"[WRITER] Generating recruiter message (score={score}, confidence={confidence})...")
            output.recruiter_message = self._generate_recruiter_message(
                job_description, evaluation, job_metadata
            )
            output.recruiter_message_generated = output.recruiter_message is not None
        else:
            confidence = evaluation.get("confidence_signal", "")
            print(f"[WRITER] Skipping recruiter message (score={score}, confidence={confidence}, need score>={recruiter_message_min} + HIGH)")

        return output

    def _generate_cover_letter(self, job_description: str, evaluation: dict,
                               job_metadata: dict) -> Optional[str]:
        """Generate a submission-ready cover letter."""
        prompt = f"""
You are writing a senior-level, role-specific cover letter for a pre-qualified APPLY role.

Write a high-quality, role-specific cover letter in the first person from the candidate's perspective.

Structure requirements:
- 5 to 7 paragraphs
- Natural paragraph length
- No filler paragraphs

Content requirements:
- Open by explicitly stating the role being applied for
- Explain why this role aligns with the candidate's current scope and growth direction
- Describe the current or recent role with emphasis on ownership and accountability, not support
- Include at least two concrete infrastructure initiatives with specific tools and constraints
- Explain outcomes in terms of reliability, cost, latency, or system behavior
- Include one paragraph explaining motivation for this company or team, grounded in the actual work
- Close with a clear statement of interest in discussing the role

Style requirements:
- Professional and direct
- Technical but readable
- No buzzwords
- No generic platform summaries
- No exaggerated claims
- No flattery

Tone requirements:
- Senior engineer explaining tradeoffs and outcomes
- Confident and grounded
- Focused on substance over enthusiasm

The cover letter must be ready to submit as is and should resemble cover letters written by senior engineers at established engineering organizations.

STAGE 1 EVALUATION SUMMARY:
Role Classification: {evaluation.get('role_classification', 'Unknown')}
Key Requirements: {', '.join(evaluation.get('key_requirements', []))}
Summary: {evaluation.get('summary', '')}

JOB DESCRIPTION:
{job_description}

Write the cover letter now. Output only the letter text.
"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",  # Use stronger model for writing
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[WRITER] Cover letter generation failed: {e}")
            return None

    def _generate_recruiter_message(self, job_description: str, evaluation: dict,
                                    job_metadata: dict) -> Optional[str]:
        """Generate a recruiter outreach message."""
        prompt = f"""
Write a concise LinkedIn or email message for a pre-qualified high-priority role.
The message is intended to start a conversation, not summarize a resume.

Structure requirements:
- 3 to 5 short sentences
- First person voice
- Natural conversational tone

Content requirements:
- One sentence establishing current role and scope, focused on ownership
- One sentence tying that scope to the core problem space of this role
- Optional sentence highlighting a specific relevant focus area if it adds clarity
- One sentence inviting a conversation or asking about next steps

Style requirements:
- Direct and human
- No buzzwords
- No flattery
- No emojis
- No resume-style lists
- Avoid generic phrases like "strong fit" or "excited about the opportunity"

Tone requirements:
- Senior engineer initiating a professional conversation
- Calm, confident, and curious
- Not promotional

JOB CONTEXT:
Company: {job_metadata.get('company', '')}
Role: {job_metadata.get('title', '')}

Write the message now. Output only the message text.
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[WRITER] Recruiter message generation failed: {e}")
            return None
