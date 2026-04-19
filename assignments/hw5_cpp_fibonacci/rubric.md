# Grading Rubric: HW5 Fibonacci Sequence Generator (C++)

Total: 100 points

## Correctness (40 points)

- **Full credit (40):** Program produces exactly: `0 1 1 2 3 5 8 13 21 34 55 89 144 233 377`
- **Partial credit (20-35):** Mostly correct output with minor issues (e.g., wrong separator, trailing whitespace, an extra/missing value, or small off-by-one error)
- **Minimal credit (5-15):** Produces some Fibonacci numbers but with significant errors (wrong values, wrong count, integer overflow)
- **No credit (0):** Does not produce the Fibonacci sequence, crashes, or fails to compile

## Algorithmic Efficiency (25 points)

- **Full credit (25):** Uses memoization, dynamic programming, iterative O(n), or matrix exponentiation
- **Partial credit (10-20):** Naive recursion that still completes correctly for n=14
- **Minimal credit (1-9):** Correct but inefficient approach that is clearly wasteful
- **No credit (0):** Does not produce output (crashes/times out) OR hardcoded answers (not an algorithm)

## Edge Case Handling (15 points)

- **Full credit (15):** Correctly handles `F(0) = 0` and `F(1) = 1` as base cases
- **Partial credit (5-10):** Handles one base case but not the other, or produces correct values but skips one of them in output
- **No credit (0):** Missing or incorrect base cases that lead to wrong output

## Code Clarity (15 points)

- **Full credit (15):** Readable code with meaningful variable names, consistent formatting, helpful comments where logic is non-obvious
- **Partial credit (5-12):** Code works but has readability issues — poor variable names (all single letters without context), inconsistent formatting, or missing comments on complex logic
- **No credit (0):** Obfuscated or extremely difficult to read

## Good Practices (5 points)

- **Full credit (5):** Proper `#include` statements, `return 0;` from `main`, appropriate data types, no magic numbers
- **Partial credit (1-4):** Minor issues (missing return, suboptimal types that still work, small code smells)
- **No credit (0):** Missing headers causing compile failures, no main function, etc.
