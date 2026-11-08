# test_environments_fixed.py
import os
import sys
import subprocess
from pathlib import Path

def test_environment_subprocess(env_name, env_file, settings_module):
    """Test environment in a separate subprocess to avoid Django reload issues"""
    print(f"\n{'='*60}")
    print(f"Testing {env_name} Environment")
    print(f"{'='*60}")
    
    # Check if env file exists
    if not Path(env_file).exists():
        print(f"✗ ERROR: {env_file} not found!")
        return False
    
    # Create a test command
    test_cmd = [
        sys.executable,
        'manage.py',
        'check',
        f'--settings={settings_module}'
    ]
    
    # Set up environment
    env = os.environ.copy()
    
    # Load env file variables
    with open(env_file) as f:
        for line in f:
            if line.strip() and not line.startswith('#') and '=' in line:
                key, value = line.strip().split('=', 1)
                env[key] = value
    
    env['DJANGO_SETTINGS_MODULE'] = settings_module
    
    # Run the check
    try:
        result = subprocess.run(
            test_cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            print(f"✓ {env_name} configuration is valid!")
            print("\nSettings loaded from:", env_file)
            
            # Show key settings
            db_name = env.get('DB_NAME', 'Not set')
            db_host = env.get('DB_HOST', 'Not set')
            debug = env.get('DEBUG', 'Not set')
            
            print(f"  - DB_NAME: {db_name}")
            print(f"  - DB_HOST: {db_host}")
            print(f"  - DEBUG: {debug}")
            
            return True
        else:
            print(f"✗ {env_name} configuration failed!")
            print("\nErrors:")
            print(result.stderr)
            if result.stdout:
                print("\nOutput:")
                print(result.stdout)
            return False
            
    except subprocess.TimeoutExpired:
        print(f"✗ {env_name} check timed out!")
        return False
    except Exception as e:
        print(f"✗ Error testing {env_name}: {e}")
        return False

def main():
    print("DataSyncher Environment Configuration Test")
    print("==========================================")
    
    environments = [
        ('Developer', '.env.developer', 'config.settings.developer'),
        ('QA', '.env.qa', 'config.settings.qa'),
        ('Production', '.env.production', 'config.settings.production'),
    ]
    
    results = []
    for env_name, env_file, settings_module in environments:
        success = test_environment_subprocess(env_name, env_file, settings_module)
        results.append((env_name, success))
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for env_name, success in results:
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"{env_name:15} {status}")

if __name__ == '__main__':
    main()