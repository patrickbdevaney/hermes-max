# Integration Proof Task

Your goal is to build a correct, tested Python implementation of a Bloom filter from scratch.
This task is designed to exercise every new integration under real load — not as a test of the
integrations, but as a real coding task where they must work together to succeed.

## Requirements

Implement bloom_filter.py with:
- BloomFilter(capacity, error_rate) constructor computing optimal bit array size and hash count
- add(item) and contains(item) methods
- False positive rate must be within 10% of the theoretical rate at capacity
- A test suite in test_bloom_filter.py with at least 8 tests

## Process requirements (these are not optional steps — they are how you work)

1. Before writing any code, call repo_map to orient to the codebase, then call
   code_impact on any existing hashing utilities. Use lsp_find_references if you
   touch any existing symbol.

2. Write SPEC.md and TASKS.md before implementation. Decompose into subtasks and
   checkpoint after each green subtask.

3. After tests pass, call property_test on bloom_filter.py. Then call metamorphic_test
   with at least two invariants: (a) anything added is always found, (b) false positive
   rate at capacity is within bounds. If property_test or metamorphic_test finds a
   failure, fix it before proceeding.

4. Do NOT use deep_research. This is a parametric topic. If you feel the urge to call
   deep_research, use search_code against the corpus first. The rationing gate will
   block you if you skip this — that is the gate working correctly.

5. Use the filesystem offload skill for any large intermediate outputs. Do not hold
   large blobs in context.

6. When done, the verify gate must be green. The regression gate (hm regression) must
   pass after you finish.

Done when: verify green, property_test passes, metamorphic invariants hold, checkpoint
exists, hm regression passes.
