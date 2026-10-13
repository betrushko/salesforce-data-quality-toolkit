"""Prepare held installment corrections using PaymentHub order evidence.

The input and outputs contain production identifiers and must stay under the
ignored work/ directory. This script performs no Salesforce or BigQuery calls.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


DONATION_FIELD = "npe03__Recurring_Donation__c"
NUMBER_FIELD = "npsp__Recurring_Donation_Installment_Number__c"
NAME_NUMBER = re.compile(r"(?<=\s)(\d+)\s*$")
NAME_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
RETRY_SUFFIX = re.compile(r"^(.*)-([1-9]\d{0,2})$")
AUTH_TIMESTAMP = re.compile(r"^DEMO-FH(?:-RD)?-(\d{14})-")


def parse_number(value: str) -> int | None:
    if not value:
        return None
    number = float(value)
    return int(number) if number.is_integer() else None


def auth_base(value: str) -> str:
    match = RETRY_SUFFIX.match(value or "")
    return match.group(1) if match else (value or "")


def auth_datetime(value: str) -> datetime | None:
    match = AUTH_TIMESTAMP.match(value or "")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S") if match else None


def paymenthub_completed_date(value: str):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def replace_name_number(name: str, expected: int) -> str:
    match = NAME_NUMBER.search(name or "")
    if not match:
        return ""
    return f"{name[:match.start(1)]}{expected}{name[match.end(1):]}"


def replace_name_date(name: str, target_date) -> str:
    match = NAME_DATE.search(name or "")
    if not match:
        return ""
    day = f"{target_date.day:02d}" if len(match.group(1)) == 2 else str(target_date.day)
    month = f"{target_date.month:02d}" if len(match.group(2)) == 2 else str(target_date.month)
    replacement = f"{day}/{month}/{target_date.year:04d}"
    return f"{name[:match.start()]}{replacement}{name[match.end():]}"


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("salesforce_csv", type=Path)
    parser.add_argument("held_donations_csv", type=Path)
    parser.add_argument("paymenthub_results_json", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    args.output_directory.mkdir(parents=True, exist_ok=True)

    with args.salesforce_csv.open(encoding="utf-8-sig", newline="") as source:
        salesforce_rows = list(csv.DictReader(source))
    with args.held_donations_csv.open(encoding="utf-8-sig", newline="") as source:
        held_ids = {row["RecurringDonationId"] for row in csv.DictReader(source)}
    paymenthub_rows = json.loads(args.paymenthub_results_json.read_text(encoding="utf-8-sig"))
    if isinstance(paymenthub_rows, dict):
        paymenthub_rows = [paymenthub_rows]

    paymenthub_by_number: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in paymenthub_rows:
        paymenthub_by_number[row["donation_number"]].append(row)

    held_rows = [row for row in salesforce_rows if row[DONATION_FIELD] in held_ids]
    by_donation: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in held_rows:
        row["_current_number"] = parse_number(row[NUMBER_FIELD])
        row["_auth_base"] = auth_base(row.get("Auth__c", ""))
        row["_auth_datetime"] = auth_datetime(row["_auth_base"])
        by_donation[row[DONATION_FIELD]].append(row)

    review_rows: list[dict[str, object]] = []
    manual_rows: list[dict[str, object]] = []
    paid_evidence_rows = 0

    for donation_id, donation_rows in by_donation.items():
        paid = [row for row in donation_rows if row["StageName"] == "Closed Won"]
        for row in paid:
            evidence = paymenthub_by_number.get(row["_auth_base"], [])
            if len(evidence) != 1:
                raise ValueError(
                    f"Expected one PaymentHub order for paid Opportunity {row['Id']}; found {len(evidence)}"
                )
            if (
                evidence[0].get("Order_Status") != "Completed"
                or evidence[0].get("Payment_State") != "Completed"
            ):
                raise ValueError(f"PaymentHub order is not completed for paid Opportunity {row['Id']}")
            if row["_auth_datetime"] is None:
                raise ValueError(f"Unparseable PaymentHub donation number for paid Opportunity {row['Id']}")
            row["_paymenthub_completed_date"] = paymenthub_completed_date(
                evidence[0].get("completed_date", "")
            )
            if row["_paymenthub_completed_date"] is None:
                raise ValueError(f"Missing PaymentHub completed_date for paid Opportunity {row['Id']}")
            paid_evidence_rows += 1

        paid.sort(key=lambda row: (row["_auth_datetime"], row["CreatedDate"], row["Id"]))
        month_counts = Counter(
            (row["_auth_datetime"].year, row["_auth_datetime"].month) for row in paid
        )
        if any(count > 1 for count in month_counts.values()):
            raise ValueError(f"Multiple original PaymentHub orders in one month for donation {donation_id}")

        expected_paid = {row["Id"]: expected for expected, row in enumerate(paid, start=1)}
        paid_number_by_auth = {
            row["_auth_base"]: expected_paid[row["Id"]] for row in paid
        }

        for row in donation_rows:
            expected: int | None = None
            proposed_close_date = row["CloseDate"]
            reason = ""
            if row["Id"] in expected_paid:
                expected = expected_paid[row["Id"]]
                proposed_close_date = row["_paymenthub_completed_date"].isoformat()
                reason = "paymenthub_completed_order_sequence"
            elif row["StageName"] == "Rechazada" and row["_auth_base"] in paid_number_by_auth:
                expected = paid_number_by_auth[row["_auth_base"]]
                reason = "rejected_matches_completed_paymenthub_order"

            proposed_name = row["Name"]
            if expected is not None:
                numbered = replace_name_number(proposed_name, expected)
                if not numbered:
                    manual_rows.append(
                        {
                            "Id": row["Id"],
                            "RecurringDonationId": donation_id,
                            "Reason": "missing_final_installment_number",
                        }
                    )
                else:
                    proposed_name = numbered
            if row["Id"] in expected_paid:
                dated = replace_name_date(proposed_name, row["_auth_datetime"].date())
                if not dated:
                    manual_rows.append(
                        {
                            "Id": row["Id"],
                            "RecurringDonationId": donation_id,
                            "Reason": "missing_name_date",
                        }
                    )
                else:
                    proposed_name = dated

            field_change = expected is not None and row["_current_number"] != expected
            close_date_change = proposed_close_date != row["CloseDate"]
            name_change = proposed_name != row["Name"]
            if field_change or close_date_change or name_change:
                review_rows.append(
                    {
                        "Id": row["Id"],
                        "RecurringDonationId": donation_id,
                        "StageName": row["StageName"],
                        "AuthMatchBase": row["_auth_base"],
                        "Reason": reason,
                        "CurrentInstallment": row["_current_number"],
                        "ProposedInstallment": expected,
                        "FieldChange": "Yes" if field_change else "No",
                        "CurrentCloseDate": row["CloseDate"],
                        "ProposedCloseDate": proposed_close_date,
                        "CloseDateChange": "Yes" if close_date_change else "No",
                        "CurrentName": row["Name"],
                        "ProposedName": proposed_name,
                        "NameChange": "Yes" if name_change else "No",
                    }
                )

    if manual_rows:
        raise ValueError(f"Manual name review required for {len(manual_rows)} rows")

    number_updates = [
        {"Id": row["Id"], NUMBER_FIELD: row["ProposedInstallment"]}
        for row in review_rows
        if row["FieldChange"] == "Yes"
    ]
    close_date_updates = [
        {"Id": row["Id"], "CloseDate": row["ProposedCloseDate"]}
        for row in review_rows
        if row["CloseDateChange"] == "Yes"
    ]
    name_updates = [
        {"Id": row["Id"], "Name": row["ProposedName"]}
        for row in review_rows
        if row["NameChange"] == "Yes"
    ]

    for updates in (number_updates, close_date_updates, name_updates):
        assert len(updates) == len({row["Id"] for row in updates})
    assert all(row["Name"] and len(row["Name"]) <= 120 for row in name_updates)

    write_csv(args.output_directory / "held-installment-number-updates.csv", number_updates, ["Id", NUMBER_FIELD])
    write_csv(args.output_directory / "held-close-date-updates.csv", close_date_updates, ["Id", "CloseDate"])
    write_csv(args.output_directory / "held-opportunity-name-updates.csv", name_updates, ["Id", "Name"])
    write_csv(
        args.output_directory / "held-production-update-review.csv",
        review_rows,
        [
            "Id",
            "RecurringDonationId",
            "StageName",
            "AuthMatchBase",
            "Reason",
            "CurrentInstallment",
            "ProposedInstallment",
            "FieldChange",
            "CurrentCloseDate",
            "ProposedCloseDate",
            "CloseDateChange",
            "CurrentName",
            "ProposedName",
            "NameChange",
        ],
    )

    summary = {
        "held_donations": len(held_ids),
        "salesforce_rows_in_held_donations": len(held_rows),
        "paid_rows_with_unique_completed_paymenthub_evidence": paid_evidence_rows,
        "distinct_changed_rows": len({row["Id"] for row in review_rows}),
        "number_update_rows": len(number_updates),
        "close_date_update_rows": len(close_date_updates),
        "name_update_rows": len(name_updates),
        "paid_number_updates": sum(
            row["FieldChange"] == "Yes" and row["StageName"] == "Closed Won"
            for row in review_rows
        ),
        "rejected_number_updates": sum(
            row["FieldChange"] == "Yes" and row["StageName"] == "Rechazada"
            for row in review_rows
        ),
        "manual_review_rows": len(manual_rows),
    }
    (args.output_directory / "held-production-update-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
