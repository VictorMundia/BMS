from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import (
    BuyingPrice, Butchery, DailyBranchSummary, DailyStock, DailyTransfer, DatePermission,
    Expense, MeatCategory, MeatProduct, Staff,
)
from .views import _business_date


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
