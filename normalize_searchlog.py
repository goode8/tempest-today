"""
One-shot script to normalize existing SearchLog rows.
Run with: python manage.py shell < normalize_searchlog.py
"""
import re
from core.models import SearchLog


def _normalize_query(q):
    q = q.lower().strip()
    q = re.sub(r'[.,;:]+\s*', ' ', q)
    q = re.sub(r'\s+', ' ', q)
    q = q.strip()
    return q


# Group all rows by their normalized form
groups = {}
for row in SearchLog.objects.all():
    key = _normalize_query(row.query)
    groups.setdefault(key, []).append(row)

merged = 0
for key, rows in groups.items():
    if len(rows) == 1:
        # Just rename if the query isn't already normalized
        row = rows[0]
        if row.query != key:
            row.query = key
            row.save(update_fields=['query'])
            merged += 1
        continue

    # Multiple rows map to the same normalized form — keep the one with the
    # highest count, add up the rest, delete the duplicates.
    rows.sort(key=lambda r: r.count, reverse=True)
    keeper, dupes = rows[0], rows[1:]
    total = sum(r.count for r in dupes)
    for dupe in dupes:
        dupe.delete()
    keeper.query = key
    keeper.count += total
    keeper.save(update_fields=['query', 'count'])
    merged += len(dupes)
    print(f"  merged {[r.query for r in dupes]} → '{key}' (count now {keeper.count})")

print(f"\nDone. {merged} rows merged/renamed.")
