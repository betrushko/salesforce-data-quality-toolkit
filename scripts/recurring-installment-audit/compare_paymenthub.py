"""Compare a PaymentHub review workbook with the local installment audit."""

import csv
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET


NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_xlsx(path):
    with zipfile.ZipFile(path) as archive:
        strings_xml = ET.fromstring(archive.read('xl/sharedStrings.xml'))
        strings = [''.join(node.itertext()) for node in strings_xml.findall('m:si', NS)]
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        for row in sheet.findall('.//m:sheetData/m:row', NS):
            cells = {}
            for cell in row.findall('m:c', NS):
                column = re.match(r'[A-Z]+', cell.attrib['r']).group()
                value_node = cell.find('m:v', NS)
                value = '' if value_node is None else value_node.text or ''
                if cell.attrib.get('t') == 's':
                    value = strings[int(value)]
                cells[column] = value
            yield row.attrib['r'], cells


def main(folder):
    folder = Path(folder)
    workbook = folder / 'OppsCountingMissmatch_20260810.xlsx'
    workbook_rows = list(read_xlsx(workbook))
    headers = workbook_rows[0][1]
    old = [{headers.get(column, column): value for column, value in cells.items()} for _, cells in workbook_rows[1:]]
    old = [row for row in old if row.get('Id')]
    with (folder / 'review-cases-auth.csv').open(encoding='utf-8-sig', newline='') as source:
        current = list(csv.DictReader(source))
    with (folder / 'duplicate-paid-groups-auth.csv').open(encoding='utf-8-sig', newline='') as source:
        groups = list(csv.DictReader(source))
    old_ids = {row['Id'] for row in old}
    current_ids = {row['Id'] for row in current}
    group_key = lambda row: (row['npe03__Recurring_Donation__c'], str(int(float(row['npsp__Recurring_Donation_Installment_Number__c']))))
    old_groups = {group_key(row) for row in old if row.get('npe03__Recurring_Donation__c') and row.get('npsp__Recurring_Donation_Installment_Number__c')}
    old_by_group = defaultdict(list)
    for row in old:
        if row.get('npe03__Recurring_Donation__c') and row.get('npsp__Recurring_Donation_Installment_Number__c'):
            old_by_group[group_key(row)].append(row)
    missing = [row for row in current if row['Id'] not in old_ids]
    for row in missing:
        row['GroupInPaymentHubWorkbook'] = 'Yes' if group_key(row) in old_groups else 'No'
    columns = list(current[0]) + ['GroupInPaymentHubWorkbook']
    with (folder / 'cases-not-in-paymenthub-workbook.csv').open('w', encoding='utf-8-sig', newline='') as dest:
        writer = csv.DictWriter(dest, fieldnames=columns)
        writer.writeheader()
        writer.writerows(missing)
    new_groups = [group for group in groups if (group['RecurringDonationId'], group['InstallmentNumber']) not in old_groups]
    with (folder / 'duplicate-groups-not-in-paymenthub-workbook.csv').open('w', encoding='utf-8-sig', newline='') as dest:
        writer = csv.DictWriter(dest, fieldnames=list(groups[0]))
        writer.writeheader()
        writer.writerows(new_groups)
    summary = {
        'workbook_rows_with_id': len(old),
        'workbook_unique_ids': len(old_ids),
        'workbook_stage_counts': dict(Counter(row.get('StageName', '') for row in old)),
        'audit_issue_rows': len(current),
        'exact_id_overlap': len(current_ids & old_ids),
        'audit_rows_absent_from_workbook': len(missing),
        'absent_rows_in_group_present_in_workbook': sum(row['GroupInPaymentHubWorkbook'] == 'Yes' for row in missing),
        'audit_duplicate_groups': len(groups),
        'audit_duplicate_groups_absent_from_workbook': len(new_groups),
        'workbook_rows_absent_from_audit': sum(row['Id'] not in current_ids for row in old),
        'workbook_field_groups': len(old_by_group),
        'workbook_field_group_size_distribution': dict(Counter(len(group) for group in old_by_group.values())),
    }
    (folder / 'workbook-only-ids.txt').write_text('\n'.join(sorted(old_ids - current_ids)) + '\n', encoding='utf-8')
    current_status_path = folder / 'workbook-only-current.csv'
    if current_status_path.exists():
        with current_status_path.open(encoding='utf-8-sig', newline='') as source:
            status_rows = {row['Id']: row for row in csv.DictReader(source)}
        old_by_id = {row['Id']: row for row in old}
        workbook_only = old_ids - current_ids
        summary['workbook_only_current_rows_found'] = len(status_rows)
        summary['workbook_only_capture_years'] = dict(Counter((status_rows[id].get('Fecha_de_captacion__c') or '')[:4] or 'blank' for id in workbook_only if id in status_rows))
        summary['workbook_only_field_number_changed'] = sum(
            str(int(float(old_by_id[id]['npsp__Recurring_Donation_Installment_Number__c']))) != str(int(float(status_rows[id]['npsp__Recurring_Donation_Installment_Number__c'])))
            for id in workbook_only if id in status_rows and old_by_id[id].get('npsp__Recurring_Donation_Installment_Number__c') and status_rows[id].get('npsp__Recurring_Donation_Installment_Number__c')
        )
        summary['workbook_only_name_changed'] = sum(old_by_id[id].get('Name') != status_rows[id].get('Name') for id in workbook_only if id in status_rows)
        summary['workbook_only_stage_changed'] = sum(old_by_id[id].get('StageName') != status_rows[id].get('StageName') for id in workbook_only if id in status_rows)
        summary['workbook_only_group_sizes'] = dict(Counter(len(old_by_group[group_key(old_by_id[id])]) for id in workbook_only))
        summary['workbook_only_field_name_mismatch'] = sum(
            bool(re.search(r'\s(\d+)\s*$', status_rows[id].get('Name', '')))
            and re.search(r'\s(\d+)\s*$', status_rows[id]['Name']).group(1) != str(int(float(status_rows[id]['npsp__Recurring_Donation_Installment_Number__c'])))
            for id in workbook_only if id in status_rows and status_rows[id].get('npsp__Recurring_Donation_Installment_Number__c')
        )
    (folder / 'paymenthub-comparison-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main(sys.argv[1])
