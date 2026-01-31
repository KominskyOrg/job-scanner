# Job Scanner

Automated LinkedIn job screening pipeline that scrapes job listings, evaluates them using LLM-based analysis, and generates personalized application materials for high-fit roles.

## Overview

Job Scanner automates the tedious parts of job searching:

1. **Stage 1 (Scrape & Evaluate)**: Scrapes LinkedIn job searches, extracts descriptions, and uses GPT to evaluate role fit against your profile
2. **Post Gates**: Deterministic filters catch dealbreakers (contract roles, onsite requirements, role mismatches)
3. **Stage 2 (Write)**: Generates cover letters and recruiter messages for roles marked APPLY

The pipeline is opinionated: it filters aggressively. Most roles should not pass. Only high-fit roles get application materials generated.

### Single Source of Truth

Stage 1 is the single decision point (APPLY/CONSIDER/SKIP):
- **profile_screening.txt**: Hard constraints, dealbreakers, and decision rubric for screening
- **profile_writing.txt**: Full narrative profile for cover letter generation
- **Post gates** enforce dealbreakers deterministically (no LLM re-evaluation)

## Quick Start

```bash
# 1. Clone and setup
cd job-scanner
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Configure
export OPENAI_API_KEY="your-key-here"
# Edit config/searches.json with your LinkedIn search URLs
# Edit config/profile_screening.txt with your screening criteria and dealbreakers
# Edit config/profile_writing.txt with your full background for cover letters
# Edit config/prompt.txt with screening instructions (references profile_screening.txt)

# 3. Login to LinkedIn (once)
python3 src/main.py --login

# 4. Run Stage 1: Scrape and evaluate
python3 src/main.py --scrape

# 5. Run Stage 2: Generate writing for APPLY roles
python3 src/main.py --write
```

## Requirements

- Python 3.10+
- OpenAI API key (GPT-4 for writing, configurable model for evaluation)
- LinkedIn account
- macOS/Linux (Windows untested)

## Installation

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for LLM calls |
| `ALLOW_CONFIG_FALLBACK` | No | Set to `true` to allow running without config (dangerous) |

### Config Files

All configuration lives in `config/`:

#### `config/searches.json`
Define your LinkedIn job searches:

```json
{
  "searches": [
    {
      "name": "platform-engineer-remote",
      "url": "https://www.linkedin.com/jobs/search/?keywords=platform%20engineer..."
    }
  ],
  "settings": {
    "target_new_jobs_per_search": 10,
    "max_jobs_to_check_per_search": 100,
    "delay_between_jobs_ms": { "min": 1000, "max": 3000 },
    "delay_between_actions_ms": { "min": 500, "max": 1000 }
  }
}
```

| Setting | Purpose |
|---------|---------|
| `target_new_jobs_per_search` | Stop when this many truly new jobs are found |
| `max_jobs_to_check_per_search` | Hard cap on jobs checked per search (prevents infinite scroll) |

The scraper checks each job against storage before extracting details, only counting jobs not already stored toward the target. This avoids wasting effort on previously seen jobs.

**Tip**: Use LinkedIn's advanced search filters, copy the URL, and paste here.

#### `config/profile_screening.txt`
**The screening profile** - short, strict, stable. Used by Stage 1 evaluator and post gates.

```
SCREENING PROFILE VERSION: 2026-01-29

TARGET ROLE TYPES
- Senior Site Reliability Engineer
- Senior Platform Engineer
...

DEALBREAKERS (auto-SKIP)
- Contract-to-hire, 1099, corp-to-corp, fixed-term
- Onsite required or relocation required
- Customer-facing, sales-adjacent roles
...

DECISION RUBRIC
APPLY: Meets all non-negotiables, no dealbreakers, clear ownership
CONSIDER: Partial match, needs manual review
SKIP: Any dealbreaker triggered
```

#### `config/profile_writing.txt`
**The writing profile** - full narrative for cover letter generation. Include:
- Current/recent role with ownership examples
- Key technical skills and tools
- Specific projects with outcomes
- Communication style notes

This file is also copied to `job_search/base/profile_writing.txt` for use with the master workflow template.

#### `config/prompt.txt`
The LLM screening instructions. References `profile_screening.txt` which is injected as a separate block:

```
# VERSION: 1.3
You are a strict job screening engine...

CANDIDATE PROFILE (provided above) contains hard constraints and priorities.
Treat profile rules as binding. Use only that profile for candidate context.

APPLY HARD GATES
...
```

#### `config/profile.txt` (deprecated)
Legacy profile file for backward compatibility. Use `profile_writing.txt` instead.

#### `config/pipeline.json`
Pipeline behavior settings:

```json
{
  "thresholds": {
    "apply_min_score": 9,
    "consider_min_score": 6,
    "cover_letter_min_score": 8,
    "recruiter_message_min_score": 9
  },
  "stage2_mode": "strict"
}
```

**Note**: In strict mode, `cover_letter_min_score` below `apply_min_score` is effectively unused since Stage 2 only runs for APPLY roles.

## Usage

### Commands

```bash
# Login to LinkedIn (saves session to browser_profile/)
python3 src/main.py --login

# Stage 1: Scrape jobs and evaluate
python3 src/main.py --scrape

# Stage 2: Generate writing for APPLY roles
python3 src/main.py --write
python3 src/main.py --write --max 5  # Process up to 5 roles

# Test mode: Process single job through both stages
python3 src/main.py --test
```

### Workflow

1. **First run**: Use `--login` to authenticate with LinkedIn. Session is saved to `browser_profile/`.

2. **Daily scraping**: Run `--scrape` to collect new jobs. The scraper:
   - Navigates through your configured searches
   - Checks each job against storage before extracting (smart deduplication)
   - Keeps scrolling until `target_new_jobs_per_search` truly new jobs found
   - Exits early if search appears stale (50 checked, 0 new)
   - Sends descriptions to GPT for evaluation
   - Logs efficiency metrics per search (e.g., "10 new / 67 checked (14.9%)")
   - Saves results to `output/roles/`

3. **Generate applications**: Run `--write` to create materials for APPLY roles:
   - Validates Stage 1 evaluation
   - Generates cover letter (if score >= threshold)
   - Generates recruiter message (if score >= threshold AND high confidence)
   - Saves to `output/apply/{timestamp}/`

### Output Structure

```
output/
├── roles/                    # Per-role storage (primary)
│   └── {role_id}/
│       ├── job_posting.json
│       ├── evaluation.json
│       ├── application_plan.json
│       └── pipeline_state.json
├── apply/                    # Stage 2 outputs by run
│   └── {timestamp}/
│       └── {company}-{title}.json
├── scan-results.json         # Aggregated view (derived)
├── job-descriptions.json     # Description cache (legacy)
├── quarantine/               # Invalid data
└── needs_attention/          # Recoverable issues
```

## Evaluation Decisions

The LLM evaluates each role and returns:

| Decision | Meaning | Stage 2 |
|----------|---------|---------|
| **APPLY** | Strong fit, clear ownership, acceptable risk | Yes |
| **CONSIDER** | Mixed signals, needs manual review | No (unless forced) |
| **SKIP** | Poor fit or disqualifier triggered | No |

### Scoring

- **9-10**: Top tier, direct platform/infra ownership
- **7-8**: Good fit with minor concerns
- **5-6**: Adjacent but uncertain
- **1-4**: Poor fit, wrong role type

### Post Gates (Deterministic Filters)

After LLM evaluation, deterministic gates catch dealbreakers that patterns can reliably detect:

| Gate | Skip Triggers | Consider Triggers |
|------|---------------|-------------------|
| **Contract** | `contract-to-hire`, `c2h`, `1099`, `corp-to-corp`, `w2 contract`, `staff augmentation` | `contract`, `contractor` (without full-time signals) |
| **Onsite** | `relocation required`, `onsite required`, LLM detected "Onsite" | `hybrid`, `X days in office`, `must be located in` |
| **Role Mismatch** | Product features, React/mobile/frontend focus without platform signals | - |
| **Staffing Firm** | Known staffing companies, "our client", "on behalf of" | Staffing indicators present |

Gates run in order. A role can be downgraded from APPLY to CONSIDER or SKIP but never upgraded.

**Traceability**: All gate decisions are logged in `evaluation.json` under `evaluator_metadata`:
- `pre_gates_final_decision`: Decision before gates
- `post_gates_final_decision`: Decision after gates
- `post_gates_reasons`: List of triggered gates (e.g., `["contract:c2h", "onsite:hybrid"]`)

## Safety

This tool:
- Never auto-applies to jobs
- Never messages recruiters automatically
- Uses human-like delays to avoid detection
- Smart scraping: targets new jobs only, with configurable caps
- Early exit on stale searches to reduce LinkedIn friction
- Runs in visible browser mode by default

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                               │
│                    (CLI orchestrator)                        │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   scraper.py    │  │  evaluator.py   │  │   writer.py     │
│  (Playwright)   │  │ (OpenAI API)    │  │ (OpenAI API)    │
│                 │  │                 │  │                 │
│ - LinkedIn nav  │  │ - Profile inject│  │ - Cover letters │
│ - Job extract   │  │ - Structured    │  │ - Recruiter msg │
│ - Pagination    │  │   Outputs       │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │      post_evaluator.py            │
              │    (Deterministic gates)          │
              │                                   │
              │ - Contract terms check            │
              │ - Onsite/location check           │
              │ - Role mismatch detection         │
              │ - Staffing firm detection         │
              └───────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │         storage.py                │
              │    (Per-role directories)         │
              │                                   │
              │ - Role deduplication              │
              │ - In-memory index                 │
              │ - Quarantine/attention            │
              └───────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │          models.py                │
              │      (Domain objects)             │
              │                                   │
              │ - JobPosting, Evaluation          │
              │ - ApplicationPlan                 │
              │ - Canonical ID generation         │
              └───────────────────────────────────┘
```

### Traceability

Every evaluation includes trace fields in `evaluator_metadata` for debugging and auditing:

| Field | Description |
|-------|-------------|
| `prompt_version` | Version from prompt.txt header (e.g., "1.3") |
| `prompt_hash` | SHA256 hash of prompt content (first 12 chars) |
| `profile_version` | Version from profile_screening.txt header |
| `profile_hash` | SHA256 hash of profile content (first 12 chars) |
| `job_description_hash` | SHA256 hash of job description (first 12 chars) |
| `pre_gates_final_decision` | LLM decision before post gates |
| `post_gates_final_decision` | Final decision after all gates |
| `post_gates_reasons` | List of gate triggers (e.g., `["contract:c2h"]`) |
| `decision_path` | Array of validation steps taken |

### Key Design Decisions

- **Single source of truth**: Stage 1 is the only decision point; post gates enforce, don't re-decide
- **Profile separation**: Screening profile (strict, stable) vs writing profile (narrative, detailed)
- **Per-role storage**: Each role gets its own directory with typed JSON files
- **Idempotent scraping**: Roles are deduplicated by canonical ID (source + job ID hash)
- **Strict gating**: Stage 2 only runs for APPLY roles that pass validation
- **Defense in depth**: Schema validation even with OpenAI Structured Outputs
- **Crash recovery**: Incremental saves during scrape
- **Deterministic gates**: Pattern-based dealbreaker detection (no LLM variance)

## Development

### Project Structure

```
job-scanner/
├── src/
│   ├── main.py           # CLI entry point, orchestration
│   ├── scraper.py        # LinkedIn scraping with Playwright
│   ├── evaluator.py      # LLM evaluation with OpenAI
│   ├── post_evaluator.py # Deterministic gates (contract, onsite, role mismatch)
│   ├── writer.py         # Cover letter/message generation
│   ├── storage.py        # Per-role directory storage
│   ├── models.py         # Domain models and ID generation
│   ├── output.py         # Legacy output formatting
│   ├── schema_validator.py # Stage 1 validation
│   ├── config.py         # Configuration loader
│   └── run_regression.py # Regression test harness
├── config/
│   ├── pipeline.json     # Thresholds and settings
│   ├── searches.json     # LinkedIn search URLs
│   ├── prompt.txt        # Screening instructions
│   ├── profile_screening.txt  # Screening profile (dealbreakers, decision rubric)
│   ├── profile_writing.txt    # Writing profile (full narrative)
│   └── profile.txt       # Legacy profile (deprecated)
├── test_jds/             # Regression test job descriptions
│   ├── *.txt             # Sample JDs (9 test cases)
│   └── expected_outcomes.json
├── output/               # Generated outputs
├── browser_profile/      # Playwright session (gitignored)
├── requirements.txt
└── README.md
```

### Running Tests

```bash
# Test single job extraction and evaluation
python3 src/main.py --test

# Run regression tests (evaluates 9 sample JDs against expected outcomes)
python3 src/run_regression.py
python3 src/run_regression.py --verbose  # Show detailed output
python3 src/run_regression.py --dry-run  # Show what would be tested
python3 src/run_regression.py --test clean_platform_role  # Run single test

# Verify config loading
python3 -c "from src.config import get_config; print(get_config())"

# Check fallback mode status
python3 -c "from src.config import is_fallback_mode; print(is_fallback_mode())"
```

### Regression Test Cases

The `test_jds/` folder contains 9 sample job descriptions covering edge cases:

| Test Case | Expected | Why |
|-----------|----------|-----|
| `clean_platform_role` | APPLY | Clear platform ownership, EKS, Terraform |
| `software_engineer_infrastructure_role` | APPLY | Title misleading, but pure platform work |
| `backend_engineer_platform_role` | APPLY/CONSIDER | Hybrid backend/platform role |
| `backend_role` | SKIP | Product features, React, wrong role type |
| `contract_to_hire_role` | SKIP | Contract-to-hire dealbreaker |
| `onsite_role` | SKIP | Relocation required dealbreaker |
| `staffing_firm_role` | CONSIDER | CyberCoders staffing firm |
| `ambiguous_devops_role` | CONSIDER | Unclear ownership, vague scope |
| `devops_role_support_queue` | SKIP | Ticket queue support, not platform |

### Common Development Tasks

**Adding a new evaluation field:**
1. Update schema in `src/evaluator.py` (`EVALUATION_SCHEMA`)
2. Update prompt in `config/prompt.txt`
3. Add field to `JobEvaluation` dataclass
4. Update `models.py` `Evaluation` class if persisted

**Changing thresholds:**
1. Edit `config/pipeline.json`
2. Thresholds are loaded at runtime, no code changes needed

**Modifying scraping selectors:**
1. Edit `src/scraper.py` selector lists
2. LinkedIn changes frequently; multiple fallback selectors are used

**Adding/modifying post gates:**
1. Edit pattern lists in `src/post_evaluator.py` (e.g., `CONTRACT_SKIP_PATTERNS`)
2. Add new gate function following `check_contract_terms` pattern
3. Wire into `validate_apply_gates()` function
4. Add test case to `test_jds/` and update `expected_outcomes.json`
5. Run regression: `python3 src/run_regression.py`

**Updating screening profile:**
1. Edit `config/profile_screening.txt`
2. Update version header (e.g., `SCREENING PROFILE VERSION: 2026-01-30`)
3. Profile hash changes automatically tracked in `evaluator_metadata`

### Debugging

**Scraper issues:**
- Raw HTML is captured on extraction failure (capped at 100KB)
- Check `output/quarantine/` for failed extractions
- Run with `--test` to see single job extraction

**Evaluation issues:**
- Check `evaluation.json` in role directory for full metadata
- `evaluator_metadata.decision_path` array shows validation steps
- `evaluator_metadata.post_gates_reasons` shows which gates triggered
- Compare `pre_gates_final_decision` vs `post_gates_final_decision` to see gate impact
- Invalid evaluations go to `output/needs_attention/`

**Post gate issues:**
- Check `post_gates_reasons` for specific triggers (e.g., `["contract:c2h", "onsite:hybrid"]`)
- Pattern definitions are in `src/post_evaluator.py`
- Run regression tests to verify gate behavior: `python3 src/run_regression.py`

**Stage 2 issues:**
- Look for `[STAGE2]` log lines showing gating decisions
- Format: `[STAGE2] {role_id} {display_name}: {decision} (reason)`

### Environment Variables for Development

```bash
# Allow running without config (for testing)
export ALLOW_CONFIG_FALLBACK=true

# OpenAI API key
export OPENAI_API_KEY="sk-..."
```

### Code Style

- Type hints on all function signatures
- Dataclasses for domain objects
- Config-driven behavior (thresholds, enums)
- Explicit error handling with typed exceptions

## Troubleshooting

### Common Issues

**"Config required" error:**
- Ensure `config/pipeline.json` exists and is valid JSON
- Or set `ALLOW_CONFIG_FALLBACK=true` (not recommended for production use)

**LinkedIn login issues:**
- Delete `browser_profile/` and re-run `--login`
- LinkedIn may require CAPTCHA or 2FA
- Sessions expire; re-login if scraping fails

**"No job cards found":**
- LinkedIn's HTML structure may have changed
- Check if you're logged in (session expired)
- Try running `--login` again

**OpenAI API errors:**
- Verify `OPENAI_API_KEY` is set and valid
- Check API quota and billing
- Review rate limits

**Stage 2 skipping all roles:**
- Check `[STAGE2]` log output for skip reasons
- Verify roles have APPLY decision
- Check threshold configuration

## Limitations

- **LinkedIn only**: Currently only supports LinkedIn job searches
- **Rate limiting**: Uses delays to avoid detection; scraping is intentionally slow
- **Session management**: Requires manual login; sessions may expire
- **Single user**: No multi-tenancy; designed for personal use
- **Cross-source duplicates**: Same job from different sources creates separate entries (by design)

## License

Private use only. Not for distribution.
