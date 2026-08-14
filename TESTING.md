# Testing

## Automated coverage

`./test.sh` runs the backend pytest suite and then the frontend Vitest suite. Gemini is mocked throughout; automated tests never consume real quota.

Backend tests cover:

- health, lightweight session creation/restoration/sign-out, email normalization, validation, and project ownership;
- project creation from pasted text, `.txt` filenames, and the three bundled sample-book IDs, including exact catalogue metadata, source exclusivity, safe invalid-ID rejection, full project-specific book-file persistence, ownership, local-only creation, and restart-safe user/project data;
- initial state, strict five-step ordering, legal state transitions, invalid/repeated actions, failed-step retry, and preservation of completed results;
- atomic SQLite step claims under concurrent requests, rejection of duplicates, and the rule that an old timestamp alone does not make same-process work recoverable;
- process-owner restart recovery, ownership of recovery actions, and preservation of already-ready portrait items;
- local-only project creation, one-time book upload, separate persisted file and interaction state, user-provided/generated styles, and conversation chaining without resending the book;
- adult-character filtering, the two-character cap, malformed structured-output failure without partial persistence, and safe retry;
- per-item portrait progress, immediate file persistence, partial-failure preservation, retrying only failed portraits, duplicate portrait prevention, authenticated image serving, and schema migration;
- chapter ordering and conversation chaining, the one-chapter cap, illustration use of persisted prompts and portrait references, failure isolation, authenticated file serving, and generated-file availability after backend restart;
- missing Gemini configuration and provider failures that persist safe user-facing errors without leaking raw provider details.

Frontend component tests cover:

- identity validation and the empty project-list state;
- project status and five-step progress rendering;
- rendering the bundled sample catalogue and creating projects from a selected sample ID, pasted text, or a `.txt` file without mixing sources;
- showing only the next legal action;
- named running state, persisted failure and retry, backend-authorized recovery, and post-recovery refresh;
- generated character prompts, all portrait item states, chapter/final-illustration rendering, and authenticated image loading;
- polling while `RUNNING` and stopping after `IDLE` or `FAILED`.

## Deliberately not automated

There is no full browser E2E suite; the assessment does not require one, and component/API tests cover the core state contracts more deterministically. Browser double-click and second-tab behavior therefore rely on tested backend concurrency plus manual UAT rather than browser automation.

The suite does not run a real Gemini five-step happy path. Doing so would consume quota and introduce provider, model, and network nondeterminism. The provider boundary and full pipeline behavior are instead exercised with controlled mocks.

Responsive and accessibility behavior has implementation-level semantics and component coverage, but there has not been a complete real-browser accessibility audit across breakpoints, keyboard paths, and assistive technologies.

## Manual Testing

The following behaviors were verified manually in the running application:

- Created a project using pasted book text.
- Created a project using a `.txt` upload.
- Completed the five pipeline steps in order.
- Refreshed the page during/after pipeline progress and confirmed completed results remained visible.
- Signed out and signed back in with the same email and confirmed the existing project resumed.
- Double-clicked the current-step action and confirmed only one step execution was accepted.
- Opened the same project in a second browser tab and confirmed duplicate execution was rejected / the existing running state was shown.
- Restarted the backend during a running step and confirmed the UI exposed the recovery action.
- Retried an interrupted/failed step and confirmed previously completed results were preserved.
- Checked the main screens at desktop and narrow viewport widths.
- Used keyboard navigation for primary controls and verified visible focus states.

These checks were manual browser verification and are not part of the automated test suite.

## Real test report

Run on 2026-08-14 through Git Bash on Windows. The command completed with exit code 0. ANSI color codes are omitted below; all result text and timings are from the real run.

```text
$ ./test.sh
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-8.4.2, pluggy-1.6.0
rootdir: C:\GitHub\gradion-book-illustration\backend
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.14.2
collected 53 items

tests\test_chapters_illustrations.py .........                           [ 16%]
tests\test_gemini_pipeline.py ..........                                 [ 35%]
tests\test_health.py .                                                   [ 37%]
tests\test_pipeline.py ...........                                       [ 58%]
tests\test_portraits.py .........                                        [ 75%]
tests\test_projects.py .........                                         [ 92%]
tests\test_session.py ....                                               [100%]

============================= 53 passed in 6.45s ==============================

> gradion-book-illustration-frontend@0.1.0 test
> vitest run

 RUN  v3.2.7 C:/GitHub/gradion-book-illustration/frontend

 ✓ src/App.test.tsx (19 tests) 1312ms

 Test Files  1 passed (1)
      Tests  19 passed (19)
   Start at  12:54:35
   Duration  3.83s (transform 206ms, setup 170ms, collect 477ms, tests 1.31s, environment 1.09s, prepare 282ms)
```

The requested frontend production check also completed successfully on
2026-08-14: `npm run build` ran `tsc -b && vite build`, transformed 30 modules,
and completed the Vite build in 1.45 seconds.
