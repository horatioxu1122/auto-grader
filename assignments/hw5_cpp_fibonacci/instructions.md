# HW5: Fibonacci Sequence Generator (C++)

Write a C++ program that prints the first 15 Fibonacci numbers — specifically `F(0)` through `F(14)`.

## Fibonacci definition

- `F(0) = 0`
- `F(1) = 1`
- `F(n) = F(n-1) + F(n-2)` for `n >= 2`

## Expected output

Print all 15 numbers separated by spaces on a single line, followed by a newline:

```
0 1 1 2 3 5 8 13 21 34 55 89 144 233 377
```

## Requirements

1. The program must read no input from stdin and print the sequence to stdout.
2. The program must compile cleanly with `g++ -std=c++17 -O2`.
3. Your implementation should be efficient enough that it finishes well within a few seconds.
4. Use meaningful variable names and reasonable code structure.
5. The program should return 0 from `main`.

## Submission

Submit a single `.cpp` file. You may name it anything you like (e.g., `solution.cpp`, `fib.cpp`).
