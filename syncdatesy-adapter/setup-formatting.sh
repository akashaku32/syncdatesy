#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}🚀 Django Code Formatting Setup - One Command Installation${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo -e "${YELLOW}📌 Python version: ${PYTHON_VERSION}${NC}"

# Step 1: Create/activate virtual environment
echo -e "\n${YELLOW}Step 1: Setting up virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi

# Activate venv
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null

# Step 2: Install formatting tools
echo -e "\n${YELLOW}Step 2: Installing formatting tools...${NC}"
pip install --quiet --upgrade pip

cat > .formatting-requirements.txt << 'EOF'
# Code Formatting & Linting
black==23.3.0
flake8==6.0.0
flake8-django==1.4
flake8-isort==6.0.0
flake8-docstrings==1.7.0
flake8-bugbear==23.5.9
isort==5.12.0
pre-commit==3.3.3
pylint==2.17.4
pylint-django==2.5.3

# Additional tools
autopep8==2.0.2
pycodestyle==2.10.0
pydocstyle==6.3.0
EOF

pip install -r .formatting-requirements.txt
echo -e "${GREEN}✓ All formatting tools installed${NC}"

# Step 3: Create configuration files
echo -e "\n${YELLOW}Step 3: Creating configuration files...${NC}"

# Create pyproject.toml (Black & isort config)
cat > pyproject.toml << 'EOF'
[tool.black]
line-length = 88
target-version = ['py38', 'py39', 'py310']
include = '\.pyi?$'
extend-exclude = '''
/(
    \.git
  | \.hg
  | \.mypy_cache
  | \.tox
  | \.venv
  | venv
  | _build
  | buck-out
  | build
  | dist
  | migrations
)/
'''

[tool.isort]
profile = "black"
line_length = 88
multi_line_output = 3
include_trailing_comma = true
force_grid_wrap = 0
use_parentheses = true
ensure_newline_before_comments = true
split_on_trailing_comma = true
skip = ["venv", ".venv", "migrations"]
skip_glob = ["*/migrations/*"]
known_django = ["django", "rest_framework"]
known_first_party = ["apps", "backend", "core"]
sections = ["FUTURE", "STDLIB", "DJANGO", "THIRDPARTY", "FIRSTPARTY", "LOCALFOLDER"]
EOF

# Create .flake8
cat > .flake8 << 'EOF'
[flake8]
max-line-length = 88
extend-ignore = E203, E266, E501, W503, F403, F401
max-complexity = 10
exclude = 
    .git,
    __pycache__,
    */migrations/*,
    */static/*,
    */media/*,
    venv,
    .venv,
    env,
    .env,
    build,
    dist,
    *.egg-info,
    .pytest_cache,
    .mypy_cache
per-file-ignores =
    __init__.py:F401,F403
    */settings.py:E501,F405
    */settings/*.py:E501,F405
    */tests/*.py:S101
EOF

# Create .editorconfig
cat > .editorconfig << 'EOF'
# EditorConfig helps maintain consistent coding styles

root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.{json,yml,yaml}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false

[Makefile]
indent_style = tab
EOF

# Create setup.cfg (alternative config)
cat > setup.cfg << 'EOF'
[flake8]
max-line-length = 88
extend-ignore = E203, W503
exclude = .git,__pycache__,venv,migrations

[isort]
profile = black
multi_line_output = 3
include_trailing_comma = True
force_grid_wrap = 0
use_parentheses = True
ensure_newline_before_comments = True
line_length = 88

[pylint]
load-plugins = pylint_django
django-settings-module = settings
max-line-length = 88

[pylint.messages_control]
disable = C0111,C0103,R0903,W0622,R0801
EOF

echo -e "${GREEN}✓ Configuration files created${NC}"

# Step 4: Setup pre-commit
echo -e "\n${YELLOW}Step 4: Setting up pre-commit hooks...${NC}"

cat > .pre-commit-config.yaml << 'EOF'
# Pre-commit hooks for code quality
default_language_version:
  python: python3

repos:
  # General hooks
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.4.0
    hooks:
      - id: trailing-whitespace
        exclude: \.md$
      - id: end-of-file-fixer
        exclude: \.md$
      - id: check-yaml
      - id: check-added-large-files
        args: ['--maxkb=1000']
      - id: check-case-conflict
      - id: check-merge-conflict
      - id: check-docstring-first
      - id: debug-statements
      - id: detect-private-key

  # Black formatter
  - repo: https://github.com/psf/black
    rev: 23.3.0
    hooks:
      - id: black
        language_version: python3
        args: ['--config=pyproject.toml']

  # isort
  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort
        args: ['--settings-path=pyproject.toml']

  # Flake8
  - repo: https://github.com/pycqa/flake8
    rev: 6.0.0
    hooks:
      - id: flake8
        args: ['--config=.flake8']
        additional_dependencies:
          - flake8-django
          - flake8-isort
          - flake8-bugbear
          - flake8-docstrings

  # Django specific
  - repo: https://github.com/adamchainz/django-upgrade
    rev: '1.13.0'
    hooks:
      - id: django-upgrade
        args: [--target-version, '3.2']

  # Security checks
  - repo: https://github.com/pycqa/bandit
    rev: '1.7.5'
    hooks:
      - id: bandit
        args: ['-r', '--skip', 'B101,B601']
        exclude: tests/
EOF

# Install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
echo -e "${GREEN}✓ Pre-commit hooks installed${NC}"

# Step 5: Create VS Code settings
echo -e "\n${YELLOW}Step 5: Creating VS Code settings...${NC}"
mkdir -p .vscode

cat > .vscode/settings.json << 'EOF'
{
    // Python interpreter
    "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python",
    
    // Format on save
    "editor.formatOnSave": true,
    "editor.formatOnPaste": false,
    "editor.formatOnType": false,
    
    // Python specific
    "[python]": {
        "editor.defaultFormatter": "ms-python.black-formatter",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit"
        },
        "editor.rulers": [88]
    },
    
    // Black formatter settings
    "black-formatter.args": [
        "--line-length=88",
        "--config=${workspaceFolder}/pyproject.toml"
    ],
    
    // isort settings
    "isort.args": [
        "--settings-path=${workspaceFolder}/pyproject.toml"
    ],
    
    // Flake8 settings
    "flake8.args": [
        "--config=${workspaceFolder}/.flake8"
    ],
    
    // Enable linting
    "python.linting.enabled": true,
    "python.linting.lintOnSave": true,
    "python.linting.flake8Enabled": true,
    "python.linting.pylintEnabled": false,
    
    // Files associations
    "files.associations": {
        "**/*.html": "html",
        "**/templates/**/*.html": "django-html",
        "**/templates/**/*": "django-txt",
        "**/requirements{/**,*}.{txt,in}": "pip-requirements"
    },
    
    // Editor config
    "editor.rulers": [88],
    "editor.wordWrap": "bounded",
    "editor.wordWrapColumn": 88
}
EOF

cat > .vscode/extensions.json << 'EOF'
{
    "recommendations": [
        "ms-python.python",
        "ms-python.vscode-pylance",
        "ms-python.black-formatter",
        "ms-python.isort",
        "ms-python.flake8",
        "batisteo.vscode-django",
        "editorconfig.editorconfig"
    ]
}
EOF

echo -e "${GREEN}✓ VS Code settings created${NC}"

# Step 6: Create helpful scripts
echo -e "\n${YELLOW}Step 6: Creating helper scripts...${NC}"

# Create format.sh
cat > format.sh << 'EOF'
#!/bin/bash
echo "🎨 Formatting all Python files..."
black .
isort .
echo "✅ Formatting complete!"
EOF
chmod +x format.sh

# Create check.sh
cat > check.sh << 'EOF'
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
EOF
chmod +x check.sh

# Create Makefile
cat > Makefile << 'EOF'
.PHONY: help format lint check fix install clean

help:
	@echo "📋 Available commands:"
	@echo "  make install  - Install formatting tools"
	@echo "  make format   - Format all code"
	@echo "  make lint     - Run all linters"
	@echo "  make check    - Check formatting without changes"
	@echo "  make fix      - Fix all auto-fixable issues"
	@echo "  make clean    - Clean cache files"

install:
	pip install -r .formatting-requirements.txt
	pre-commit install

format:
	@echo "🎨 Formatting code..."
	black .
	isort .

lint:
	@echo "🔍 Running linters..."
	flake8 .

check:
	@echo "✅ Running format checks..."
	black --check .
	isort --check-only .

fix: format

clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

EOF

	