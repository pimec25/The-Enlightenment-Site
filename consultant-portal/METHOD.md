# PIMEC Method workspace

Source: PIMEC Method Implementation Guide, 2 October 2026, version 1.0.

Open `/consultant/method` from the Consultant analysis home. Existing dashboard SSO and legacy Consultant authentication protect the page, schema, and review endpoint.

## Workflow mapping

| Guide stage | Collections | Review outputs |
| --- | --- | --- |
| Frame and align | Charter, responsibilities, baseline definitions, review rhythm | Charter and six-stage readiness |
| Diagnose and quantify | Data requests/checks, observations, cause hypotheses, opportunities | Opportunity low/high ranges; separate Pareto groups; processing/waiting analysis |
| Prioritize and choose | Initiatives, six scores, resources, approval/deferral, milestones | Ranked roadmap; impact and feasibility means; missing execution requirements |
| Execute and mobilize | Actions, pilot conditions/results, daily management | Baseline changes, pilot comparison, overdue and evidence checks |
| Govern and validate | Benefit stages, Finance review, allocations, decision log | Separate validated totals by category/unit/period; duplicate claims and overdue decisions |
| Transfer and sustain | Standard work, capability demonstrations/backups, controls, audits, acceptance | Handover evidence pack and suggested 30/60/90-day dates |

Review is deterministic. It does not call an LLM or independently perform site observation, verify source evidence, approve initiatives, or grant Finance/sponsor acceptance. Those decisions are attributed to the named reviewer. Data validation findings are entered by the consultant; the existing Excel analysis remains separately available. The workspace does not create Client Portal projects, upload resources or send messages; it collects reference locations and session plans.

## Data and persistence

Entries live only in page memory. No localStorage, server database, cookies, or permanent server file stores case content. Download a version 1 JSON case to retain work; import validates it on the authenticated server. The review request is processed transiently, protected by the existing session and CSRF token. Maximum case size is 2 MB, 200 records per register, 8,000 characters per field. Store downloaded cases and source evidence in approved client storage.

The management review includes a printable evidence pack and JSON download. Every edit hides the previous review to avoid exporting stale calculations. Navigation warns about unsaved edits. No data is sent to third-party model providers by this feature.

## Calculation basis

Opportunity low/high = quantity low/high × relevant rate low/high. Negative or reversed ranges are excluded. Pareto uses low estimates within identical category, case-insensitive unit and period; unsubstantiated, duplicate-ID or overlapping opportunities are excluded. It reports the observed number of items reaching 80%, not an assumed 80/20 distribution.

Priority = equal-weight mean of six 1–5 scores; impact and feasibility each use three criteria. This does not auto-select initiatives. KPI/pilot change = actual minus baseline; percentage uses absolute baseline, with no percentage for zero baseline. Processing plus waiting yields observed elapsed time only, not a plant-wide lead-time model.

Benefit totals include only records marked Validated with evidence, calculation, owner/reviewer/date, financial Finance reviewer, and overlap allocation when relevant. A shared overlap group is counted once per category/unit/period; record the consolidated shared result in one row. Identical initiative/name/category/unit/period records count once. Different labels or undisclosed interactions cannot be detected automatically. Achieved-to-date and entered estimated annualized values stay separate; annualized amounts are not inferred or confirmed by the agent. Cash release, capacity, service and risk are never rolled into profit.

## Verification

Run `python -m pytest tests/test_method.py tests/test_sso.py tests/test_routes.py -q -p no:cacheprovider` from this directory. Run `node --check static/method.js` for JavaScript syntax.
