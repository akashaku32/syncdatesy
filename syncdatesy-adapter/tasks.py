#!/usr/bin/env python
"""Task runner for DataSyncher - Cross-platform development commands"""
import os
import sys
import subprocess

def run_command(cmd):
    """Run a shell command"""
    return subprocess.run(cmd, shell=True).returncode == 0

commands = {
    "install": "pip install -r requirements/development.txt",
    "run": "python manage.py runserver",
    "migrate": "python manage.py migrate",
    "test": "pytest tests/",
    "format": "black apps/ datasyncher/ && isort apps/ datasyncher/",
    "lint": "flake8 apps/ datasyncher/",
    "dev-up": "docker-compose -f deploy/docker-compose/docker-compose.yml up -d",
    "dev-down": "docker-compose -f deploy/docker-compose/docker-compose.yml down",
    "dev-logs": "docker-compose -f deploy/docker-compose/docker-compose.yml logs -f",
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print("Available commands:")
        for cmd in commands:
            print(f"  python tasks.py {cmd}")
    else:
        run_command(commands[sys.argv[1]])
