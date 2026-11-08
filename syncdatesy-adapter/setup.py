#!/usr/bin/env python
"""
Enhanced DataSyncher Project Setup with Robust Error Handling and User Prompts
"""
import os
import sys
import platform
import subprocess
import shutil
import json
from pathlib import Path
import time

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}✗ {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")

def ask_to_continue(message):
    """Asks the user if they want to continue despite a warning/error."""
    while True:
        response = input(f"{Colors.YELLOW}{message} (y/N): {Colors.END}").lower().strip()
        if response == 'y':
            print_info("Continuing setup.")
            return True
        elif response == 'n' or response == '':
            print_error("Aborting setup as requested.")
            return False
        else:
            print_warning("Invalid input. Please enter 'y' or 'n'.")

def run_command(cmd, check=True, shell=True, env=None, capture_output=True):
    """Run a command and return success status and output."""
    try:
        if env is None:
            env = os.environ.copy()
        process = subprocess.run(
            cmd,
            shell=shell,
            check=check,
            capture_output=capture_output,
            text=True,
            env=env
        )
        return True, process.stdout
    except subprocess.CalledProcessError as e:
        return False, f"{e.stderr}\n{e.stdout}"
    except FileNotFoundError:
        return False, f"Command not found: {cmd.split(' ')[0]}"
    except Exception as e:
        return False, f"An unexpected error occurred: {e}"

def check_venv_activated():
    """Check if virtual environment is activated."""
    return sys.prefix != sys.base_prefix

def get_python_executable():
    """Get the correct Python executable."""
    if check_venv_activated():
        return sys.executable
    else:
        # Try to find venv Python
        venv_python = Path("venv/Scripts/python.exe") if platform.system() == "Windows" else Path("venv/bin/python")
        if venv_python.exists():
            return str(venv_python)
        # Fallback to system Python if venv not found/activated
        return sys.executable

def get_pip_executable():
    """Get the correct pip executable."""
    python_exe = get_python_executable()
    return f"{python_exe} -m pip"

def check_python_and_venv():
    """Verify Python and virtual environment setup."""
    print_info("Verifying Python executable and virtual environment...")
    python_exe = get_python_executable()
    if not Path(python_exe).exists():
        print_error(f"Python executable not found at '{python_exe}'.")
        print_info("Please ensure Python is installed and accessible, or that your 'venv' directory is correctly set up.")
        return False

    print_success(f"Using Python executable: {python_exe}")

    if not check_venv_activated():
        print_warning("Virtual environment is NOT currently activated.")
        print_info("The setup script will attempt to use the venv Python directly.")
        print_info(f"You will need to activate it manually for subsequent commands (e.g., '{Colors.BOLD}source venv/bin/activate{Colors.END}' or '{Colors.BOLD}venv\\Scripts\\activate{Colors.END}').")
        os.environ['PATH'] = str(Path(python_exe).parent) + os.pathsep + os.environ['PATH']
    else:
        print_success(f"Virtual environment activated: {sys.prefix}")

    return True

def verify_installation():
    """Verify all required Python packages are installed."""
    print_header("Verifying Python Package Installation")
    pip_exe = get_pip_executable()
    if not pip_exe:
        print_error("Could not determine pip executable path. Cannot verify packages.")
        return False

    required_packages = {
        'django': 'Django framework',
        'black': 'Code formatter',
        'isort': 'Import sorter',
        'flake8': 'Code linter',
        'pytest': 'Test framework',
        'psycopg2-binary': 'PostgreSQL adapter',
        'python-environ': 'Environment variables',
        'celery': 'Task queue',
        'redis': 'Redis client',
        'pre-commit': 'Git hooks manager'
    }

    print_info("Checking installed packages...")
    success, output = run_command(f"{pip_exe} list --format=json", check=False)
    if not success:
        print_error(f"Failed to get package list: {output}")
        print_info("This might indicate an issue with your pip installation or virtual environment.")
        return False

    try:
        installed_packages = {pkg['name'].lower(): pkg['version']
                              for pkg in json.loads(output)}
    except json.JSONDecodeError:
        print_error("Failed to parse package list (invalid JSON output from pip).")
        print_info("Please check your pip installation by running 'pip list'.")
        return False
    except Exception as e:
        print_error(f"An unexpected error occurred while parsing package list: {e}")
        return False

    missing_packages = []
    for package, description in required_packages.items():
        if package.lower() in installed_packages:
            print_success(f"{package} ({description}) - v{installed_packages[package.lower()]}")
        else:
            print_error(f"{package} ({description}) - NOT INSTALLED")
            missing_packages.append(package)

    if missing_packages:
        print_warning(f"\nMissing packages: {', '.join(missing_packages)}")
        print_info("Attempting to install missing packages...")
        all_installed_successfully = True
        for package in missing_packages:
            print_info(f"Installing {package}...")
            install_cmd = f"{pip_exe} install {package}"
            success, install_output = run_command(install_cmd, check=False)
            if success:
                print_success(f"{package} installed")
            else:
                print_error(f"Failed to install {package}:\n{install_output}")
                all_installed_successfully = False
        return all_installed_successfully
    else:
        print_success("All required Python packages are installed.")
        return True

def setup_env_file():
    """Creates a basic .env file if it doesn't exist."""
    print_header("Setting up .env File")
    env_file = Path(".env")
    if env_file.exists():
        print_info(f"'.env' file already exists at '{env_file}'. Skipping creation.")
        return True

    print_info("'.env' file not found. Creating a basic template...")
    env_content = """
# Django settings
DEBUG=True
SECRET_KEY=your-insecure-dev-secret-key-change-in-prod
ALLOWED_HOSTS=localhost,127.0.0.1,.ngrok.io

# Database (PostgreSQL)
DATABASE_URL=postgres://datasyncher_user:datasyncher_password@db:5432/datasyncher_db

# Redis for Celery and Cache
REDIS_URL=redis://redis:6379/0

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Optional: Email (for dev, you might use 'console' or 'filebased')
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend

USE_HTTPS=False
"""
    try:
        with open(env_file, "w") as f:
            f.write(env_content.strip())
        print_success(f"'.env' file created at '{env_file}'.")
        print_info("Remember to update SECRET_KEY and other settings as needed for production.")
        return True
    except IOError as e:
        print_error(f"Failed to create '.env' file: {e}")
        return False

def setup_docker_check():
    """Check if Docker is running and containers are up."""
    print_header("Docker Environment Check")
    print_info("Checking Docker CLI installation...")
    success, output = run_command("docker --version", check=False)
    if not success:
        print_error(f"Docker is not installed or not in PATH: {output.strip()}")
        print_info("Install Docker Desktop from https://docker.com and ensure it's in your system's PATH.")
        return False
    print_success(f"Docker CLI installed: {output.strip()}")

    print_info("Checking Docker daemon status...")
    success, _ = run_command("docker info", check=False, capture_output=False)
    if not success:
        print_error("Docker daemon is not running.")
        print_info("Please start Docker Desktop or your Docker daemon.")
        return False
    print_success("Docker daemon is running.")

    compose_files = [
        "deploy/docker-compose/docker-compose.yml",
        "docker-compose.yml",
        "deploy/docker-compose.yml"
    ]
    compose_file = None
    for file in compose_files:
        if Path(file).exists():
            compose_file = file
            break
    if not compose_file:
        print_error("docker-compose.yml not found in common locations.")
        print_info("Please ensure it exists in the project root or 'deploy/docker-compose/'.")
        return False
    print_success(f"Found docker-compose file: {compose_file}")

    print_info("Checking Docker containers status (via docker-compose ps)...")
    success, output = run_command(f"docker-compose -f {compose_file} ps", check=False)
    if success and "Up" in output:
        print_info("Docker containers status:")
        print(output)
        if "db" in output and "Up" in output:
            print_success("Database container ('db') is running.")
        else:
            print_warning("Database container ('db') might not be running or healthy.")
            if not ask_to_continue("Do you want to attempt starting/restarting Docker containers now?"):
                return False
            print_info("Starting Docker containers...")
            success, start_output = run_command(f"docker-compose -f {compose_file} up -d", check=False)
            if success:
                print_success("Docker containers started.")
                print_info("Waiting for database to be ready (10 seconds)...")
                time.sleep(10)
                success_recheck, output_recheck = run_command(f"docker-compose -f {compose_file} ps", check=False)
                if success_recheck and "db" in output_recheck and "Up" in output_recheck:
                    print_success("Database container is now running.")
                else:
                    print_error("Database container did not come up successfully after starting.")
                    print_info(output_recheck)
                    return False
            else:
                print_error(f"Failed to start Docker containers:\n{start_output}")
                return False
    else:
        print_warning("No Docker containers are currently running for this project.")
        print_info("Output of `docker-compose ps`:\n" + output)
        if not ask_to_continue("Do you want to start Docker containers now?"):
            return False
        print_info("Starting Docker containers...")
        success, start_output = run_command(f"docker-compose -f {compose_file} up -d", check=False)
        if success:
            print_success("Docker containers started.")
            print_info("Waiting for database to be ready (10 seconds)...")
            time.sleep(10)
            success_recheck, output_recheck = run_command(f"docker-compose -f {compose_file} ps", check=False)
            if success_recheck and "db" in output_recheck and "Up" in output_recheck:
                print_success("Database container is now running.")
            else:
                print_error("Database container did not come up successfully after starting.")
                print_info(output_recheck)
                return False
        else:
            print_error(f"Failed to start Docker containers:\n{start_output}")
            return False
    return True

def create_enhanced_task_runner():
    """Create an enhanced task runner with better error handling."""
    print_header("Creating Enhanced Task Runner")
    task_runner_content = '''#!/usr/bin/env python
"""Enhanced Task Runner for DataSyncher with Error Handling"""
import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path
import time

class Colors:
    GREEN = '\\033[92m'
    RED = '\\033[91m'
    BLUE = '\\033[94m'
    YELLOW = '\\033[93m'
    END = '\\033[0m'
    BOLD = '\\033[1m'

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
            print("  venv\\Scripts\\activate")
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
            print_error(f"Linting failed. Issues found:\n{output}")
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
        print_error(f"Failed to start containers. Output:\n{output}")
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
        print_error(f"Failed to stop containers. Output:\n{output}")

def makemigrations():
    """Create Django migrations"""
    print_info("Creating Django migrations...")
    python_cmd = get_venv_executable("python")
    success, output = run_command(f"{python_cmd} manage.py makemigrations", check_venv=True)
    if success:
        print_success("Migrations created.")
        print(output)
    else:
        print_error(f"Failed to create migrations. Output:\n{output}")
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
        print_error(f"Migration failed. Output:\n{output}")
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
        print_error(f"Failed to create superuser. Output:\n{output}")
        print_info("Ensure database container is running and migrations are applied.")

def runserver():
    """Run Django development server"""
    print_info("Starting Django development server (http://localhost:8000)...")
    python_cmd = get_venv_executable("python")
    print_info("Press Ctrl+C to stop the server.")
    success, output = run_command(f"{python_cmd} manage.py runserver 0.0.0.0:8000", check_venv=True, display_output=True)
    if not success:
        print_error(f"Django development server failed to start. Output:\n{output}")
        print_info("Check for port conflicts or Django errors.")

def test():
    """Run tests"""
    print_info("Running tests with Pytest...")
    pytest_cmd = get_venv_executable("pytest")
    success, output = run_command(f"{pytest_cmd}", check_venv=True)
    if success:
        print_success("All tests passed!")
    else:
        print_error(f"Tests failed. Output:\n{output}")

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
        print_error(f"Celery worker failed to start. Output:\n{output}")
        print_info("Ensure Redis is running (via docker-compose dev_up) and Celery configuration is correct.")

def celery_beat():
    """Start Celery beat scheduler"""
    print_info("Starting Celery beat scheduler...")
    python_cmd = get_venv_executable("python")
    app_name = "datasyncher"
    print_info("Press Ctrl+C to stop the Celery beat scheduler.")
    success, output = run_command(f"{python_cmd} -m celery -A {app_name} beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler", check_venv=True, display_output=True)
    if not success:
        print_error(f"Celery beat failed to start. Output:\n{output}")
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
'''

    task_runner_file = Path("task_runner.py")
    try:
        with open(task_runner_file, "w", encoding="utf-8") as f:
            f.write(task_runner_content)
        if platform.system() != "Windows":
            os.chmod(task_runner_file, 0o755)
        print_success(f"Enhanced task runner created at '{task_runner_file}'.")
        print_info(f"You can now run tasks using: python {task_runner_file} <command>")
        if platform.system() != "Windows":
            print_info(f"Or directly: ./{task_runner_file} <command>")
        return True
    except IOError as e:
        print_error(f"Failed to create task_runner.py: {e}")
        return False

def main():
    print_header("DataSyncher Project Setup Script")
    overall_success = True
    if not check_python_and_venv():
        overall_success = False
        if not ask_to_continue("Python/Virtual Environment setup encountered issues."):
            sys.exit(1)
    if not verify_installation():
        overall_success = False
        if not ask_to_continue("Python package verification/installation encountered issues."):
            sys.exit(1)
    if not setup_env_file():
        overall_success = False
        if not ask_to_continue(".env file setup encountered issues."):
            sys.exit(1)
    if not setup_docker_check():
        overall_success = False
        if not ask_to_continue("Docker environment check encountered issues."):
            sys.exit(1)
    if not create_enhanced_task_runner():
        overall_success = False
        if not ask_to_continue("Creating the task runner script encountered issues."):
            sys.exit(1)
    print_header("Setup Process Complete")
    if overall_success:
        print_success("Your DataSyncher project should now be ready for development!")
    else:
        print_warning("Some setup steps encountered issues.")
        print_info("Please review the output above for specific errors and warnings.")
        print_warning("The project might not be fully functional until issues are resolved.")
    print_info("\nNext steps (after activating your virtual environment):")
    print(f"  1. Activate your virtual environment: {Colors.BOLD}source venv/bin/activate{Colors.END} (Linux/macOS) or {Colors.BOLD}venv\\Scripts\\activate{Colors.END} (Windows)")
    print(f"  2. Start Docker containers: {Colors.BOLD}python task_runner.py dev_up{Colors.END}")
    print(f"  3. Apply database migrations: {Colors.BOLD}python task_runner.py migrate{Colors.END}")
    print(f"  4. Create a superuser: {Colors.BOLD}python task_runner.py createsuperuser{Colors.END}")
    print(f"  5. Run the development server: {Colors.BOLD}python task_runner.py runserver{Colors.END}")
    print_info("\nFor a list of all available commands, run: python task_runner.py help")

if __name__ == "__main__":
    main()