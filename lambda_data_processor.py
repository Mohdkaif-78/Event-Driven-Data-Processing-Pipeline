"""
Data Processing Lambda Function
===============================
Processes incoming events from S3, applies data transformations,
validates quality, and stores results in multiple formats.
"""

import json
import os
import logging
import boto3
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Dict, Any, List, Tuple
import base64
from io import BytesIO
import csv
import pyarrow.parquet as pq
import pyarrow as pa

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secrets_client = boto3.client('secretsmanager')
sns_client = boto3.client('sns')

# Environment variables
RAW_BUCKET = os.environ.get('RAW_BUCKET')
PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET')
EVENT_TABLE = os.environ.get('EVENT_TABLE')
ALERTS_TOPIC_ARN = os.environ.get('ALERTS_TOPIC_ARN')
DB_SECRET_ARN = os.environ.get('DB_SECRET_ARN')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ============================================================================
# Utility Functions
# ============================================================================

def get_db_credentials() -> Dict[str, str]:
    """
    Retrieve database credentials from AWS Secrets Manager.
    
    Returns:
        Dictionary with database connection parameters
    """
    try:
        response = secrets_client.get_secret_value(SecretId=DB_SECRET_ARN)
        return json.loads(response['SecretString'])
    except Exception as e:
        logger.error(f"Failed to retrieve database credentials: {str(e)}")
        raise


def get_db_connection():
    """
    Establish connection to RDS PostgreSQL database.
    
    Returns:
        psycopg2 connection object
    """
    creds = get_db_credentials()
    try:
        conn = psycopg2.connect(
            host=creds['host'].split(':')[0],  # Remove port from endpoint
            port=int(creds.get('port', 5432)),
            database=creds['database'],
            user=creds['username'],
            password=creds['password']
        )
        logger.info("Successfully connected to RDS database")
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to RDS: {str(e)}")
        raise


def send_alert(subject: str, message: str) -> None:
    """
    Send SNS alert notification.
    
    Args:
        subject: Alert subject line
        message: Alert message body
    """
    try:
        sns_client.publish(
            TopicArn=ALERTS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        logger.info(f"Alert sent: {subject}")
    except Exception as e:
        logger.warning(f"Failed to send alert: {str(e)}")


def log_event_metadata(event_id: str, source: str, status: str, 
                       record_count: int, error_message: str = None) -> None:
    """
    Log event processing metadata to DynamoDB.
    
    Args:
        event_id: Unique event identifier
        source: Data source identifier
        status: Processing status (success/failed)
        record_count: Number of records processed
        error_message: Error details if processing failed
    """
    try:
        table = dynamodb.Table(EVENT_TABLE)
        table.put_item(
            Item={
                'event_id': event_id,
                'timestamp': int(datetime.utcnow().timestamp()),
                'source': source,
                'status': status,
                'record_count': record_count,
                'error_message': error_message or '',
                'processed_at': datetime.utcnow().isoformat(),
                'environment': ENVIRONMENT,
                'expiration_time': int(datetime.utcnow().timestamp()) + (90 * 86400)  # 90 days TTL
            }
        )
        logger.info(f"Logged event metadata: {event_id}")
    except Exception as e:
        logger.error(f"Failed to log event metadata: {str(e)}")


def check_duplicate_event(event_id: str) -> bool:
    """
    Check if event has already been processed (idempotency check).
    
    Args:
        event_id: Event identifier to check
        
    Returns:
        True if event already processed, False otherwise
    """
    try:
        table = dynamodb.Table(EVENT_TABLE)
        response = table.get_item(Key={'event_id': event_id})
        return 'Item' in response
    except Exception as e:
        logger.warning(f"Failed to check duplicate: {str(e)}")
        return False


# ============================================================================
# Data Processing Functions
# ============================================================================

def extract_data_from_s3(bucket: str, key: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extract data from S3 object.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        Tuple of (data records list, data format)
    """
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read()
        
        # Determine file format and parse accordingly
        if key.endswith('.json'):
            data = json.loads(content.decode('utf-8'))
            file_format = 'json'
        elif key.endswith('.csv'):
            csv_reader = csv.DictReader(content.decode('utf-8').split('\n'))
            data = list(csv_reader)
            file_format = 'csv'
        else:
            # Try JSON first, fall back to text
            try:
                data = json.loads(content.decode('utf-8'))
                file_format = 'json'
            except:
                data = [{'raw_content': content.decode('utf-8')}]
                file_format = 'text'
        
        # Ensure data is a list
        if isinstance(data, dict):
            data = [data]
        
        logger.info(f"Extracted {len(data)} records from {key} ({file_format})")
        return data, file_format
        
    except Exception as e:
        logger.error(f"Failed to extract data from S3: {str(e)}")
        raise


def validate_data_quality(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Validate data quality and apply cleansing transformations.
    
    Args:
        records: List of data records
        
    Returns:
        Tuple of (cleaned records, quality metrics)
    """
    quality_metrics = {
        'total_records': len(records),
        'valid_records': 0,
        'invalid_records': 0,
        'null_values_removed': 0,
        'duplicates_removed': 0,
    }
    
    cleaned = []
    seen_values = set()
    
    for record in records:
        try:
            # Remove null/empty fields
            before_size = len(record)
            record = {k: v for k, v in record.items() if v is not None and v != ''}
            quality_metrics['null_values_removed'] += before_size - len(record)
            
            # Check for duplicates (simple hash-based)
            record_hash = hash(json.dumps(record, sort_keys=True, default=str))
            if record_hash in seen_values:
                quality_metrics['duplicates_removed'] += 1
                continue
            seen_values.add(record_hash)
            
            # Add metadata
            record['_extracted_at'] = datetime.utcnow().isoformat()
            record['_record_id'] = record_hash
            
            cleaned.append(record)
            quality_metrics['valid_records'] += 1
            
        except Exception as e:
            logger.warning(f"Failed to validate record: {str(e)}")
            quality_metrics['invalid_records'] += 1
            continue
    
    logger.info(f"Data quality check completed: {quality_metrics}")
    return cleaned, quality_metrics


def transform_data(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply data transformations (normalization, enrichment).
    
    Args:
        records: List of data records
        
    Returns:
        Transformed records
    """
    transformed = []
    
    for record in records:
        try:
            # Example transformations:
            # 1. Normalize timestamp fields
            for field in ['timestamp', 'created_at', 'updated_at', 'date']:
                if field in record and isinstance(record[field], str):
                    try:
                        # Parse various date formats
                        if record[field].isdigit() and len(record[field]) == 10:
                            record[f'{field}_unix'] = int(record[field])
                        else:
                            from dateutil import parser
                            dt = parser.parse(record[field])
                            record[f'{field}_unix'] = int(dt.timestamp())
                            record[f'{field}_iso'] = dt.isoformat()
                    except:
                        pass
            
            # 2. Standardize string fields
            for key, value in record.items():
                if isinstance(value, str) and value:
                    # Strip whitespace
                    record[key] = value.strip()
            
            # 3. Add computed fields
            record['_processing_timestamp'] = int(datetime.utcnow().timestamp())
            record['_data_version'] = '1.0'
            
            transformed.append(record)
            
        except Exception as e:
            logger.warning(f"Transformation failed for record: {str(e)}")
            transformed.append(record)  # Keep original on error
    
    return transformed


def load_to_rds(records: List[Dict[str, Any]]) -> int:
    """
    Load processed records to RDS PostgreSQL database.
    
    Args:
        records: List of processed records
        
    Returns:
        Number of records inserted
    """
    if not records:
        return 0
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Create table if not exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_data (
                id SERIAL PRIMARY KEY,
                record_id VARCHAR(255) UNIQUE,
                data JSONB,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                _extracted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert records
        inserted = 0
        for record in records:
            try:
                record_id = record.get('_record_id', '')
                cur.execute(
                    """
                    INSERT INTO processed_data (record_id, data, _extracted_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (record_id) DO NOTHING
                    """,
                    (record_id, json.dumps(record), record.get('_extracted_at'))
                )
                inserted += 1
            except psycopg2.IntegrityError:
                # Record already exists - skip
                logger.debug(f"Record already exists: {record_id}")
                cur.connection.rollback()
            except Exception as e:
                logger.warning(f"Failed to insert record: {str(e)}")
                cur.connection.rollback()
        
        conn.commit()
        logger.info(f"Loaded {inserted} records to RDS")
        return inserted
        
    except Exception as e:
        logger.error(f"Failed to load to RDS: {str(e)}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def save_as_parquet(records: List[Dict[str, Any]], bucket: str, key: str) -> int:
    """
    Save records as Parquet file in S3.
    
    Args:
        records: List of records to save
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        Number of records saved
    """
    if not records:
        return 0
    
    try:
        # Flatten records for Parquet (handle nested structures)
        flattened = []
        for record in records:
            flat = {}
            for k, v in record.items():
                if isinstance(v, (dict, list)):
                    flat[k] = json.dumps(v)
                else:
                    flat[k] = v
            flattened.append(flat)
        
        # Convert to Parquet table
        table = pa.Table.from_pylist(flattened)
        
        # Write to bytes
        buffer = BytesIO()
        pq.write_table(table, buffer, compression='snappy')
        buffer.seek(0)
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        logger.info(f"Saved {len(records)} records as Parquet to s3://{bucket}/{key}")
        return len(records)
        
    except Exception as e:
        logger.error(f"Failed to save as Parquet: {str(e)}")
        raise


# ============================================================================
# Lambda Handler
# ============================================================================

def lambda_handler(event, context):
    """
    Lambda handler for data processing pipeline.
    
    Triggers:
    1. S3 PUT events via EventBridge
    2. SQS messages
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        Response dictionary with processing results
    """
    
    logger.info(f"Processing event: {json.dumps(event)}")
    
    try:
        # Parse event source
        if 'Records' in event:
            # SQS event
            return process_sqs_event(event)
        elif 'detail' in event and event.get('source') == 'aws.s3':
            # EventBridge S3 event
            return process_eventbridge_event(event)
        else:
            # Direct invocation
            return process_direct_event(event)
            
    except Exception as e:
        logger.error(f"Pipeline error: {str(e)}", exc_info=True)
        send_alert(
            subject="Data Pipeline Error",
            message=f"Error processing event: {str(e)}\n\nEnvironment: {ENVIRONMENT}"
        )
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def process_sqs_event(event):
    """Process SQS event records."""
    results = {
        'processed': 0,
        'failed': 0,
        'details': []
    }
    
    for record in event.get('Records', []):
        try:
            # Parse SQS message
            body = json.loads(record['body'])
            
            if 'detail' in body and body.get('source') == 'aws.s3':
                # S3 event within SQS
                result = process_s3_event_detail(body['detail'])
            else:
                # Direct data in message
                result = process_direct_data(body)
            
            results['details'].append(result)
            results['processed'] += 1
            
        except Exception as e:
            logger.error(f"Failed to process SQS record: {str(e)}")
            results['failed'] += 1
    
    return {
        'statusCode': 200,
        'body': json.dumps(results)
    }


def process_eventbridge_event(event):
    """Process EventBridge S3 event."""
    return {
        'statusCode': 200,
        'body': json.dumps(
            process_s3_event_detail(event['detail'])
        )
    }


def process_s3_event_detail(detail):
    """Process S3 event detail."""
    bucket = detail['bucket']['name']
    key = detail['object']['key']
    object_size = detail['object'].get('size', 0)
    
    logger.info(f"Processing S3 object: s3://{bucket}/{key} ({object_size} bytes)")
    
    # Generate event ID
    event_id = f"{bucket}#{key}#{int(datetime.utcnow().timestamp())}"
    
    # Check for duplicates
    if check_duplicate_event(event_id):
        logger.info(f"Skipping duplicate event: {event_id}")
        return {
            'event_id': event_id,
            'status': 'skipped',
            'reason': 'duplicate'
        }
    
    try:
        # Extract data
        records, file_format = extract_data_from_s3(bucket, key)
        
        # Validate quality
        records, quality_metrics = validate_data_quality(records)
        
        # Transform data
        records = transform_data(records)
        
        # Load to destinations
        rds_count = load_to_rds(records)
        
        # Save as Parquet
        date_partition = datetime.utcnow().strftime('year=%Y/month=%m/day=%d')
        parquet_key = f"processed/{date_partition}/{key.split('/')[-1].split('.')[0]}.parquet"
        parquet_count = save_as_parquet(records, PROCESSED_BUCKET, parquet_key)
        
        # Log success
        log_event_metadata(
            event_id=event_id,
            source=bucket,
            status='success',
            record_count=len(records)
        )
        
        logger.info(f"Successfully processed event: {event_id}")
        
        return {
            'event_id': event_id,
            'status': 'success',
            'records_processed': len(records),
            'records_loaded_rds': rds_count,
            'records_saved_parquet': parquet_count,
            'quality_metrics': quality_metrics,
            'parquet_location': f"s3://{PROCESSED_BUCKET}/{parquet_key}"
        }
        
    except Exception as e:
        logger.error(f"Error processing event {event_id}: {str(e)}")
        log_event_metadata(
            event_id=event_id,
            source=bucket,
            status='failed',
            record_count=0,
            error_message=str(e)
        )
        raise


def process_direct_event(event):
    """Process direct data invocation."""
    return {
        'statusCode': 200,
        'body': json.dumps(
            process_direct_data(event)
        )
    }


def process_direct_data(data):
    """Process direct data payload."""
    event_id = f"direct#{int(datetime.utcnow().timestamp())}"
    
    try:
        # Ensure data is a list
        if isinstance(data, dict):
            records = [data]
        else:
            records = data if isinstance(data, list) else [data]
        
        # Validate and transform
        records, quality_metrics = validate_data_quality(records)
        records = transform_data(records)
        
        # Load to RDS
        rds_count = load_to_rds(records)
        
        log_event_metadata(
            event_id=event_id,
            source='direct',
            status='success',
            record_count=len(records)
        )
        
        return {
            'event_id': event_id,
            'status': 'success',
            'records_processed': len(records),
            'records_loaded_rds': rds_count,
            'quality_metrics': quality_metrics
        }
        
    except Exception as e:
        logger.error(f"Error processing direct data: {str(e)}")
        log_event_metadata(
            event_id=event_id,
            source='direct',
            status='failed',
            record_count=0,
            error_message=str(e)
        )
        raise


if __name__ == "__main__":
    # Local testing
    test_event = {
        'detail': {
            'bucket': {'name': 'test-bucket'},
            'object': {'key': 'test.json', 'size': 1024}
        }
    }
    print(lambda_handler(test_event, None))
