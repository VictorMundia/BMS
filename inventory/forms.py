from django import forms
from django.contrib.auth.models import User

from .models import StockMovement, Expense, Staff, StockTransfer


class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ['product', 'quantity', 'movement_type', 'unit_price', 'notes']

    def clean(self):
        cleaned_data = super().clean()
        movement_type = cleaned_data.get('movement_type')
        quantity = cleaned_data.get('quantity')
        product = cleaned_data.get('product')

        if movement_type == 'OUT' and product and quantity:
            if quantity > product.current_stock:
                raise forms.ValidationError(
                    'Quantity cannot exceed current stock for an OUT movement.'
                )
        return cleaned_data


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['butchery', 'amount', 'description', 'date', 'receipt_image']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }


class StaffCreateForm(forms.ModelForm):
    """Combined form to create a User account and linked Staff profile."""
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = Staff
        fields = ['butchery', 'role', 'phone', 'id_number', 'date_hired', 'is_active']
        widgets = {
            'date_hired': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('A user with that username already exists.')
        return username

    def save(self, commit=True):
        user = User.objects.create_user(
            username=self.cleaned_data['username'],
            password=self.cleaned_data['password'],
            first_name=self.cleaned_data.get('first_name', ''),
            last_name=self.cleaned_data.get('last_name', ''),
            email=self.cleaned_data.get('email', ''),
        )
        staff = super().save(commit=False)
        staff.user = user
        if commit:
            staff.save()
        return staff


class StockTransferForm(forms.ModelForm):
    class Meta:
        model = StockTransfer
        fields = ['from_butchery', 'to_butchery', 'product', 'quantity', 'notes']

    def clean(self):
        cleaned_data = super().clean()
        from_butchery = cleaned_data.get('from_butchery')
        to_butchery = cleaned_data.get('to_butchery')
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        if from_butchery and to_butchery and from_butchery == to_butchery:
            raise forms.ValidationError('Source and destination butchery must differ.')
        if product and quantity and quantity > product.current_stock:
            raise forms.ValidationError(
                f"Quantity exceeds available stock ({product.current_stock})."
            )
        return cleaned_data
