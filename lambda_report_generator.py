"""
Daily Report Generator Lambda Function
======================================
Generates automated daily summary reports with data processing metrics,
data quality scores, and performance analytics.
"""

import json
import os
import logging
import boto3
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple
from io import StringIO, BytesIO
import base64

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secrets_client = boto3.client('secretsmanager')
sns_client = boto3.client('sns')
ses_client = boto3.client('ses')

# Environment variables
REPORTS_BUCKET = os.environ.get('REPORTS_BUCKET')
EVENT_TABLE = os.environ.get('EVENT_TABLE')
REPORT_HISTORY_TABLE = os.environ.get('REPORT_HISTORY_TABLE')
DB_SECRET_ARN = os.environ.get('DB_SECRET_ARN')
ALERTS_TOPIC_ARN = os.environ.get('ALERTS_TOPIC_ARN')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ============================================================================
# Utility Functions
# ============================================================================

def get_db_credentials() -> Dict[str, str]:
    """Retrieve database credentials from Secrets Manager."""
    try:
        response = secrets_client.get_secret_value(SecretId=DB_SECRET_ARN)
        return json.loads(response['SecretString'])
    except Exception as e:
        logger.error(f"Failed to retrieve database credentials: {str(e)}")
        raise


def get_db_connection():
    """Establish connection to RDS PostgreSQL database."""
    creds = get_db_credentials()
    try:
        conn = psycopg2.connect(
            host=creds['host'].split(':')[0],
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
    """Send SNS alert notification."""
    try:
        sns_client.publish(
            TopicArn=ALERTS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        logger.info(f"Alert sent: {subject}")
    except Exception as e:
        logger.warning(f"Failed to send alert: {str(e)}")


# ============================================================================
# Data Collection Functions
# ============================================================================

def get_events_summary(days: int = 1) -> Dict[str, Any]:
    """
    Get summary statistics of processed events.
    
    Args:
        days: Number of days to analyze
        
    Returns:
        Dictionary with event statistics
    """
    try:
        table = dynamodb.Table(EVENT_TABLE)
        now = int(datetime.utcnow().timestamp())
        cutoff = now - (days * 86400)
        
        # Query events from the past N days
        response = table.scan(
            FilterExpression='#ts > :cutoff',
            ExpressionAttributeNames={'#ts': 'timestamp'},
            ExpressionAttributeValues={':cutoff': cutoff}
        )
        
        events = response.get('Items', [])
        
        # Calculate statistics
        success_count = len([e for e in events if e.get('status') == 'success'])
        failed_count = len([e for e in events if e.get('status') == 'failed'])
        total_records = sum(e.get('record_count', 0) for e in events)
        
        # Group by source
        sources = {}
        for event in events:
            source = event.get('source', 'unknown')
            if source not in sources:
                sources[source] = {'count': 0, 'records': 0}
            sources[source]['count'] += 1
            sources[source]['records'] += event.get('record_count', 0)
        
        return {
            'total_events': len(events),
            'successful_events': success_count,
            'failed_events': failed_count,
            'success_rate': (success_count / len(events) * 100) if events else 0,
            'total_records_processed': total_records,
            'average_records_per_event': total_records / len(events) if events else 0,
            'events_by_source': sources,
            'errors': [e.get('error_message') for e in events if e.get('error_message')]
        }
        
    except Exception as e:
        logger.error(f"Failed to get events summary: {str(e)}")
        return {}


def get_database_statistics() -> Dict[str, Any]:
    """Get statistics from RDS database."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        stats = {}
        
        # Total records
        cur.execute("SELECT COUNT(*) as count FROM processed_data")
        stats['total_records_in_db'] = cur.fetchone()['count']
        
        # Records created today
        cur.execute("""
            SELECT COUNT(*) as count FROM processed_data 
            WHERE DATE(created_at) = CURRENT_DATE
        """)
        stats['records_created_today'] = cur.fetchone()['count']
        
        # Average record size
        cur.execute("""
            SELECT 
                AVG(pg_column_size(data)) as avg_size,
                MAX(pg_column_size(data)) as max_size,
                MIN(pg_column_size(data)) as min_size
            FROM processed_data
        """)
        size_stats = cur.fetchone()
        stats['avg_record_size_bytes'] = size_stats['avg_size'] or 0
        stats['max_record_size_bytes'] = size_stats['max_size'] or 0
        stats['min_record_size_bytes'] = size_stats['min_size'] or 0
        
        # Data quality metrics
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN data::text LIKE '%null%' THEN 1 END) as with_nulls,
                COUNT(DISTINCT data->>'_record_id') as unique_records
            FROM processed_data
            WHERE created_at > NOW() - INTERVAL '1 day'
        """)
        quality = cur.fetchone()
        stats['quality_metrics'] = {
            'total_records': quality['total'],
            'records_with_nulls': quality['with_nulls'],
            'unique_records': quality['unique_records'],
            'null_rate': (quality['with_nulls'] / quality['total'] * 100) if quality['total'] > 0 else 0
        }
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get database statistics: {str(e)}")
        return {}
    finally:
        if conn:
            conn.close()


def get_s3_statistics() -> Dict[str, Any]:
    """Get statistics about S3 data storage."""
    try:
        stats = {}
        
        # Count objects and estimate size for processed bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        
        # Processed data
        try:
            pages = paginator.paginate(Bucket=REPORTS_BUCKET.replace('-reports-', '-processed-'))
            total_size = 0
            object_count = 0
            
            for page in pages:
                for obj in page.get('Contents', []):
                    total_size += obj.get('Size', 0)
                    object_count += 1
            
            stats['processed_data_count'] = object_count
            stats['processed_data_size_gb'] = total_size / (1024 ** 3)
        except:
            stats['processed_data_count'] = 0
            stats['processed_data_size_gb'] = 0
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get S3 statistics: {str(e)}")
        return {}


def get_cloudwatch_metrics() -> Dict[str, Any]:
    """Get CloudWatch metrics for the pipeline."""
    try:
        cloudwatch = boto3.client('cloudwatch')
        metrics = {}
        
        now = datetime.utcnow()
        start_time = now - timedelta(hours=24)
        
        # Lambda error metrics
        response = cloudwatch.get_metric_statistics(
            Namespace='AWS/Lambda',
            MetricName='Errors',
            StartTime=start_time,
            EndTime=now,
            Period=3600,  # 1 hour
            Statistics=['Sum']
        )
        
        total_errors = sum(dp['Sum'] for dp in response.get('Datapoints', []))
        metrics['lambda_errors_24h'] = int(total_errors)
        
        # Lambda invocations
        response = cloudwatch.get_metric_statistics(
            Namespace='AWS/Lambda',
            MetricName='Invocations',
            StartTime=start_time,
            EndTime=now,
            Period=3600,
            Statistics=['Sum']
        )
        
        total_invocations = sum(dp['Sum'] for dp in response.get('Datapoints', []))
        metrics['lambda_invocations_24h'] = int(total_invocations)
        
        return metrics
        
    except Exception as e:
        logger.warning(f"Failed to get CloudWatch metrics: {str(e)}")
        return {}


# ============================================================================
# Report Generation Functions
# ============================================================================

def generate_report_data() -> Dict[str, Any]:
    """
    Generate comprehensive report data.
    
    Returns:
        Dictionary with all report metrics
    """
    logger.info("Generating report data...")
    
    report_date = datetime.utcnow().strftime('%Y-%m-%d')
    
    report = {
        'report_date': report_date,
        'generated_at': datetime.utcnow().isoformat(),
        'environment': ENVIRONMENT,
        'events_summary': get_events_summary(days=1),
        'database_statistics': get_database_statistics(),
        's3_statistics': get_s3_statistics(),
        'cloudwatch_metrics': get_cloudwatch_metrics(),
        'health_status': 'HEALTHY'  # Can be enhanced with logic
    }
    
    # Determine health status
    if report['events_summary'].get('failed_events', 0) > 0:
        report['health_status'] = 'DEGRADED'
    
    if report['events_summary'].get('success_rate', 100) < 95:
        report['health_status'] = 'WARNING'
    
    logger.info(f"Report data generated. Health status: {report['health_status']}")
    return report


def generate_json_report(report_data: Dict[str, Any]) -> str:
    """Generate JSON formatted report."""
    return json.dumps(report_data, indent=2, default=str)


def generate_html_report(report_data: Dict[str, Any]) -> str:
    """
    Generate HTML formatted report for email distribution.
    
    Args:
        report_data: Dictionary with report metrics
        
    Returns:
        HTML string
    """
    events = report_data['events_summary']
    db = report_data['database_statistics']
    metrics = report_data['cloudwatch_metrics']
    
    html = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 5px; }}
                .header {{ border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
                .status {{ font-size: 20px; margin: 10px 0; }}
                .status.healthy {{ color: #28a745; }}
                .status.warning {{ color: #ffc107; }}
                .status.degraded {{ color: #dc3545; }}
                .metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
                .metric-box {{ background-color: #f9f9f9; padding: 15px; border-left: 4px solid #0066cc; }}
                .metric-label {{ color: #666; font-size: 12px; text-transform: uppercase; }}
                .metric-value {{ font-size: 24px; font-weight: bold; color: #0066cc; margin: 5px 0; }}
                table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
                th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f2f2f2; }}
                .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Data Pipeline Daily Report</h1>
                    <p>Report Date: {report_data['report_date']} | Generated: {report_data['generated_at']}</p>
                </div>
                
                <div class="status {report_data['health_status'].lower()}">
                    Status: {report_data['health_status']}
                </div>
                
                <h2>Events Processing Summary</h2>
                <div class="metric-grid">
                    <div class="metric-box">
                        <div class="metric-label">Total Events</div>
                        <div class="metric-value">{events.get('total_events', 0)}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Success Rate</div>
                        <div class="metric-value">{events.get('success_rate', 0):.1f}%</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Successful Events</div>
                        <div class="metric-value">{events.get('successful_events', 0)}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Failed Events</div>
                        <div class="metric-value">{events.get('failed_events', 0)}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Records Processed</div>
                        <div class="metric-value">{events.get('total_records_processed', 0):,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Avg Records/Event</div>
                        <div class="metric-value">{events.get('average_records_per_event', 0):.0f}</div>
                    </div>
                </div>
                
                <h2>Database Statistics</h2>
                <div class="metric-grid">
                    <div class="metric-box">
                        <div class="metric-label">Total Records</div>
                        <div class="metric-value">{db.get('total_records_in_db', 0):,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Records Today</div>
                        <div class="metric-value">{db.get('records_created_today', 0):,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Avg Record Size</div>
                        <div class="metric-value">{db.get('avg_record_size_bytes', 0):.0f} bytes</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Quality Score</div>
                        <div class="metric-value">{100 - db.get('quality_metrics', {}).get('null_rate', 0):.1f}%</div>
                    </div>
                </div>
                
                <h2>Infrastructure Metrics (24h)</h2>
                <div class="metric-grid">
                    <div class="metric-box">
                        <div class="metric-label">Lambda Invocations</div>
                        <div class="metric-value">{metrics.get('lambda_invocations_24h', 0):,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Lambda Errors</div>
                        <div class="metric-value">{metrics.get('lambda_errors_24h', 0)}</div>
                    </div>
                </div>
                
                <h2>Events by Source</h2>
                <table>
                    <tr>
                        <th>Source</th>
                        <th>Event Count</th>
                        <th>Records Processed</th>
                    </tr>
    """
    
    for source, data in events.get('events_by_source', {}).items():
        html += f"""
                    <tr>
                        <td>{source}</td>
                        <td>{data['count']}</td>
                        <td>{data['records']:,}</td>
                    </tr>
        """
    
    html += """
                </table>
                
                <div class="footer">
                    <p>Automated report generated by Data Pipeline</p>
                    <p>Environment: ENVIRONMENT | Next report: Tomorrow at 08:00 UTC</p>
                </div>
            </div>
        </body>
    </html>
    """.replace('ENVIRONMENT', ENVIRONMENT)
    
    return html


def save_report_to_s3(report_data: Dict[str, Any], 
                     json_content: str, 
                     html_content: str) -> Tuple[str, str]:
    """
    Save report files to S3.
    
    Args:
        report_data: Report data dictionary
        json_content: JSON formatted report
        html_content: HTML formatted report
        
    Returns:
        Tuple of (json_key, html_key)
    """
    try:
        date_str = report_data['report_date']
        
        # Save JSON
        json_key = f"reports/{date_str}/daily_report.json"
        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=json_key,
            Body=json_content.encode('utf-8'),
            ContentType='application/json'
        )
        logger.info(f"Saved JSON report to s3://{REPORTS_BUCKET}/{json_key}")
        
        # Save HTML
        html_key = f"reports/{date_str}/daily_report.html"
        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=html_key,
            Body=html_content.encode('utf-8'),
            ContentType='text/html'
        )
        logger.info(f"Saved HTML report to s3://{REPORTS_BUCKET}/{html_key}")
        
        return json_key, html_key
        
    except Exception as e:
        logger.error(f"Failed to save reports to S3: {str(e)}")
        raise


def save_report_history(report_data: Dict[str, Any], 
                       json_key: str, 
                       html_key: str) -> str:
    """
    Save report history to DynamoDB.
    
    Args:
        report_data: Report data dictionary
        json_key: S3 key for JSON report
        html_key: S3 key for HTML report
        
    Returns:
        Report ID
    """
    try:
        report_id = f"{report_data['report_date']}-{int(datetime.utcnow().timestamp())}"
        
        table = dynamodb.Table(REPORT_HISTORY_TABLE)
        table.put_item(
            Item={
                'report_date': report_data['report_date'],
                'report_id': report_id,
                'generated_at': report_data['generated_at'],
                'health_status': report_data['health_status'],
                's3_json_location': f"s3://{REPORTS_BUCKET}/{json_key}",
                's3_html_location': f"s3://{REPORTS_BUCKET}/{html_key}",
                'events_summary': json.dumps(report_data['events_summary'], default=str),
                'metrics_summary': {
                    'total_events': report_data['events_summary'].get('total_events', 0),
                    'success_rate': report_data['events_summary'].get('success_rate', 0),
                    'records_processed': report_data['events_summary'].get('total_records_processed', 0)
                }
            }
        )
        
        logger.info(f"Saved report history: {report_id}")
        return report_id
        
    except Exception as e:
        logger.error(f"Failed to save report history: {str(e)}")
        raise


# ============================================================================
# Lambda Handler
# ============================================================================

def lambda_handler(event, context):
    """
    Lambda handler for daily report generation.
    
    Triggered by EventBridge scheduled rule daily at specified time.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        Response dictionary with report details
    """
    
    logger.info("Starting daily report generation...")
    
    try:
        # Generate report data
        report_data = generate_report_data()
        
        # Generate formatted reports
        json_report = generate_json_report(report_data)
        html_report = generate_html_report(report_data)
        
        # Save to S3
        json_key, html_key = save_report_to_s3(report_data, json_report, html_report)
        
        # Save history
        report_id = save_report_history(report_data, json_key, html_key)
        
        # Send notification
        if report_data['health_status'] != 'HEALTHY':
            send_alert(
                subject=f"Pipeline Alert: {report_data['health_status']}",
                message=f"Daily report generated with status: {report_data['health_status']}\n\nReport ID: {report_id}\n\nEnvironment: {ENVIRONMENT}"
            )
        
        logger.info(f"Report generation completed successfully. Report ID: {report_id}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'report_id': report_id,
                'report_date': report_data['report_date'],
                'health_status': report_data['health_status'],
                'json_location': f"s3://{REPORTS_BUCKET}/{json_key}",
                'html_location': f"s3://{REPORTS_BUCKET}/{html_key}"
            })
        }
        
    except Exception as e:
        logger.error(f"Report generation failed: {str(e)}", exc_info=True)
        send_alert(
            subject="Report Generation Error",
            message=f"Failed to generate daily report: {str(e)}\n\nEnvironment: {ENVIRONMENT}"
        )
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


if __name__ == "__main__":
    # Local testing
    print(lambda_handler({}, None))
