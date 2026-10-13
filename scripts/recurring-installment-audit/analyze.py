import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def auth_base(value):
    return re.sub(r'-\d+$', '', (value or '').strip())


def all_same_nonblank(values):
    return 'Unknown' if any(not value for value in values) else ('Yes' if len(set(values)) == 1 else 'No')


def main(folder):
    folder = Path(folder)
    with (folder / 'opportunities-source.csv').open(encoding='utf-8-sig', newline='') as source:
        rows = list(csv.DictReader(source))
    number_field = 'npsp__Recurring_Donation_Installment_Number__c'
    donation_field = 'npe03__Recurring_Donation__c'
    for row in rows:
        match = re.search(r'\s(\d+)\s*$', row['Name'])
        row['NameInstallment'] = match.group(1) if match else ''
        raw = row[number_field]
        row['FieldInstallment'] = str(int(float(raw))) if raw and float(raw).is_integer() else raw
        row['Issue'] = ''
    paid = [r for r in rows if r['StageName'] == 'Closed Won']
    by_field = defaultdict(list)
    by_name = defaultdict(list)
    for row in paid:
        if row['FieldInstallment']:
            by_field[(row[donation_field], row['FieldInstallment'])].append(row)
        if row['NameInstallment']:
            by_name[(row[donation_field], row['NameInstallment'])].append(row)
    duplicate_field = {key: group for key, group in by_field.items() if len(group) > 1}
    duplicate_name = {key: group for key, group in by_name.items() if len(group) > 1}
    for group in duplicate_field.values():
        exact_values = [(r['Auth__c'] or '').strip() for r in group]
        base_values = [auth_base(value) for value in exact_values]
        for row in group:
            others = [r for r in group if r['Id'] != row['Id']]
            exact = (row['Auth__c'] or '').strip()
            base = auth_base(exact)
            row['OtherPaidAuthValues'] = ' | '.join((r['Auth__c'] or '').strip() for r in others)
            row['AuthMatchesAnyOtherPaidExact'] = 'Unknown' if not exact or any(not r['Auth__c'] for r in others) else ('Yes' if any(exact == r['Auth__c'].strip() for r in others) else 'No')
            row['AuthBaseMatchesAnyOtherPaid'] = 'Unknown' if not base or any(not r['Auth__c'] for r in others) else ('Yes' if any(base == auth_base(r['Auth__c']) for r in others) else 'No')
            row['PaidGroupAuthAllSameExact'] = all_same_nonblank(exact_values)
            row['PaidGroupAuthBaseAllSame'] = all_same_nonblank(base_values)
    issue_rows = {}
    for row in rows:
        issues = []
        if not row['FieldInstallment']:
            issues.append('missing_field_number')
        if not row['NameInstallment']:
            issues.append('missing_name_suffix')
        if row['FieldInstallment'] and row['NameInstallment'] and row['FieldInstallment'] != row['NameInstallment']:
            issues.append('field_name_mismatch')
        if row['StageName'] == 'Closed Won':
            if (row[donation_field], row['FieldInstallment']) in duplicate_field:
                issues.append('duplicate_paid_field_number')
            if (row[donation_field], row['NameInstallment']) in duplicate_name:
                issues.append('duplicate_paid_name_suffix')
        if issues:
            row['Issue'] = ';'.join(issues)
            issue_rows[row['Id']] = row
    fields = ['Issue', 'Id', donation_field, 'Name', 'StageName', 'CloseDate', 'Fecha_de_captacion__c', number_field, 'FieldInstallment', 'NameInstallment', 'Auth__c', 'OtherPaidAuthValues', 'AuthMatchesAnyOtherPaidExact', 'AuthBaseMatchesAnyOtherPaid', 'PaidGroupAuthAllSameExact', 'PaidGroupAuthBaseAllSame']
    with (folder / 'review-cases-auth.csv').open('w', encoding='utf-8-sig', newline='') as dest:
        writer = csv.DictWriter(dest, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(sorted(issue_rows.values(), key=lambda r: (r[donation_field], r['CloseDate'], r['Id'])))
    with (folder / 'duplicate-paid-groups-auth.csv').open('w', encoding='utf-8-sig', newline='') as dest:
        writer = csv.DictWriter(dest, fieldnames=['RecurringDonationId', 'InstallmentNumber', 'PaidOpportunities', 'OpportunityIds', 'CloseDates', 'Names', 'AuthValues', 'AuthAllSameExact', 'AuthBaseValues', 'AuthBaseAllSame'])
        writer.writeheader()
        for (donation_id, installment), group in sorted(duplicate_field.items()):
            group = sorted(group, key=lambda r: (r['CloseDate'], r['Id']))
            writer.writerow({
                'RecurringDonationId': donation_id,
                'InstallmentNumber': installment,
                'PaidOpportunities': len(group),
                'OpportunityIds': ' | '.join(r['Id'] for r in group),
                'CloseDates': ' | '.join(r['CloseDate'] for r in group),
                'Names': ' | '.join(r['Name'] for r in group),
                'AuthValues': ' | '.join((r['Auth__c'] or '').strip() for r in group),
                'AuthAllSameExact': all_same_nonblank([(r['Auth__c'] or '').strip() for r in group]),
                'AuthBaseValues': ' | '.join(auth_base(r['Auth__c']) for r in group),
                'AuthBaseAllSame': all_same_nonblank([auth_base(r['Auth__c']) for r in group]),
            })
    summary = {
        'source_rows': len(rows),
        'unique_source_ids': len({r['Id'] for r in rows}),
        'recurring_donations': len({r[donation_field] for r in rows}),
        'stage_counts': dict(Counter(r['StageName'] for r in rows)),
        'issue_rows': len(issue_rows),
        'issue_counts': dict(Counter(issue for r in issue_rows.values() for issue in r['Issue'].split(';'))),
        'duplicate_paid_field_groups': len(duplicate_field),
        'duplicate_paid_name_groups': len(duplicate_name),
        'rejected_with_auth_retry_suffix': sum(bool(re.search(r'-\d+\s*$', r['Auth__c'] or '')) for r in rows if r['StageName'] == 'Rechazada'),
        'duplicate_paid_groups_auth_exact': dict(Counter(all_same_nonblank([(r['Auth__c'] or '').strip() for r in group]) for group in duplicate_field.values())),
        'duplicate_paid_groups_auth_base': dict(Counter(all_same_nonblank([auth_base(r['Auth__c']) for r in group]) for group in duplicate_field.values())),
    }
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1])
