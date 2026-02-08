import boto3
import zipfile

region = 'eu-north-1'
client = boto3.client('lambda', region_name=region)

print("Deploying processor...")
with zipfile.ZipFile('proc.zip', 'w') as z:
    z.write('lambda_data_processor.py', 'index.py')

with open('proc.zip', 'rb') as f:
    resp = client.update_function_code(
        FunctionName='data-pipeline-data-processor',
        ZipFile=f.read()
    )
    print("[OK] Processor: {} bytes".format(resp['CodeSize']))

print("Deploying reporter...")
with zipfile.ZipFile('rep.zip', 'w') as z:
    z.write('lambda_report_generator.py', 'index.py')

with open('rep.zip', 'rb') as f:
    resp = client.update_function_code(
        FunctionName='data-pipeline-report-generator',
        ZipFile=f.read()
    )
    print("[OK] Reporter: {} bytes".format(resp['CodeSize']))

print("\n[SUCCESS] All functions deployed!")

