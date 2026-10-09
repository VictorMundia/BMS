from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from inventory.utils import SMSError, log_action, send_sms
from inventory.views import _business_date, _dashboard_context, _parse_date


def _money(value):
    return f"{value:,.0f}"


def build_daily_summary(date):
    ctx = _dashboard_context(date)
    lines = [f"BMS {date:%a} {date.day} {date:%b}"]
    for b in ctx['per_branch']:
        name = b['butchery'].name
        if not b['submitted']:
            lines.append(f"{name}: NOT ENTERED")
            continue
        if b['is_closed'] and not b['entries']:
            lines.append(f"{name}: No trading")
            continue
        variance = b['cash_variance']
        if variance is None:
            cash = 'Cash not counted'
        elif abs(variance) < 1:
            cash = 'Cash OK'
        else:
            cash = f"Cash diff {'+' if variance > 0 else ''}{_money(variance)}"
        lines.append(
            f"{name}: Sales {_money(b['revenue'])} | Mpesa {_money(b['mpesa_amount'])} "
            f"| Exp {_money(b['expenses'])} | {cash}"
        )
    totals = ctx['totals']
    lines.append(f"TOTAL Sales {_money(totals['revenue'])} | Net profit {_money(totals['net_profit'])}")
    return '\n'.join(lines)


class Command(BaseCommand):
    help = "Send the owner an SMS summary of a business day (default: yesterday)."

    def add_arguments(self, parser):
        parser.add_argument('--date', help='Business day to summarise (YYYY-MM-DD)')
        parser.add_argument('--dry-run', action='store_true', help='Print the message without sending')

    def handle(self, *args, **options):
        if options['date']:
            date = _parse_date(options['date'])
            if date is None:
                raise CommandError('Invalid --date, use YYYY-MM-DD.')
        else:
            date = _business_date() - timedelta(days=1)

        message = build_daily_summary(date)
        if options['dry_run']:
            self.stdout.write(message)
            self.stdout.write(f"\n({len(message)} characters, would go to {len(settings.SMS_RECIPIENTS)} recipient(s))")
            return

        recipients = settings.SMS_RECIPIENTS
        try:
            results = send_sms(message, recipients)
        except SMSError as e:
            log_action(None, 'SMS_SUMMARY_FAILED', 'DailyBranchSummary', 0, f"Summary for {date}: {e}")
            raise CommandError(str(e))
        cost = ', '.join(str(r.get('cost', '')) for r in results)
        log_action(
            None, 'SMS_SUMMARY_SENT', 'DailyBranchSummary', 0,
            f"Summary for {date} sent to {len(results)} recipient(s). Cost: {cost}",
        )
        self.stdout.write(self.style.SUCCESS(f"Summary for {date} sent to {len(results)} recipient(s)."))
