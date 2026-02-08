import subprocess
import shutil
import os
import zipfile
from pathlib import Path

def deploy_lambda(function_name, source_file, build_dir):
    """Deploy Lambda with dependencies"""
    
    # Setup
    bd = Path(build_dir)
    if bd.exists():
        shutil.rmtree(bd)
    bd.mkdir(parents=True)
    os.chdir(bd)
    
    print(f"Building {function_name}...")
    
    # Install dependencies to current directory
    subprocess.run(['pip', 'install', 'boto3', 'psycopg2-binary', 'pyarrow', '-t', '.', '-q'], check=True)
    
    # Copy code
    shutil.copy(source_file, 'index.py')
    
    # Create ZIP properly - files at root level
    print(f"Creating ZIP...")
    with zipfile.ZipFile('function.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add all files in current directory to ZIP root
        for root, dirs, files in os.walk('.'):
            for file in files:
                if file != 'function.zip':  # Don't include the zip itself
                    file_path = Path(root) / file
                    arcname = str(file_path).lstrip('./')
                    zf.write(file_path, arcname)
    
    # Deploy
    print(f"Uploading to Lambda...")
    subprocess.run([
        'aws', 'lambda', 'update-function-code',
        '--function-name', function_name,
        '--zip-file', f'fileb://{bd}/function.zip',
        '--region', 'eu-north-1'
    ], check=True)
    
    print(f"OK {function_name} deployed!")

# Deploy both
deploy_lambda('data-pipeline-data-processor', 'C:/Users/DELL/Desktop/Project-2/lambda_data_processor.py', 'C:/temp/lambda_proc')
deploy_lambda('data-pipeline-report-generator', 'C:/Users/DELL/Desktop/Project-2/lambda_report_generator.py', 'C:/temp/lambda_report')

print("\nBoth functions deployed successfully!")
