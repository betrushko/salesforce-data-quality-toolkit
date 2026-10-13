"""Audit Closed Won installment order for affected recurring donations.

The input is a full-history Opportunity export for a bounded set of recurring
donations. Row-level outputs contain production identifiers and must be written
under the ignored work/ directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


NUMBER_FIELD = "npsp__Recurring_Donation_Installment_Number__c"
DONATION_FIELD = "npe03__Recurring_Donation__c"
NAME_NUMBER = re.compile(r"(?<=\s)(\d+)\s*$")


def parse_number(value: str) -> int | None:
    if not value:
        return None
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else None


def month_distance(first: date, second: date) -> int:
    return (second.year - first.year) * 12 + second.month - first.month


def proposed_name(name: str, expected: int) -> str:
    match = NAME_NUMBER.search(name or "")
    if not match:
        return ""
    return f"{name[:match.start(1)]}{expected}{name[match.end(1):]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()

    args.output_directory.mkdir(parents=True, exist_ok=True)
    with args.input_csv.open(encoding="utf-8-sig", newline="") as source:
        source_rows = list(csv.DictReader(source))

    paid_rows: list[dict[str, str]] = []
    by_donation: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        if row["StageName"] != "Closed Won":
            continue
        row["_close_date"] = date.fromisoformat(row["CloseDate"])
        row["_field_number"] = parse_number(row[NUMBER_FIELD])
        match = NAME_NUMBER.search(row.get("Name", ""))
        row["_name_number"] = int(match.group(1)) if match else None
        paid_rows.append(row)
        by_donation[row[DONATION_FIELD]].append(row)

    donation_review: list[dict[str, object]] = []
    opportunity_review: list[dict[str, object]] = []
    safe_updates: list[dict[str, object]] = []
    classification_counts: Counter[str] = Counter()
    timing_counts: Counter[str] = Counter()

    for donation_id, rows in by_donation.items():
        rows.sort(key=lambda row: (row["_close_date"], row["CreatedDate"], row["Id"]))
        transitions = [
            month_distance(previous["_close_date"], current["_close_date"])
            for previous, current in zip(rows, rows[1:])
        ]
        same_month = any(distance == 0 for distance in transitions)
        first_late_skip = bool(
            transitions
            and transitions[0] == 2
            and rows[0]["_close_date"].day >= 20
        )
        invalid_gap = any(
            distance != 1 and not (index == 0 and first_late_skip)
            for index, distance in enumerate(transitions)
        )

        field_changes = 0
        name_changes = 0
        changed_rows = 0
        for expected, row in enumerate(rows, start=1):
            field_change = row["_field_number"] != expected
            name_change = row["_name_number"] != expected
            field_changes += int(field_change)
            name_changes += int(name_change)
            changed_rows += int(field_change or name_change)

        if same_month:
            classification = "payment_review_required"
        elif invalid_gap:
            classification = "date_gap_review_required"
        elif changed_rows:
            classification = "correction_candidate"
        else:
            classification = "coherent_no_action"
        classification_counts[classification] += 1

        first_gap = transitions[0] if transitions else None
        first_day = rows[0]["_close_date"].day
        if first_gap == 1:
            timing_class = "next_month"
        elif first_gap == 2 and first_day >= 23:
            timing_class = "paymenthub_late_skip_strong"
        elif first_gap == 2 and first_day >= 20:
            timing_class = "paymenthub_late_skip_boundary"
        elif first_gap == 0:
            timing_class = "same_month"
        else:
            timing_class = "gap_review"
        timing_counts[timing_class] += 1

        donation_review.append(
            {
                "RecurringDonationId": donation_id,
                "Classification": classification,
                "TimingClass": timing_class,
                "ClosedWonCount": len(rows),
                "FirstClosedWonDate": rows[0]["CloseDate"],
                "FirstClosedWonDay": first_day,
                "FirstToSecondMonthGap": first_gap,
                "FieldChanges": field_changes,
                "NameChanges": name_changes,
                "ChangedRows": changed_rows,
            }
        )

        for expected, row in enumerate(rows, start=1):
            new_name = proposed_name(row["Name"], expected)
            field_change = row["_field_number"] != expected
            name_change = row["_name_number"] != expected
            opportunity_review.append(
                {
                    "Classification": classification,
                    "Id": row["Id"],
                    "RecurringDonationId": donation_id,
                    "CloseDate": row["CloseDate"],
                    "CreatedDate": row["CreatedDate"],
                    "CurrentFieldInstallment": row["_field_number"],
                    "CurrentNameInstallment": row["_name_number"],
                    "ExpectedInstallment": expected,
                    "FieldChange": "Yes" if field_change else "No",
                    "NameChange": "Yes" if name_change else "No",
                    "CurrentName": row["Name"],
                    "ProposedName": new_name,
                    "NameRequiresManualConstruction": "Yes" if name_change and not new_name else "No",
                    "Auth__c": row.get("Auth__c", ""),
                }
            )
            if classification == "correction_candidate" and (field_change or name_change):
                safe_updates.append(
                    {
                        "Id": row["Id"],
                        NUMBER_FIELD: expected if field_change else row[NUMBER_FIELD],
                        "Name": new_name if name_change and new_name else row["Name"],
                        "FieldChange": "Yes" if field_change else "No",
                        "NameChange": "Yes" if name_change else "No",
                        "NameRequiresManualConstruction": "Yes" if name_change and not new_name else "No",
                    }
                )

    duplicate_groups: dict[tuple[str, int | None], list[dict[str, str]]] = defaultdict(list)
    for row in paid_rows:
        duplicate_groups[(row[DONATION_FIELD], row["_field_number"])].append(row)
    duplicates = [rows for rows in duplicate_groups.values() if len(rows) > 1]
    same_auth_duplicates = [
        rows for rows in duplicates if len({row.get("Auth__c", "") for row in rows}) == 1
    ]

    summary = {
        "source_rows": len(source_rows),
        "closed_won_rows": len(paid_rows),
        "recurring_donations": len(by_donation),
        "classification_counts": dict(sorted(classification_counts.items())),
        "timing_counts": dict(sorted(timing_counts.items())),
        "current_duplicate_installment_groups": len(duplicates),
        "current_duplicate_installment_rows": sum(len(rows) for rows in duplicates),
        "current_same_auth_duplicate_groups": len(same_auth_duplicates),
        "safe_candidate_update_rows": len(safe_updates),
        "safe_candidate_field_changes": sum(row["FieldChange"] == "Yes" for row in safe_updates),
        "safe_candidate_name_changes": sum(row["NameChange"] == "Yes" for row in safe_updates),
        "safe_candidate_manual_name_construction": sum(
            row["NameRequiresManualConstruction"] == "Yes" for row in safe_updates
        ),
        "method": {
            "paid_stage": "Closed Won",
            "order": ["CloseDate", "CreatedDate", "Id"],
            "expected_number": "1-based rank among Closed Won Opportunities",
            "paymenthub_hint": "A two-month first-to-second gap is accepted when the first payment day is 20 or later; days 20-22 remain a boundary clue.",
        },
    }

    def write_csv(filename: str, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        with (args.output_directory / filename).open("w", encoding="utf-8-sig", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write_csv("donation-sequence-review.csv", donation_review)
    write_csv("opportunity-sequence-review.csv", opportunity_review)
    write_csv("proposed-safe-updates-review-only.csv", safe_updates)
    (args.output_directory / "sequence-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
