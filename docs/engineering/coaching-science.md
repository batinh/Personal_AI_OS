# Coaching Science Constants

## TRIMP Formula

| Gender | Formula |
|--------|---------|
| Male | `0.64 × e^(1.92 × HRR)` |
| Female | `0.86 × e^(1.67 × HRR)` |

Gender comes from `config["gender"]`.

## ACWR Thresholds

| Range | Status |
|-------|--------|
| 0.8 – 1.3 | Sweet spot |
| > 1.3 | Caution |
| > 1.5 | Danger |

## Taper Schedule

Injected via `taper_factor` in prompt context:

| Week | Load |
|------|------|
| Week −3 | 75% |
| Week −2 | 50% |
| Race week | 25% |

Rule: never increase load during taper.

## 15% Rule

Weekly volume increases must not exceed 15%.

## GCS Rubric (Gemini scoring)

- **Motor**: cadence vs 175 spm
- **Frame**: decoupling vs 5% threshold
- **Fuel**: pace vs race target
