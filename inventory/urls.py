from django.urls import path

from . import views

urlpatterns = [
    # Dashboard (live)
    path('dashboard-data/', views.dashboard_data, name='dashboard_data'),
    # Daily stock entry (butchers)
    path('daily-stock/', views.daily_stock_entry, name='daily_stock_entry'),
    path('daily-stock/history/', views.daily_stock_history, name='daily_stock_history'),
    path('daily-stock/readonly/', views.daily_stock_readonly, name='daily_stock_readonly'),
    # Stock movements
    path('stock-movement/', views.stock_movement, name='stock_movement'),
    path('stock-movements/', views.stock_movement_list, name='stock_movement_list'),
    # Reports
    path('reports/', views.reporting_dashboard, name='reporting_dashboard'),
    path('reports/export/', views.export_reports, name='export_reports'),
    path('reports/profit-loss/', views.profit_loss_report, name='profit_loss_report'),
    path('reports/profit-loss/export/', views.export_profit_loss, name='export_profit_loss'),
    # Expenses
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/create/', views.expense_create, name='expense_create'),
    # Staff
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/create/', views.staff_create, name='staff_create'),
    # Stock transfers
    path('transfers/create/', views.transfer_create, name='transfer_create'),
    path('transfers/<int:pk>/approve/', views.transfer_approve, name='transfer_approve'),
    # Buying prices (owner only)
    path('buying-prices/', views.buying_prices, name='buying_prices'),
    path('buying-prices/save/', views.save_buying_prices, name='save_buying_prices'),
]
