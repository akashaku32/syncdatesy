#!/bin/bash
echo "🎨 Formatting all Python files..."
black .
isort .
echo "✅ Formatting complete!"
