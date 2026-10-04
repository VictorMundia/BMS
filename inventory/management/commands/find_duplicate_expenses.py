from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from inventory.models import Expense


class Command(BaseCommand):
    help = (
        'List expenses that look duplicated (same branch, date, description and amount). '
        'Pass --delete to remove the extra copies, keeping the earliest one.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='Delete the duplicates')

    def handle(self, *args, **options):
        groups = (
            Expense.objects.values('butchery_id', 'butchery__name', 'date', 'description', 'amount')
            .annotate(n=Count('id')).filter(n__gt=1).order_by('date')
        )
        extra_ids = []
        for g in groups:
            ids = list(
                Expense.objects.filter(
                    butchery_id=g['butchery_id'], date=g['date'],
                    description=g['description'], amount=g['amount'],
                ).order_by('pk').values_list('pk', flat=True)
            )
            extra_ids.extend(ids[1:])
            self.stdout.write(
                f"{g['date']}  {g['butchery__name']}  {g['description'][:40]!r}  "
                f"{g['amount']}  x{g['n']}  (extra ids: {ids[1:]})"
            )

        if not extra_ids:
            self.stdout.write(self.style.SUCCESS('No duplicate expenses found.'))
            return
        self.stdout.write(f'{len(extra_ids)} extra expense record(s) found.')
        if not options['delete']:
            self.stdout.write('Dry run only. Re-run with --delete to remove them.')
            return
        with transaction.atomic():
            deleted, _ = Expense.objects.filter(pk__in=extra_ids).delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} duplicate expense(s).'))
