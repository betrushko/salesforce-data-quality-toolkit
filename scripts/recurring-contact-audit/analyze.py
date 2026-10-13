"""Read-only local comparison of Opportunity and recurring-donation contacts."""
import argparse
import collections
import csv
import json
from pathlib import Path


def analyze(folder):
    folder = Path(folder)
    base = 'https://example.my.salesforce.com'
    pc = 'npsp__Primary_Contact__c'
    rd = 'npe03__Recurring_Donation__c'
    rc = 'npe03__Recurring_Donation__r.npe03__Contact__c'
    counts = collections.Counter()
    stages = collections.Counter()
    affected_rd = set()
    seen = set()
    rows = []
    null_rows = []
    with (folder / 'opportunities-source.csv').open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        assert {pc, rd, rc, 'Id'}.issubset(reader.fieldnames), reader.fieldnames
        for r in reader:
            oid = r['Id']
            assert oid and oid not in seen, 'Missing or duplicate Opportunity ID'
            seen.add(oid)
            assert r[rd], 'Opportunity without recurring donation outside scope'
            a, b = (r[k].strip() if r[k] not in ('#N/A', 'null') else '' for k in (pc, rc))
            if a and b:
                category = 'Contactos diferentes' if a[:15] != b[:15] else 'Coinciden'
            elif not a and not b:
                category = 'Ambos contactos vacios'
            elif not a:
                category = 'Primary Contact vacio'
            else:
                category = 'Contacto de donacion recurrente vacio'
            counts[category] += 1
            if category == 'Coinciden':
                continue
            item = {
                'Caso': category,
                'Oportunidad_ID': oid,
                'Oportunidad': r['Name'],
                'Etapa': r['StageName'],
                'Fecha_cierre': r['CloseDate'],
                'Importe': r['Amount'],
                'Contacto_oportunidad_ID': a,
                'Contacto_oportunidad': r['npsp__Primary_Contact__r.Name'],
                'Donacion_recurrente_ID': r[rd],
                'Donacion_recurrente': r['npe03__Recurring_Donation__r.Name'],
                'Contacto_donacion_recurrente_ID': b,
                'Contacto_donacion_recurrente': r['npe03__Recurring_Donation__r.npe03__Contact__r.Name'],
                'Oportunidad_ultima_modificacion': r['LastModifiedDate'],
                'Donacion_recurrente_ultima_modificacion': r['npe03__Recurring_Donation__r.LastModifiedDate'],
                'Enlace_oportunidad': base + oid,
                'Enlace_donacion_recurrente': base + r[rd],
                'Enlace_contacto_oportunidad': base + a if a else '',
                'Enlace_contacto_donacion_recurrente': base + b if b else '',
            }
            if category == 'Contactos diferentes':
                rows.append(item)
                affected_rd.add(r[rd])
                stages[r['StageName']] += 1
            else:
                null_rows.append(item)
    fields = ['Caso', 'Oportunidad_ID', 'Oportunidad', 'Etapa', 'Fecha_cierre', 'Importe',
              'Contacto_oportunidad_ID', 'Contacto_oportunidad', 'Donacion_recurrente_ID',
              'Donacion_recurrente', 'Contacto_donacion_recurrente_ID', 'Contacto_donacion_recurrente',
              'Oportunidad_ultima_modificacion', 'Donacion_recurrente_ultima_modificacion',
              'Enlace_oportunidad', 'Enlace_donacion_recurrente', 'Enlace_contacto_oportunidad',
              'Enlace_contacto_donacion_recurrente']
    for name, data in [('contactos-diferentes.csv', rows), ('contactos-vacios.csv', null_rows)]:
        data.sort(key=lambda x: (x['Donacion_recurrente_ID'], x['Fecha_cierre'], x['Oportunidad_ID']))
        with (folder / name).open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)
        with (folder / name).open(encoding='utf-8-sig', newline='') as handle:
            assert sum(1 for _ in csv.DictReader(handle)) == len(data)
    result = {'total_opportunities': len(seen), 'categories': dict(counts),
              'distinct_recurring_donations_with_mismatch': len(affected_rd),
              'mismatch_by_stage': dict(stages), 'duplicate_opportunity_ids': 0,
              'mismatch_rate_percent': round(100 * len(rows) / len(seen), 5) if seen else 0}
    assert sum(counts.values()) == len(seen)
    (folder / 'summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', help='Local ignored folder containing opportunities-source.csv')
    analyze(parser.parse_args().folder)
