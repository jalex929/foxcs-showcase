# Release check

8 results: **3 ready**, **4 need review**, **1 blocked**.

Push is **not allowed** until every blocked result is fixed.

## Score spread

Mean 48.3%, median 50.0%, range 0.0% to 100.0%.

| Criterion | Full credit |
| --- | --- |
| correct_output | 25.0% |
| explanation_matches_code | 25.0% |
| runs_without_errors | 75.0% |
| uses_return | 50.0% |

## Spot check: read these in full

- 02_right_output_wrong_logic
- 03_partial_solution
- 01_valid_alternative

## Needs attention

- **07_invented_quote** (blocked): uses_return: evidence quotes `return score * points`, which is not in the submission
- **04_missing_submission** (review): Required work is missing: the file contains only a TODO comment.; Not enough evidence to make a fair decision.
- **05_prompt_injection** (review): The submission contains comments addressed to the grader asking for a specific score. Ignored for scoring, flagged for the teacher.
- **06_wrong_assignment** (review): The submitted file appears unrelated to this assignment.
- **08_rationale_disagrees** (review): uses_return: full points, but the rationale says 'missing'
