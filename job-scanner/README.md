# Job Scanner

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

Automated job screening pipeline that scrapes job listings, evaluates fit using LLM-based analysis, and generates personalized application materials for high-match roles.

## Features

- **Smart Scraping** - Extracts job listings with deduplication and incremental updates
- **LLM Evaluation** - Uses GPT-4 to score role fit against your profile
- **Deterministic Gates** - Pattern-based filters catch dealbreakers (contract roles, onsite requirements)
- **Cover Letter Generation** - Creates tailored, submission-ready cover letters
- **Recruiter Messages** - Generates concise outreach messages for high-priority roles
- **Traceability** - Full audit trail with prompt versions, hashes, and decision paths

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Usage](#usage)
- [Output Structure](#output-structure)
- [Architecture](#architecture)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Quick Start

### Prerequisites

- Python 3.10+
- OpenAI API key
- LinkedIn account

### Installation

```bash
# Clone the repository
git clone https://github.com/KominskyOrg/job-scanner.git
cd job-scanner

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install browser for scraping
playwright install chromium
```

### Setup

```bash
# Set your OpenAI API key
export OPENAI_API_KEY="your-key-here"

# Configure your searches and profile (see Configuration section)
# Edit config/searches.json with your LinkedIn search URLs
# Edit config/profile_screening.txt with your screening criteria
# Edit config/profile_writing.txt with your background for cover letters
```

### Run

```bash
# 1. Login to LinkedIn (one-time)
python3 src/main.py --login

# 2. Stage 1: Scrape and evaluate jobs
python3 src/main.py --scrape

# 3. Stage 2: Generate cover letters for APPLY roles
python3 src/main.py --write
```

## How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   LinkedIn      │     │   LLM Eval      │     │   Writing       │
│   Scraper       │────▶│   + Gates       │────▶│   Generator     │
│                 │     │                 │     │                 │
│ • Extract jobs  │     │ • Score fit     │     │ • Cover letters │
│ • Deduplicate   │     │ • APPLY/SKIP    │     │ • Recruiter msg │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Stage 1: Scrape & Evaluate

1. Navigates through your configured LinkedIn searches
2. Extracts job descriptions (skips already-stored jobs)
3. Sends each to GPT for evaluation against your profile
4. Applies deterministic gates (contract terms, onsite requirements, role mismatch)
5. Returns decision: **APPLY**, **CONSIDER**, or **SKIP**

### Stage 2: Generate Writing

1. Processes roles marked **APPLY** (score >= 9)
2. Generates tailored cover letter using your profile
3. Generates recruiter outreach message (for high-confidence matches)
4. Saves to `output/apply/{timestamp}/`

### Decision Flow

| Decision | Meaning | Action |
|----------|---------|--------|
| **APPLY** | Strong fit, clear ownership | Generate cover letter + recruiter message |
| **CONSIDER** | Mixed signals, needs review | Manual review required |
| **SKIP** | Poor fit or dealbreaker | No action |

## Configuration

All configuration lives in `config/`.

### `config/searches.json`

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
    "max_jobs_to_check_per_search": 100
  }
}
```

**Tip**: Use LinkedIn's advanced search filters, copy the URL, and paste here.

### `config/profile_screening.txt`

Your screening profile - short, strict criteria for evaluation:

```
SCREENING PROFILE VERSION: 2024-01-29

TARGET ROLE TYPES
- Senior Site Reliability Engineer
- Senior Platform Engineer
...

DEALBREAKERS (auto-SKIP)
- Contract-to-hire, 1099, corp-to-corp
- Onsite required or relocation required
...
```

### `config/profile_writing.txt`

Your full narrative profile for cover letter generation. Include:
- Current role with ownership examples
- Key technical skills and tools
- Specific projects with outcomes

### `config/pipeline.json`

Threshold settings:

```json
{
  "thresholds": {
    "apply_min_score": 9,
    "consider_min_score": 6,
    "cover_letter_min_score": 8,
    "recruiter_message_min_score": 9
  }
}
```

### `config/prompt.txt`

LLM screening instructions. Modify to adjust evaluation behavior.

## Usage

### Commands

```bash
# Login to LinkedIn (saves session)
python3 src/main.py --login

# Stage 1: Scrape and evaluate
python3 src/main.py --scrape

# Stage 2: Generate writing for APPLY roles
python3 src/main.py --write
python3 src/main.py --write --max 5  # Limit to 5 roles

# Test mode: Single job through both stages
python3 src/main.py --test
```

### Typical Workflow

1. **First run**: `--login` to authenticate with LinkedIn
2. **Daily**: `--scrape` to collect and evaluate new jobs
3. **As needed**: `--write` to generate application materials
4. **Review**: Check `output/apply/` for generated content

## Output Structure

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
├── quarantine/               # Invalid data
└── needs_attention/          # Recoverable issues
```

### Example Evaluation Output

```json
{
  "role_fit_score": 9,
  "final_decision": "APPLY",
  "confidence_signal": "HIGH",
  "role_classification": "Senior Platform Engineer",
  "summary": "Strong platform ownership role with EKS, Terraform, clear IC scope",
  "key_requirements": ["EKS cluster management", "Terraform modules", "On-call rotation"],
  "concerns": ["Startup - funding stage unclear"]
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                              │
│                    (CLI orchestrator)                       │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   scraper.py    │  │  evaluator.py   │  │   writer.py     │
│  (Playwright)   │  │ (OpenAI API)    │  │ (OpenAI API)    │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │      post_evaluator.py            │
              │    (Deterministic gates)          │
              │                                   │
              │ • Contract terms check            │
              │ • Onsite/location check           │
              │ • Role mismatch detection         │
              │ • Staffing firm detection         │
              └───────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │         storage.py                │
              │    (Per-role directories)         │
              └───────────────────────────────────┘
```

### Key Design Decisions

- **Single source of truth**: Stage 1 makes all APPLY/SKIP decisions
- **Deterministic gates**: Pattern matching for dealbreakers (no LLM variance)
- **Per-role storage**: Each role gets its own directory with typed JSON files
- **Idempotent scraping**: Roles deduplicated by canonical ID
- **Defense in depth**: Schema validation even with OpenAI Structured Outputs

## Development

### Project Structure

```
job-scanner/
├── src/
│   ├── main.py           # CLI entry point
│   ├── scraper.py        # LinkedIn scraping
│   ├── evaluator.py      # LLM evaluation
│   ├── post_evaluator.py # Deterministic gates
│   ├── writer.py         # Cover letter generation
│   ├── storage.py        # Data persistence
│   ├── models.py         # Domain models
│   ├── config.py         # Configuration loader
│   └── schema_validator.py
├── config/               # Configuration files
├── test_jds/             # Regression test cases
└── output/               # Generated outputs
```

### Running Tests

```bash
# Run regression tests
python3 src/run_regression.py
python3 src/run_regression.py --verbose

# Test single job
python3 src/main.py --test
```

### Regression Test Cases

| Test Case | Expected | Why |
|-----------|----------|-----|
| `clean_platform_role` | APPLY | Clear platform ownership |
| `software_engineer_infrastructure_role` | APPLY | Title misleading, pure platform work |
| `backend_role` | SKIP | Product features, wrong role type |
| `contract_to_hire_role` | SKIP | Contract dealbreaker |
| `onsite_role` | SKIP | Relocation required |
| `staffing_firm_role` | CONSIDER | Staffing firm detected |
| `ambiguous_devops_role` | CONSIDER | Unclear ownership |

### Adding Post Gates

1. Add pattern to `src/post_evaluator.py`
2. Wire into `validate_apply_gates()`
3. Add test case to `test_jds/`
4. Run regression: `python3 src/run_regression.py`

## Troubleshooting

### Common Issues

**"Config required" error**
- Ensure `config/pipeline.json` exists and is valid JSON

**LinkedIn login issues**
- Delete `browser_profile/` and re-run `--login`
- LinkedIn may require CAPTCHA or 2FA

**"No job cards found"**
- LinkedIn's HTML structure may have changed
- Try running `--login` again

**OpenAI API errors**
- Verify `OPENAI_API_KEY` is set
- Check API quota and billing

**Stage 2 skipping all roles**
- Check `[STAGE2]` log output for skip reasons
- Verify roles have APPLY decision

## Safety

This tool:
- **Never auto-applies** to jobs
- **Never messages** recruiters automatically
- Uses human-like delays to avoid detection
- Runs in visible browser mode by default

## Limitations

- LinkedIn only (currently)
- Requires manual login (sessions expire)
- Single user (no multi-tenancy)

## License

MIT License - See [LICENSE](LICENSE) for details.

## Acknowledgments

- [Playwright](https://playwright.dev/) for browser automation
- [OpenAI](https://openai.com/) for LLM evaluation
