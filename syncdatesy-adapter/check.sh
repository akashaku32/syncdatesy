#!/bin/bash
echo "🔍 Checking code quality..."
echo ""
echo "1️⃣ Black check..."
black --check .
echo ""
echo "2️⃣ isort check..."
isort --check-only .
echo ""
echo "3️⃣ Flake8 check..."
flake8 .
echo ""
echo "✅ All checks complete!"
