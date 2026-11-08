#!/usr/bin/env python
"""Enhanced Task Runner for DataSyncher with Error Handling"""
import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path
import time

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    YELLOW = '\033[93m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}✗ {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")

def get_venv_executable(command):
    if platform.system() == "Windows":
        venv_path = Path("venv/Scripts")
    else:
        venv_path = Path("venv/bin")
    exe_path = venv_path / command
    if exe_path.exists():
        return str(exe_path)
    return command

def run_command(cmd, check_venv=False, display_output=True, ignore_errors=False):
    if check_venv and sys.prefix == sys.base_prefix:
        print_error("Virtual environment is not activated!")
        print_info("Please activate it first:")
        if platform.system() == "Windows":
            print("  venv\Scripts\activate")
        else:
            print("  source venv/bin/activate")
        return False, "Venv not activated"
    print_info(f"Running: {cmd}")
    try:
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output_lines = []
        for line in process.stdout:
            if display_output:
                print(line, end='')
            output_lines.append(line)
        process.wait()
        if process.returncode != 0:
            if not ignore_errors:
                print_error(f"Command failed with exit code {process.returncode}")
                if not display_output:
                    print("--- Command Output ---")
                    print("".join(output_lines))
                    print("----------------------")
            return False, "".join(output_lines)
        return True, "".join(output_lines)
    except FileNotFoundError:
        print_error(f"Command not found: {cmd.split(' ')[0]}")
        return False, "Command not found"
    except Exception as e:
        print_error(f"An unexpected error occurred: {str(e)}")
        return False, str(e)

def find_docker_compose():
    paths = [
        "deploy/docker-compose/docker-compose.yml",
        "docker-compose.yml",
        "deploy/docker-compose.yml"
    ]
    for path in paths:
        if Path(path).exists():
            return path
    return None

def install():
    """Install dependencies"""
    print_info("Installing dependencies...")
    pip_cmd = get_venv_executable("pip")
    success, _ = run_command(f"{pip_cmd} install -r requirements.txt")
    if success:
        print_success("Dependencies installed")
        print_info("Installing pre-commit hooks...")
        run_command("pre-commit install", ignore_errors=True)
    else:
        print_error("Failed to install dependencies.")
        print_info("Ensure requirements.txt exists and virtual environment is active.")

def format_code():
    """Format code"""
    print_info("Formatting code with Black and iSort...")
    black = get_venv_executable("black")
    isort = get_venv_executable("isort")
    success_black, _ = run_command(f"{black} --version", check_venv=False, display_output=False, ignore_errors=True)
    success_isort, _ = run_command(f"{isort} --version", check_venv=False, display_output=False, ignore_errors=True)
    if not success_black or not success_isort:
        print_warning("Black or isort not found in venv. Attempting to install...")
        pip_cmd = get_venv_executable("pip")
        install_success, _ = run_command(f"{pip_cmd} install black isort")
        if not install_success:
            print_error("Failed to install Black and isort. Aborting format.")
            return
    dirs = ["apps", "datasyncher", "tests"]
    existing_dirs = [d for d in dirs if Path(d).exists()]
    if existing_dirs:
        black_cmd = f"{black} {' '.join(existing_dirs)}"
        isort_cmd = f"{isort} {' '.join(existing_dirs)}"
        black_success, _ = run_command(black_cmd)
        isort_success, _ = run_command(isort_cmd)
        if black_success and isort_success:
            print_success("Code formatted successfully.")
        else:
            print_error("Code formatting failed. Check output for details.")
    else:
        print_warning("No application, project, or test directories found (apps, datasyncher, tests) to format.")

def lint():
    """Lint code with Flake8"""
    print_info("Linting code with Flake8...")
    flake8 = get_venv_executable("flake8")
    success_flake8, _ = run_command(f"{flake8} --version", check_venv=False, display_output=False, ignore_errors=True)
    if not success_flake8:
        print_warning("Flake8 not found in venv. Attempting to install...")
        pip_cmd = get_venv_executable("pip")
        install_success, _ = run_command(f"{pip_cmd} install flake8")
        if not install_success:
            print_error("Failed to install Flake8. Aborting lint.")
            return
    dirs = ["apps", "datasyncher", "tests"]
    existing_dirs = [d for d in dirs if Path(d).exists()]
    if existing_dirs:
        lint_cmd = f"{flake8} {' '.join(existing_dirs)}"
        success, output = run_command(lint_cmd)
        if success:
            print_success("Code linted successfully (no issues found).")
        else:
            print_error(f"Linting failed. Issues found:
{output}")
    else:
        print_warning("No application, project, or test directories found (apps, datasyncher, tests) to lint.")

def dev_up():
    """Start development containers"""
    compose_file = find_docker_compose()
    if not compose_file:
        print_error("docker-compose.yml not found! Searched in: deploy/docker-compose/, current dir.")
        return
    print_info("Starting development environment...")
    success, output = run_command(f"docker-compose -f {compose_file} up -d")
    if success:
        print_success("Development environment started")
        print_info("Services available at:")
        print("  - Django: http://localhost:8000")
        print("  - PostgreSQL: localhost:5432")
        print("  - Redis: localhost:6379")
        print("  - Flower (Celery Monitor): http://localhost:5555")
    else:
        print_error(f"Failed to start containers. Output:
{output}")
        print_info("Check if Docker Desktop is running and containers are not already in a bad state.")

def dev_down():
    """Stop and remove development containers"""
    compose_file = find_docker_compose()
    if not compose_file:
        print_error("docker-compose.yml not found!")
        return
    print_info("Stopping and removing development containers...")
    success, output = run_command(f"docker-compose -f {compose_file} down -v")
    if success:
        print_success("Development environment stopped and volumes removed.")
    else:
        print_error(f"Failed to stop containers. Output:
{output}")

def makemigrations():
    """Create Django migrations"""
    print_info("Creating Django migrations...")
    python_cmd = get_venv_executable("python")
    success, output = run_command(f"{python_cmd} manage.py makemigrations", check_venv=True)
    if success:
        print_success("Migrations created.")
        print(output)
    else:
        print_error(f"Failed to create migrations. Output:
{output}")
        print_info("Ensure database container is running and accessible.")

def migrate():
    """Run Django migrations"""
    print_info("Running Django migrations...")
    python_cmd = get_venv_executable("python")
    success, output = run_command(f"{python_cmd} manage.py migrate", check_venv=True)
    if success:
        print_success("Migrations completed.")
        print(output)
    else:
        print_error(f"Migration failed. Output:
{output}")
        print_info("Ensure the database container is running and accessible (try 'python task_runner.py dev_up' then 'python task_runner.py migrate').")
        print_info("Check database connection settings in .env file.")

def createsuperuser():
    """Create a Django superuser"""
    print_info("Creating Django superuser...")
    python_cmd = get_venv_executable("python")
    success, output = run_command(f"{python_cmd} manage.py createsuperuser", check_venv=True, display_output=True)
    if success:
        print_success("Superuser creation initiated. Follow prompts.")
    else:
        print_error(f"Failed to create superuser. Output:
{output}")
        print_info("Ensure database container is running and migrations are applied.")

def runserver():
    """Run Django development server"""
    print_info("Starting Django development server (http://localhost:8000)...")
    python_cmd = get_venv_executable("python")
    print_info("Press Ctrl+C to stop the server.")
    success, output = run_command(f"{python_cmd} manage.py runserver 0.0.0.0:8000", check_venv=True, display_output=True)
    if not success:
        print_error(f"Django development server failed to start. Output:
{output}")
        print_info("Check for port conflicts or Django errors.")

def test():
    """Run tests"""
    print_info("Running tests with Pytest...")
    pytest_cmd = get_venv_executable("pytest")
    success, output = run_command(f"{pytest_cmd}", check_venv=True)
    if success:
        print_success("All tests passed!")
    else:
        print_error(f"Tests failed. Output:
{output}")

def shell():
    """Open Django shell"""
    print_info("Opening Django shell...")
    python_cmd = get_venv_executable("python")
    success, _ = run_command(f"{python_cmd} manage.py shell", check_venv=True, display_output=True)
    if not success:
        print_error("Failed to open Django shell.")

def celery_worker():
    """Start Celery worker"""
    print_info("Starting Celery worker...")
    python_cmd = get_venv_executable("python")
    app_name = "datasyncher"
    print_info("Press Ctrl+C to stop the Celery worker.")
    success, output = run_command(f"{python_cmd} -m celery -A {app_name} worker -l info --pool=solo", check_venv=True, display_output=True)
    if not success:
        print_error(f"Celery worker failed to start. Output:
{output}")
        print_info("Ensure Redis is running (via docker-compose dev_up) and Celery configuration is correct.")

def celery_beat():
    """Start Celery beat scheduler"""
    print_info("Starting Celery beat scheduler...")
    python_cmd = get_venv_executable("python")
    app_name = "datasyncher"
    print_info("Press Ctrl+C to stop the Celery beat scheduler.")
    success, output = run_command(f"{python_cmd} -m celery -A {app_name} beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler", check_venv=True, display_output=True)
    if not success:
        print_error(f"Celery beat failed to start. Output:
{output}")
        print_info("Ensure Redis and database are running (via docker-compose dev_up) and Celery configuration is correct.")

def clean():
    """Clean up Python cache files and build artifacts"""
    print_info("Cleaning up Python cache files and build artifacts...")
    deleted_count = 0
    for p in Path('.').rglob('__pycache__'):
        try:
            shutil.rmtree(p)
            print_info(f"Deleted: {p}")
            deleted_count += 1
        except OSError as e:
            print_warning(f"Could not delete {p}: {e}")
    for p in Path('.').rglob('.pytest_cache'):
        try:
            shutil.rmtree(p)
            print_info(f"Deleted: {p}")
            deleted_count += 1
        except OSError as e:
            print_warning(f"Could not delete {p}: {e}")
    for f in Path('.').rglob('*.pyc'):
        try:
            os.remove(f)
            print_info(f"Deleted: {f}")
            deleted_count += 1
        except OSError as e:
            print_warning(f"Could not delete {f}: {e}")
    for p in Path('.').rglob('*.egg-info'):
        try:
            shutil.rmtree(p)
            print_info(f"Deleted: {p}")
            deleted_count += 1
        except OSError as e:
            print_warning(f"Could not delete {p}: {e}")
    if deleted_count > 0:
        print_success(f"Cleaned up {deleted_count} items.")
    else:
        print_info("No cache or build artifacts found to clean.")

def help_menu():
    print_info("Available commands:")
    for cmd, func in COMMANDS.items():
        print(f"  {{sys.argv[0]}} {cmd} - {func.__doc__.strip()}")
    print(f"  {{sys.argv[0]}} help - Display this help message.")

COMMANDS = {
    "install": install,
    "format": format_code,
    "lint": lint,
    "dev_up": dev_up,
    "dev_down": dev_down,
    "makemigrations": makemigrations,
    "migrate": migrate,
    "createsuperuser": createsuperuser,
    "runserver": runserver,
    "test": test,
    "shell": shell,
    "worker": celery_worker,
    "beat": celery_beat,
    "clean": clean,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] == "help":
        help_menu()
        return
    command = sys.argv[1]
    if command in COMMANDS:
        COMMANDS[command]()
    else:
        print_error(f"Unknown command: '{command}'")
        help_menu()

if __name__ == "__main__":
    main()
