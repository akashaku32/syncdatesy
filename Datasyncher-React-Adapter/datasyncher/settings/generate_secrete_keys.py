# Create a file: generate_secret_keys.py
from django.core.management.utils import get_random_secret_key

print("=== Generate Secret Keys for Each Environment ===")
print("\nDeveloper Secret Key:")
print(get_random_secret_key())
print("\nQA Secret Key:")
print(get_random_secret_key())
print("\nProduction Secret Key:")
print(get_random_secret_key())
print("\n⚠️  IMPORTANT: Never share or commit these keys!")