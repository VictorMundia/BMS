import io
import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from .management.commands.send_daily_summary import build_daily_summary
from .models import (
    AuditLog, BuyingPrice, Butchery, DailyBranchSummary, DailyStock, DailyTransfer, DatePermission,
    Expense, MeatCategory, MeatProduct, School, SchoolDelivery, SchoolPrice, Staff,
)
from .views import _business_date, _entry_status_grid


class DailyEntryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser('owner', password='pw-owner-123')
        self.butcher = User.objects.create_user('butcher', password='pw-butcher-123')
        self.branch = Butchery.objects.create(name='Main', location='Town', phone='1')
        self.other = Butchery.objects.create(name='Other', location='Village', phone='2')
        self.today = _business_date()
        Staff.objects.create(
            user=self.butcher, butchery=self.branch, role='BUTCHER', phone='3',
            id_number='ID1', date_hired=self.today,
        )
        cat = MeatCategory.objects.create(name='Beef')
        self.beef = MeatProduct.objects.create(
            category=cat, name='Beef', buying_price=Decimal('500'),
            selling_price=Decimal('700'), butchery=self.branch,
        )
        self.url = reverse('daily_stock_entry')

    def post(self, user, date=None, branch=None, received='10', wastage='0', closing='4', **extra):
        self.client.force_login(user)
        data = {
            'form_version': '2',
            'butchery': (branch or self.branch).id,
            'date': (date or self.today).isoformat(),
            f'received_{self.beef.id}': received,
            f'wastage_{self.beef.id}': wastage,
            f'closing_{self.beef.id}': closing,
            'mpesa_amount': '0',
        }
        data.update(extra)
        return self.client.post(self.url, data)

    def row(self, date=None):
        return DailyStock.objects.get(product=self.beef, date=date or self.today)

    def transfer(self, qty, to=None, tid=''):
        pid = self.beef.id
        return {
            f'transfer_id_{pid}': [tid],
            f'transfer_to_{pid}': [str((to or self.other).id)],
            f'transfer_quantity_{pid}': [qty],
        }

    def test_sold_and_revenue(self):
        self.post(self.butcher)
        row = self.row()
        self.assertEqual(row.sold, Decimal('6'))
        self.assertEqual(row.revenue, Decimal('4200'))
        self.assertEqual(row.gross_profit, Decimal('1200'))

    def test_transfers_out_are_not_counted_as_sold(self):
        self.post(self.butcher, **self.transfer('2'))
        self.assertEqual(self.row().sold, Decimal('4'))
        self.assertEqual(self.row().revenue, Decimal('2800'))

    def test_resaving_does_not_duplicate_transfers(self):
        self.post(self.butcher, **self.transfer('2'))
        t = DailyTransfer.objects.get()
        self.post(self.butcher, **self.transfer('3', tid=str(t.pk)))
        t.refresh_from_db()
        self.assertEqual(DailyTransfer.objects.count(), 1)
        self.assertEqual(t.quantity, Decimal('3'))

    def test_transfer_to_own_branch_rejected(self):
        resp = self.post(self.butcher, **self.transfer('2', to=self.branch))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(DailyTransfer.objects.exists())

    def test_transfer_more_than_available_rejected(self):
        resp = self.post(self.butcher, **self.transfer('11'))
        self.assertEqual(resp.status_code, 400)

    def test_recorded_buying_price_used_for_cost(self):
        BuyingPrice.objects.create(
            product=self.beef, date=self.today, buying_price_per_kg=Decimal('550'),
            quantity_received=Decimal('10'),
        )
        self.post(self.butcher)
        self.assertEqual(self.row().cost_of_sales, Decimal('3300'))

    def test_price_change_does_not_rewrite_past_revenue(self):
        yesterday = self.today - timedelta(days=1)
        self.post(self.owner, date=yesterday)
        MeatProduct.objects.filter(pk=self.beef.pk).update(selling_price=Decimal('900'))
        self.post(self.owner, date=yesterday, closing='5')
        self.assertEqual(self.row(yesterday).revenue, Decimal('3500'))

    def test_resaving_does_not_duplicate_expenses(self):
        self.post(self.butcher, exp_id=[''], exp_description=['Charcoal'], exp_amount=['200'], exp_category=[''])
        exp = Expense.objects.get()
        self.post(self.butcher, exp_id=[str(exp.pk)], exp_description=['Charcoal'], exp_amount=['250'], exp_category=[''])
        exp.refresh_from_db()
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(exp.amount, Decimal('250'))

    def test_removed_expense_is_deleted(self):
        self.post(self.butcher, exp_id=[''], exp_description=['Charcoal'], exp_amount=['200'], exp_category=[''])
        self.post(self.butcher)
        self.assertEqual(Expense.objects.count(), 0)

    def test_old_form_without_version_only_appends(self):
        self.post(self.butcher, exp_id=[''], exp_description=['Charcoal'], exp_amount=['200'], exp_category=[''])
        self.client.post(self.url, {
            'butchery': self.branch.id, 'date': self.today.isoformat(),
            'exp_description': ['Bags'], 'exp_amount': ['50'],
        })
        self.assertEqual(Expense.objects.count(), 2)

    def test_editing_past_day_carries_forward(self):
        day1 = self.today - timedelta(days=2)
        day2 = self.today - timedelta(days=1)
        self.post(self.owner, date=day1, received='10', closing='10')
        self.post(self.owner, date=day2, received='0', closing='4')
        self.assertEqual(self.row(day2).opening_stock, Decimal('10'))

        self.post(self.owner, date=day1, received='10', closing='8')
        self.assertEqual(self.row(day2).opening_stock, Decimal('8'))
        self.assertEqual(self.row(day2).sold, Decimal('4'))
        self.beef.refresh_from_db()
        self.assertEqual(self.beef.current_stock, Decimal('4'))

    def test_invalid_quantities_rejected(self):
        resp = self.post(self.butcher, closing='-3')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(DailyStock.objects.exists())
        resp = self.post(self.butcher, received='abc')
        self.assertEqual(resp.status_code, 400)

    def test_expense_without_amount_rejected(self):
        resp = self.post(self.butcher, exp_id=[''], exp_description=['Charcoal'], exp_amount=[''], exp_category=[''])
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Expense.objects.exists())

    def test_butcher_locked_to_own_branch(self):
        self.post(self.butcher, branch=self.other)
        self.assertEqual(self.row().product.butchery, self.branch)
        self.assertFalse(DailyBranchSummary.objects.filter(butchery=self.other).exists())

    def test_butcher_cannot_edit_past_day_without_permission(self):
        self.post(self.butcher, date=self.today - timedelta(days=1))
        self.assertFalse(DailyStock.objects.exists())

    def test_date_permission_allows_past_day(self):
        yesterday = self.today - timedelta(days=1)
        DatePermission.objects.create(
            user=self.butcher, butchery=self.branch, start_date=yesterday, end_date=yesterday,
        )
        self.post(self.butcher, date=yesterday)
        self.assertTrue(DailyStock.objects.filter(date=yesterday).exists())

    def test_nobody_can_enter_future_day(self):
        self.post(self.owner, date=self.today + timedelta(days=1))
        self.assertFalse(DailyStock.objects.exists())

    def test_closed_day_blocks_butcher_but_not_owner(self):
        self.post(self.butcher)
        self.client.force_login(self.owner)
        self.client.post(reverse('daily_close'), {
            'butchery_id': self.branch.id, 'date': self.today.isoformat(), 'action': 'close',
        })
        self.post(self.butcher, closing='1')
        self.assertEqual(self.row().closing_stock, Decimal('4'))
        self.post(self.owner, closing='1')
        self.assertEqual(self.row().closing_stock, Decimal('1'))

    def test_butcher_cannot_close_day(self):
        self.client.force_login(self.butcher)
        self.client.post(reverse('daily_close'), {
            'butchery_id': self.branch.id, 'date': self.today.isoformat(), 'action': 'close',
        })
        self.assertFalse(DailyBranchSummary.objects.filter(is_closed=True).exists())

    def test_saved_values_are_prefilled(self):
        self.post(self.butcher, mpesa_amount='1500', cash_counted='900', notes='Fridge broken', **self.transfer('2'))
        resp = self.client.get(self.url)
        self.assertContains(resp, 'value="1500.00"')
        self.assertContains(resp, 'value="900.00"')
        self.assertContains(resp, 'Fridge broken')
        self.assertContains(resp, f'name="transfer_id_{self.beef.id}" value="{DailyTransfer.objects.get().pk}"')

    def test_cash_variance(self):
        # revenue 4200 - mpesa 1000 - expenses 200 = 3000 expected
        self.post(
            self.butcher, mpesa_amount='1000', cash_counted='2900',
            exp_id=[''], exp_description=['Charcoal'], exp_amount=['200'], exp_category=[''],
        )
        self.client.force_login(self.owner)
        data = self.client.get(reverse('dashboard_data'), {'date': self.today.isoformat()}).json()
        main = next(b for b in data['per_branch'] if b['name'] == 'Main')
        self.assertEqual(main['expected_cash'], 3000.0)
        self.assertEqual(main['cash_variance'], -100.0)

    def test_butcher_only_reaches_daily_entry(self):
        self.client.force_login(self.butcher)
        for name in ('home', 'audit_log_list', 'expense_list', 'buying_prices'):
            resp = self.client.get(reverse(name))
            self.assertRedirects(resp, self.url, fetch_redirect_response=False)

    def test_owner_pages_render(self):
        self.post(self.butcher, **self.transfer('2'))
        self.client.force_login(self.owner)
        for url in (
            reverse('home'), reverse('audit_log_list'), reverse('reporting_dashboard'),
            reverse('profit_loss_report'), reverse('buying_prices'),
            f"{reverse('daily_stock_readonly')}?butchery_id={self.branch.id}&date={self.today}",
            f"{reverse('daily_stock_readonly')}?butchery_id={self.other.id}&date={self.today}",
            f"{self.url}?butchery={self.other.id}",
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class MissingEntriesGridTests(TestCase):
    def setUp(self):
        self.today = _business_date()
        self.owner = User.objects.create_superuser('owner', password='pw-owner-123')
        self.main = Butchery.objects.create(name='Main', location='Town', phone='1')
        self.new = Butchery.objects.create(name='New', location='Village', phone='2')

    def summary(self, branch, days_ago, **kw):
        DailyBranchSummary.objects.create(butchery=branch, date=self.today - timedelta(days=days_ago), **kw)

    def statuses(self, grid, branch):
        row = next(r for r in grid['status_rows'] if r['branch'] == branch)
        return [c['status'] for c in row['cells']], row['missing']

    def test_statuses(self):
        self.summary(self.main, 5)
        self.summary(self.main, 3)
        self.summary(self.main, 1, is_closed=True)
        grid = _entry_status_grid(7)
        statuses, missing = self.statuses(grid, self.main)
        # newest first: today, 1..6 days ago
        self.assertEqual(statuses, ['today', 'closed', 'missing', 'submitted', 'missing', 'submitted', 'none'])
        self.assertEqual(missing, 2)

    def test_branch_without_entries_is_not_counted(self):
        self.summary(self.main, 0)
        grid = _entry_status_grid(7)
        statuses, missing = self.statuses(grid, self.new)
        self.assertEqual(set(statuses), {'none'})
        self.assertEqual(missing, 0)
        self.assertEqual(grid['missing_total'], 0)

    def test_dashboard_shows_panel_and_range(self):
        self.summary(self.main, 3)
        self.client.force_login(self.owner)
        resp = self.client.get(reverse('home'), {'missing_days': '30'})
        self.assertContains(resp, 'Missing Entries')
        self.assertEqual(len(resp.context['status_dates']), 30)
        self.assertEqual(resp.context['missing_total'], 2)
        resp = self.client.get(reverse('home'), {'missing_days': '999'})
        self.assertEqual(resp.context['missing_days'], 14)


@override_settings(MISSING_DAY_ENFORCE_FROM=date(2000, 1, 1))
class MissedDayGateTests(TestCase):
    def setUp(self):
        self.today = _business_date()
        self.owner = User.objects.create_superuser('owner', password='pw-owner-123')
        self.butcher = User.objects.create_user('butcher', password='pw-butcher-123')
        self.branch = Butchery.objects.create(name='Main', location='Town', phone='1')
        Staff.objects.create(
            user=self.butcher, butchery=self.branch, role='BUTCHER', phone='3',
            id_number='ID1', date_hired=self.today,
        )
        cat = MeatCategory.objects.create(name='Beef')
        self.beef = MeatProduct.objects.create(
            category=cat, name='Beef', buying_price=Decimal('500'),
            selling_price=Decimal('700'), butchery=self.branch,
        )
        self.url = reverse('daily_stock_entry')
        # Entered 3 days ago; 2 days ago and yesterday were missed.
        DailyBranchSummary.objects.create(butchery=self.branch, date=self.ago(3))

    def ago(self, n):
        return self.today - timedelta(days=n)

    def post(self, user, day):
        self.client.force_login(user)
        return self.client.post(self.url, {
            'form_version': '2', 'butchery': self.branch.id, 'date': day.isoformat(),
            f'received_{self.beef.id}': '5', f'wastage_{self.beef.id}': '0',
            f'closing_{self.beef.id}': '1', 'mpesa_amount': '0',
        })

    def saved(self, day):
        return DailyStock.objects.filter(product=self.beef, date=day).exists()

    def test_butcher_blocked_from_today_and_redirected_to_oldest_missed_day(self):
        self.post(self.butcher, self.today)
        self.assertFalse(self.saved(self.today))
        resp = self.client.get(self.url)
        self.assertRedirects(
            resp, f"{self.url}?butchery={self.branch.id}&date={self.ago(2)}",
            fetch_redirect_response=False,
        )

    def test_must_fill_oldest_first(self):
        self.post(self.butcher, self.ago(1))
        self.assertFalse(self.saved(self.ago(1)))

    def test_catching_up_then_today(self):
        resp = self.post(self.butcher, self.ago(2))
        self.assertTrue(self.saved(self.ago(2)))
        self.assertRedirects(
            resp, f"{self.url}?butchery={self.branch.id}&date={self.ago(1)}",
            fetch_redirect_response=False,
        )
        resp = self.post(self.butcher, self.ago(1))
        self.assertRedirects(
            resp, f"{self.url}?butchery={self.branch.id}&date={self.today}",
            fetch_redirect_response=False,
        )
        self.post(self.butcher, self.today)
        self.assertTrue(self.saved(self.today))

    def test_filled_past_day_is_locked_again(self):
        self.post(self.butcher, self.ago(2))
        self.post(self.butcher, self.ago(1))
        DailyStock.objects.filter(date=self.ago(2)).update(closing_stock=Decimal('1'))
        self.client.force_login(self.butcher)
        self.client.post(self.url, {
            'form_version': '2', 'butchery': self.branch.id, 'date': self.ago(2).isoformat(),
            f'received_{self.beef.id}': '5', f'closing_{self.beef.id}': '3',
        })
        self.assertEqual(DailyStock.objects.get(date=self.ago(2)).closing_stock, Decimal('1'))

    def test_no_trading_day_does_not_block(self):
        self.client.force_login(self.owner)
        for n in (2, 1):
            self.client.post(reverse('daily_close'), {
                'butchery_id': self.branch.id, 'date': self.ago(n).isoformat(), 'action': 'close',
            })
        self.post(self.butcher, self.today)
        self.assertTrue(self.saved(self.today))

    def test_owner_never_blocked(self):
        self.post(self.owner, self.today)
        self.assertTrue(self.saved(self.today))

    def test_days_before_enforcement_start_ignored(self):
        with override_settings(MISSING_DAY_ENFORCE_FROM=self.ago(1)):
            self.post(self.butcher, self.today)
            self.assertFalse(self.saved(self.today))
            self.post(self.butcher, self.ago(1))
            self.post(self.butcher, self.today)
            self.assertTrue(self.saved(self.today))
            self.assertFalse(self.saved(self.ago(2)))

    def test_branch_without_entries_not_blocked(self):
        DailyBranchSummary.objects.all().delete()
        self.post(self.butcher, self.today)
        self.assertTrue(self.saved(self.today))


@override_settings(
    AT_USERNAME='bms', AT_API_KEY='test-key', AT_SENDER_ID='', SMS_RECIPIENTS=['+254700000001'],
)
class DailySummarySMSTests(TestCase):
    def setUp(self):
        self.day = _business_date() - timedelta(days=1)
        self.owner = User.objects.create_superuser('owner', password='pw-owner-123')
        self.main = Butchery.objects.create(name='Main', location='Town', phone='1')
        self.idle = Butchery.objects.create(name='Idle', location='Village', phone='2')
        self.shut = Butchery.objects.create(name='Shut', location='Hill', phone='3')
        cat = MeatCategory.objects.create(name='Beef')
        beef = MeatProduct.objects.create(
            category=cat, name='Beef', buying_price=Decimal('500'),
            selling_price=Decimal('700'), butchery=self.main,
        )
        DailyStock.objects.create(
            product=beef, date=self.day, opening_stock=0, received=10, closing_stock=4,
            selling_price=Decimal('700'), buying_price=Decimal('500'),
        )
        DailyBranchSummary.objects.create(
            butchery=self.main, date=self.day, mpesa_amount=Decimal('1000'), cash_counted=Decimal('2900'),
        )
        Expense.objects.create(butchery=self.main, date=self.day, amount=Decimal('200'), description='Charcoal')
        DailyBranchSummary.objects.create(butchery=self.shut, date=self.day, is_closed=True)

    def fake_response(self, status_code=101):
        body = json.dumps({'SMSMessageData': {'Message': 'Sent to 1/1', 'Recipients': [
            {'statusCode': status_code, 'number': '+254700000001', 'status': 'Success' if status_code == 101 else 'InsufficientBalance', 'cost': 'KES 0.8000'},
        ]}}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        return response

    def test_message_content(self):
        msg = build_daily_summary(self.day)
        self.assertIn('Main: Sales 4,200 | Mpesa 1,000 | Exp 200 | Cash diff -100', msg)
        self.assertIn('Idle: NOT ENTERED', msg)
        self.assertIn('Shut: No trading', msg)
        self.assertIn('TOTAL Sales 4,200 | Net profit 1,000', msg)

    def test_dry_run_does_not_send(self):
        out = StringIO()
        with mock.patch('urllib.request.urlopen') as urlopen:
            call_command('send_daily_summary', '--dry-run', stdout=out)
        urlopen.assert_not_called()
        self.assertIn('Main: Sales', out.getvalue())

    def test_sends_and_logs(self):
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response()) as urlopen:
            call_command('send_daily_summary', stdout=StringIO())
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, 'https://api.africastalking.com/version1/messaging')
        self.assertEqual(request.get_header('Apikey'), 'test-key')
        self.assertIn(b'to=%2B254700000001', request.data)
        self.assertTrue(AuditLog.objects.filter(action='SMS_SUMMARY_SENT').exists())

    def test_failed_recipient_raises_and_logs(self):
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response(405)):
            with self.assertRaises(CommandError):
                call_command('send_daily_summary', stdout=StringIO())
        log = AuditLog.objects.get(action='SMS_SUMMARY_FAILED')
        self.assertIn('InsufficientBalance', log.description)
        self.assertNotIn('test-key', log.description)

    @override_settings(AT_API_KEY='')
    def test_not_configured(self):
        with self.assertRaises(CommandError):
            call_command('send_daily_summary', stdout=StringIO())

    @override_settings(AT_USERNAME='sandbox')
    def test_sandbox_url(self):
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response()) as urlopen:
            call_command('send_daily_summary', stdout=StringIO())
        self.assertIn('sandbox', urlopen.call_args[0][0].full_url)


class SchoolDeliveryTests(TestCase):
    def setUp(self):
        self.today = _business_date()
        self.owner = User.objects.create_superuser('owner', password='pw-owner-123')
        self.butcher = User.objects.create_user('butcher', password='pw-butcher-123')
        self.supa = Butchery.objects.create(name='Supa', location='Karen', phone='1', serves_schools=True)
        Staff.objects.create(
            user=self.butcher, butchery=self.supa, role='BUTCHER', phone='3',
            id_number='ID1', date_hired=self.today,
        )
        cat = MeatCategory.objects.create(name='Beef')
        self.beef = MeatProduct.objects.create(
            category=cat, name='Beef', buying_price=Decimal('500'), selling_price=Decimal('700'), butchery=self.supa,
        )
        self.minced = MeatProduct.objects.create(
            category=cat, name='Minced Meat', buying_price=Decimal('550'), selling_price=Decimal('750'), butchery=self.supa,
        )
        self.school = School.objects.create(butchery=self.supa, name='Hill School')
        SchoolPrice.objects.create(school=self.school, product=self.beef, price_per_unit=Decimal('800'))
        SchoolPrice.objects.create(school=self.school, product=self.minced, price_per_unit=Decimal('900'))
        self.url = reverse('daily_stock_entry')

    def post(self, deliveries=(), user=None, **extra):
        self.client.force_login(user or self.butcher)
        data = {
            'form_version': '2', 'butchery': self.supa.id, 'date': self.today.isoformat(),
            f'received_{self.beef.id}': '20', f'closing_{self.beef.id}': '5',
            f'received_{self.minced.id}': '10', f'closing_{self.minced.id}': '10',
            'mpesa_amount': '0',
            'delivery_id': [d[0] for d in deliveries],
            'delivery_school': [str(self.school.id) for _ in deliveries],
            'delivery_product': [str(d[1].id) for d in deliveries],
            'delivery_quantity': [d[2] for d in deliveries],
        }
        data.update(extra)
        return self.client.post(self.url, data)

    def test_revenue_uses_school_price_and_cash_excludes_deliveries(self):
        self.post([('', self.beef, '10')])
        ds = DailyStock.objects.get(product=self.beef, date=self.today)
        self.assertEqual(ds.sold, Decimal('15'))
        self.assertEqual(ds.revenue, Decimal('11500'))  # 5 kg x 700 + 10 kg x 800
        self.client.force_login(self.owner)
        data = self.client.get(reverse('dashboard_data'), {'date': self.today.isoformat()}).json()
        self.assertEqual(data['per_branch'][0]['expected_cash'], 3500.0)

    def test_resave_updates_and_remove_deletes(self):
        self.post([('', self.beef, '10')])
        delivery = SchoolDelivery.objects.get()
        self.post([(str(delivery.pk), self.beef, '12')])
        delivery.refresh_from_db()
        self.assertEqual(SchoolDelivery.objects.count(), 1)
        self.assertEqual(delivery.quantity, Decimal('12'))
        self.post([])
        self.assertFalse(SchoolDelivery.objects.exists())

    def test_price_kept_when_school_price_changes(self):
        self.post([('', self.beef, '10')])
        SchoolPrice.objects.filter(product=self.beef).update(price_per_unit=Decimal('850'))
        delivery = SchoolDelivery.objects.get()
        self.post([(str(delivery.pk), self.beef, '11')])
        delivery.refresh_from_db()
        self.assertEqual(delivery.unit_price, Decimal('800'))

    def test_delivery_more_than_sold_rejected(self):
        resp = self.post([('', self.minced, '3')])  # minced sold 0
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SchoolDelivery.objects.exists())

    def test_missing_price_rejected(self):
        SchoolPrice.objects.filter(product=self.beef).delete()
        resp = self.post([('', self.beef, '5')])
        self.assertEqual(resp.status_code, 400)

    def test_section_only_for_school_branch(self):
        other = Butchery.objects.create(name='Starlight', location='CBD', phone='2')
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.url, {'butchery': other.id}), 'School Deliveries')
        self.assertContains(self.client.get(self.url, {'butchery': self.supa.id}), 'School Deliveries')

    def test_schools_report_and_cheque(self):
        self.post([('', self.beef, '10'), ('', self.minced, '0')], **{f'closing_{self.minced.id}': '8'})
        self.assertEqual(SchoolDelivery.objects.count(), 0)  # zero kg row rejected the whole save
        self.post([('', self.beef, '10'), ('', self.minced, '2')], **{f'closing_{self.minced.id}': '8'})
        self.client.force_login(self.owner)
        url = reverse('schools_report')
        resp = self.client.get(url, {'week': self.today.isoformat()})
        row = resp.context['rows'][0]
        self.assertEqual(row['total'], Decimal('9800'))  # 10x800 + 2x900
        self.client.post(f"{url}?week={self.today.isoformat()}", {
            'school_id': self.school.id, 'cheque_number': '000123', 'amount': '9800',
        })
        resp = self.client.get(url, {'week': self.today.isoformat()})
        self.assertEqual(resp.context['rows'][0]['balance'], Decimal('0'))
        self.assertEqual(resp.context['owed_total'], Decimal('0'))
        statement = self.client.get(reverse('school_statement', args=[self.school.id]), {'week': self.today.isoformat()})
        self.assertContains(statement, 'Hill School')
        self.assertContains(statement, '000123')

    def test_butcher_cannot_open_schools_page(self):
        self.client.force_login(self.butcher)
        resp = self.client.get(reverse('schools_report'))
        self.assertRedirects(resp, self.url, fetch_redirect_response=False)

    def test_admin_school_pages_render(self):
        self.client.force_login(self.owner)
        for name, args in (('admin:inventory_school_changelist', []), ('admin:inventory_school_add', []),
                           ('admin:inventory_school_change', [self.school.id])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)


class SecurityUpgradeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser('owner', password='pw-owner-123')
        self.butcher = User.objects.create_user('butcher', password='pw-butcher-123')
        branch = Butchery.objects.create(name='Main', location='Town', phone='1')
        Staff.objects.create(
            user=self.butcher, butchery=branch, role='BUTCHER', phone='3',
            id_number='ID1', date_hired=_business_date(),
        )
        cat = MeatCategory.objects.create(name='Beef')
        self.beef = MeatProduct.objects.create(
            category=cat, name='Beef', buying_price=Decimal('0'), selling_price=Decimal('700'), butchery=branch,
        )

    def test_logins_last_30_days(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 30)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)

    def test_log_out_everywhere(self):
        butcher_client = self.client_class()
        butcher_client.force_login(self.butcher)
        self.client.force_login(self.owner)
        self.client.post(reverse('staff_logout_everywhere', args=[self.butcher.id]))
        resp = butcher_client.get(reverse('daily_stock_entry'))
        self.assertRedirects(resp, reverse('login'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)

    def test_butcher_cannot_log_out_others(self):
        self.client.force_login(self.butcher)
        self.client.post(reverse('staff_logout_everywhere', args=[self.owner.id]))
        owner_client = self.client_class()
        owner_client.force_login(self.owner)
        self.assertEqual(owner_client.get(reverse('home')).status_code, 200)

    def test_missing_buying_price_banner(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('home')), 'no buying price')
        BuyingPrice.objects.create(
            product=self.beef, date=_business_date(), buying_price_per_kg=Decimal('550'),
            quantity_received=Decimal('1'),
        )
        self.assertNotContains(self.client.get(reverse('home')), 'no buying price')


class DuplicateExpenseCommandTests(TestCase):
    def test_finds_and_deletes_duplicates(self):
        branch = Butchery.objects.create(name='Main', location='Town', phone='1')
        for _ in range(3):
            Expense.objects.create(butchery=branch, amount=Decimal('200'), description='Charcoal')
        Expense.objects.create(butchery=branch, amount=Decimal('50'), description='Bags')

        call_command('find_duplicate_expenses', stdout=StringIO())
        self.assertEqual(Expense.objects.count(), 4)

        call_command('find_duplicate_expenses', '--delete', stdout=StringIO())
        self.assertEqual(Expense.objects.count(), 2)
