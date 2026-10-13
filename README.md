# Salesforce data quality toolkit

Deterministic preparation of cleanup candidates, phone normalization, first-channel attribution and recurring installment audits.

**Stack:** Python, PowerShell, Salesforce Flow.

## What this demonstrates

Reusable implementation patterns for data integration, validation and maintainable automation. This is a curated, generalized portfolio edition. It contains no production exports, deployment history, internal access inventory or credentials. API names and source labels have been generalized where needed; configure the matching schema in your own sandbox before use.

## Getting started

For Salesforce work, use Salesforce CLI and a dedicated sandbox with the required schema and NPSP dependencies. Inspect `manifest/package.xml`, then validate with `sf project deploy start --dry-run --manifest manifest/package.xml --target-org DEMO_SANDBOX`. This repository has not been deployed or tested against a live org during portfolio preparation. 

## Source map

- `README.md`
- `force-app/main/default/flows/Corrector_MobilePhone.flow-meta.xml`
- `manifest/package.xml`
- `scripts/canal-inicial-cleanup/prepare-contact-canal-inicial-updates.ps1`
- `scripts/phone-cleanup/prepare-contact-phone-updates.ps1`
- `scripts/recurring-contact-audit/analyze.py`
- `scripts/recurring-installment-audit/analyze.py`
- `scripts/recurring-installment-audit/analyze_sequence.py`
- `scripts/recurring-installment-audit/compare_paymenthub.py`
- `scripts/recurring-installment-audit/prepare-paymenthub-payment-review-updates.py`
- `sfdx-project.json`

## Validation

Python sources and JSON/XML configuration are checked locally. Apex test classes are retained where present; executing them requires a compatible sandbox. No Salesforce query, deployment, backfill or record mutation was performed during preparation. See [SECURITY.md](SECURITY.md) for data handling.
