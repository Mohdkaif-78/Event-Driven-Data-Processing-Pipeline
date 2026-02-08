# Event-Driven Data Processing Pipeline on AWS

## Overview

This project implements a production-grade, fully automated event-driven data processing pipeline on AWS with the following capabilities:

- **Real-time data ingestion** from multiple sources
- **Automated data processing** with quality validation and transformation
- **Multi-format storage** (Parquet, PostgreSQL, JSON)
- **Daily automated reporting** with comprehensive metrics
- **Fault tolerance** with dead-letter queues and error handling
- **Full infrastructure automation** using Terraform
- **CI/CD pipeline** with GitHub Actions
- **Comprehensive monitoring** and alerting

## Architecture Components

### Data Ingestion
- **S3 Buckets**: Raw and processed data storage with versioning and lifecycle policies
- **EventBridge**: Event routing and orchestration
- **SQS**: Message queuing for reliable processing

### Processing
- **Lambda Functions**: Serverless compute for data processing and reporting
- **DynamoDB**: Event metadata and processing state
- **RDS PostgreSQL**: Analytics-ready data warehouse

### Reporting
- **Scheduled Lambda**: Daily automated report generation
- **CloudWatch**: Metrics and monitoring
- **SNS**: Alert notifications

### Infrastructure Management
- **Terraform**: Complete Infrastructure-as-Code
- **GitHub Actions**: Continuous Integration and Deployment
- **AWS Secrets Manager**: Secure credential management

## Project Structure

```
.
├── README.md                          # This file
├── terraform/
│   ├── main.tf                       # Primary infrastructure configuration
│   ├── variables.tf                  # Input variables
│   ├── outputs.tf                    # Output values
│   ├── terraform.tfvars              # Variable values (environment-specific)
│   └── .terraform.lock.hcl           # Dependency lock file
│
├── lambda/
│   ├── data_processor/               # Data processing function
│   │   ├── index.py                  # Main handler
│   │   └── requirements.txt          # Python dependencies
│   │
│   └── report_generator/             # Report generation function
│       ├── index.py                  # Main handler
│       └── requirements.txt          # Python dependencies
│
├── .github/
│   └── workflows/
│       └── deploy.yml                # GitHub Actions CI/CD pipeline
│
├── scripts/
│   ├── deploy_lambdas.sh            # Lambda packaging and deployment
│   ├── verify_deployment.sh         # Post-deployment validation
│   ├── test_pipeline.sh             # End-to-end pipeline testing
│   └── integration_tests.py         # Python integration tests
│
├── docs/
│   ├── ARCHITECTURE.md              # Detailed architecture documentation
│   ├── DEPLOYMENT.md                # Deployment guide
│   ├── MONITORING.md                # Monitoring and alerting setup
│   └── TROUBLESHOOTING.md           # Common issues and solutions
│
└── tests/
    ├── unit/                        # Unit tests for Lambda functions
    ├── integration/                 # Integration tests
    └── fixtures/                    # Test data and mocks
```

## Prerequisites

### Local Development
- AWS Account with appropriate permissions
- AWS CLI v2 (installed and configured)
- Terraform ≥ 1.0
- Python 3.11+
- Git
- Docker (for local testing, optional)

### For CI/CD
- GitHub repository
- GitHub Secrets configured (see below)
- AWS IAM Role for GitHub Actions (OIDC Provider)

## Installation & Deployment

### Step 1: Clone Repository

```bash
git clone https://github.com/your-org/data-pipeline.git
cd data-pipeline
```

### Step 2: Configure AWS Credentials

**Option A: AWS CLI Profile (Local Development)**
```bash
aws configure --profile data-pipeline
export AWS_PROFILE=data-pipeline
```

**Option B: Environment Variables**
```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

**Option C: OIDC with GitHub Actions (Recommended)**
Follow: https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect

### Step 3: Configure GitHub Secrets

Add the following secrets to your GitHub repository:

```
AWS_ROLE_ARN              # IAM role ARN for OIDC
TF_STATE_BUCKET           # S3 bucket for Terraform state
TF_LOCK_TABLE             # DynamoDB table for state locking
SLACK_WEBHOOK_URL         # (Optional) Slack notification webhook
```

Pre-create the S3 bucket and DynamoDB table:

```bash
# Create S3 bucket for Terraform state
aws s3api create-bucket \
    --bucket data-pipeline-terraform-state-$(aws sts get-caller-identity --query Account --output text) \
    --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
    --bucket data-pipeline-terraform-state-$(aws sts get-caller-identity --query Account --output text) \
    --versioning-configuration Status=Enabled

# Create DynamoDB table for state locking
aws dynamodb create-table \
    --table-name terraform-locks \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region us-east-1
```

### Step 4: Initialize Terraform

```bash
cd terraform

# Initialize Terraform
terraform init

# Validate configuration
terraform validate

# Format code
terraform fmt -recursive .
```

### Step 5: Plan Infrastructure

```bash
# Development environment
terraform plan \
    -var="environment=dev" \
    -var="aws_region=us-east-1" \
    -var="alert_email=your-email@example.com" \
    -out=tfplan

# Production environment
terraform plan \
    -var="environment=prod" \
    -var="aws_region=us-east-1" \
    -var="rds_instance_class=db.t3.medium" \
    -var="alert_email=your-email@example.com" \
    -out=tfplan
```

### Step 6: Deploy Infrastructure

```bash
# Apply the plan
terraform apply tfplan

# Save outputs
terraform output -json > outputs.json
```

### Step 7: Build and Deploy Lambda Functions

```bash
cd ..  # Return to project root

# Make deployment script executable
chmod +x scripts/deploy_lambdas.sh

# Deploy all functions
./scripts/deploy_lambdas.sh all us-east-1

# Or deploy specific function
./scripts/deploy_lambdas.sh data-processor us-east-1
```

### Step 8: Verify Deployment

```bash
# Run verification script
./scripts/verify_deployment.sh

# Check CloudFormation stack
aws cloudformation describe-stacks \
    --stack-name terraform-20240101000000000000 \
    --region us-east-1
```

## Usage

### Ingesting Data

**Via S3 Direct Upload:**
```bash
# Upload CSV file
aws s3 cp data.csv s3://data-pipeline-raw-123456789/

# Upload JSON file
aws s3 cp data.json s3://data-pipeline-raw-123456789/

# Upload in batch
aws s3 sync ./data/ s3://data-pipeline-raw-123456789/ --exclude "*.tmp"
```

**Via Lambda (Direct Invocation):**
```bash
aws lambda invoke \
    --function-name data-pipeline-data-processor \
    --payload '{"data": [{"id": 1, "value": "test"}]}' \
    response.json

cat response.json
```

**Via REST API:**
```bash
# If API Gateway configured
curl -X POST https://api-endpoint.execute-api.us-east-1.amazonaws.com/prod/ingest \
    -H "Content-Type: application/json" \
    -d '{"data": [{"id": 1, "value": "test"}]}'
```

### Querying Processed Data

**From RDS:**
```bash
# Get database endpoint from Terraform outputs
DB_ENDPOINT=$(terraform output -raw rds_endpoint)

# Connect using psql
psql -h $DB_ENDPOINT -U postgres -d datapipeline

# Example queries
SELECT COUNT(*) FROM processed_data;
SELECT * FROM processed_data LIMIT 10;
SELECT data ->> 'field_name' AS field, COUNT(*) FROM processed_data GROUP BY field;
```

**From S3 (Parquet):**
```bash
# Download Parquet files
aws s3 sync s3://data-pipeline-processed-123456789/processed/ ./processed_data/

# Query with AWS Athena
aws athena start-query-execution \
    --query-string "SELECT COUNT(*) FROM processed_data" \
    --query-execution-context Database=default \
    --result-configuration OutputLocation=s3://query-results/
```

### Accessing Daily Reports

```bash
# List generated reports
aws s3 ls s3://data-pipeline-reports-123456789/reports/

# Download latest report
aws s3 cp \
    s3://data-pipeline-reports-123456789/reports/$(date +%Y-%m-%d)/daily_report.html \
    ./report.html

# Subscribe to email reports (SNS)
aws sns subscribe \
    --topic-arn $(terraform output -raw alerts_topic_arn) \
    --protocol email \
    --notification-endpoint your-email@example.com
```

## Configuration

### Environment Variables

**Terraform Variables** (`terraform.tfvars`):
```hcl
aws_region                    = "us-east-1"
environment                   = "prod"
project_name                  = "data-pipeline"
lambda_timeout_seconds        = 300
lambda_memory_mb              = 512
rds_instance_class            = "db.t3.medium"
daily_report_time_utc         = "08:00"
alert_email                   = "ops-team@example.com"
raw_data_retention_days       = 90
processed_data_retention_days = 365
enable_enhanced_monitoring    = true
```

**Lambda Environment Variables** (set by Terraform):
- `RAW_BUCKET`: S3 bucket for raw data
- `PROCESSED_BUCKET`: S3 bucket for processed data
- `EVENT_TABLE`: DynamoDB table for event metadata
- `REPORT_HISTORY_TABLE`: DynamoDB table for report history
- `ALERTS_TOPIC_ARN`: SNS topic for alerts
- `DB_SECRET_ARN`: Secrets Manager ARN for database credentials
- `ENVIRONMENT`: Environment name

### Customization

**Change Daily Report Time:**
```bash
terraform apply \
    -var="daily_report_time_utc=14:00" \
    -var="environment=prod"
```

**Increase Lambda Memory for Larger Datasets:**
```bash
terraform apply \
    -var="lambda_memory_mb=1024" \
    -var="environment=prod"
```

**Modify Data Retention Policies:**
```bash
terraform apply \
    -var="raw_data_retention_days=180" \
    -var="processed_data_retention_days=730" \
    -var="environment=prod"
```

## Monitoring & Alerts

### CloudWatch Dashboards

Automatically created dashboards display:
- Data processing metrics
- Lambda performance
- RDS utilization
- Error rates and DLQ depth

Access via AWS Console:
```
CloudWatch > Dashboards > data-pipeline-*
```

### Alarms

Configured alarms notify via SNS when:
- Lambda error rate > 1%
- Processing latency p99 > 30 seconds
- DLQ message depth > 5 messages
- RDS CPU utilization > 80%
- Data becomes stale > 1 hour

Configure email notifications:
```bash
aws sns subscribe \
    --topic-arn $(terraform output -raw alerts_topic_arn) \
    --protocol email \
    --notification-endpoint your-email@example.com
```

### Logs

View Lambda logs:
```bash
aws logs tail /aws/lambda/data-pipeline-data-processor --follow
aws logs tail /aws/lambda/data-pipeline-report-generator --follow
```

Query logs:
```bash
aws logs start-query \
    --log-group-name /aws/lambda/data-pipeline-data-processor \
    --start-time $(date -d '1 hour ago' +%s) \
    --end-time $(date +%s) \
    --query-string 'fields @timestamp, @message, @duration | stats count() by @duration'
```

## Testing

### Unit Tests
```bash
pip install pytest pytest-cov boto3 moto

pytest tests/unit/ -v --cov=lambda/
```

### Integration Tests
```bash
# Test data processing pipeline
./scripts/test_pipeline.sh

# Manual test with sample data
aws s3 cp tests/fixtures/sample.json s3://$(terraform output -raw raw_bucket)/
```

### End-to-End Test
```bash
python scripts/integration_tests.py \
    --bucket $(terraform output -raw raw_bucket) \
    --region us-east-1 \
    --environment prod
```

## Troubleshooting

### Lambda Function Not Triggering

1. Check EventBridge rule:
```bash
aws events list-rules --name-prefix data-pipeline-
aws events list-targets-by-rule --rule data-pipeline-s3-raw-data
```

2. Verify S3 bucket notifications:
```bash
aws s3api get-bucket-notification-configuration \
    --bucket $(terraform output -raw raw_bucket)
```

3. Check Lambda permissions:
```bash
aws lambda get-policy --function-name data-pipeline-data-processor
```

### Database Connection Issues

1. Verify RDS security group:
```bash
aws ec2 describe-security-groups \
    --group-ids $(terraform output -raw rds_security_group_id)
```

2. Test connection from Lambda VPC:
```bash
aws lambda invoke \
    --function-name data-pipeline-data-processor \
    --payload '{"test": true}' \
    response.json
```

3. Check database logs:
```bash
aws rds describe-db-logs \
    --db-instance-identifier $(terraform output -raw rds_db_id)
```

### Dead-Letter Queue Accumulating Messages

1. Check DLQ contents:
```bash
aws sqs receive-message \
    --queue-url $(terraform output -raw dlq_url) \
    --max-number-of-messages 10
```

2. Analyze failed message:
```bash
aws dynamodb scan \
    --table-name data-pipeline-event-metadata \
    --filter-expression "attribute_exists(error_message)"
```

3. Replay messages:
```bash
aws sqs send-message \
    --queue-url $(terraform output -raw processing_queue_url) \
    --message-body '{...}'
```

## Cost Optimization

### Estimated Monthly Costs (Development Environment)
- Lambda: ~$2-5
- RDS (t3.small): ~$20-30
- S3: ~$5-10
- DynamoDB: ~$1-2
- **Total: ~$30-50/month**

### Cost Reduction Strategies
1. Use RDS Reserved Instances (30-40% discount for 1-year)
2. Enable S3 Intelligent-Tiering for automatic cost optimization
3. Set appropriate CloudWatch Logs retention (default: 7 days)
4. Use Lambda provisioned concurrency only for predictable workloads
5. Archive old reports to Glacier ($0.004/GB/month vs $0.023/GB/month)

## Security Best Practices

✅ **Implemented**
- Encryption at rest (S3, RDS, DynamoDB)
- Encryption in transit (HTTPS/TLS)
- VPC isolation for RDS
- IAM least-privilege policies
- Secrets Manager for credentials
- API request logging (CloudTrail)
- Data versioning and point-in-time recovery

⚠️ **Additional Recommendations**
- Enable MFA for AWS console access
- Use CloudTrail for audit logging
- Implement VPC flow logs
- Enable S3 block public access
- Regular security assessments
- Implement data encryption key rotation

## Support & Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Terraform state lock | `terraform force-unlock <LOCK_ID>` |
| Lambda timeout | Increase `lambda_timeout_seconds` |
| Out of memory | Increase `lambda_memory_mb` |
| RDS connection failed | Check security group and VPC connectivity |
| S3 access denied | Verify IAM role permissions |

### Getting Help

1. Check logs: `aws logs tail /aws/lambda/data-pipeline-*`
2. Review CloudTrail: AWS Console > CloudTrail
3. Check AWS Status: https://status.aws.amazon.com/
4. GitHub Issues: Create an issue with logs and configuration

## Contributing

1. Create feature branch
2. Make changes and test locally
3. Submit pull request
4. Wait for GitHub Actions checks to pass
5. Request code review
6. Merge to main (auto-deploys)

## License

MIT License - See LICENSE file for details

## Changelog

### v1.0.0 (2024-01-01)
- Initial release
- Core event-driven pipeline functionality
- Automated reporting
- Full Terraform IaC
- GitHub Actions CI/CD

---

**Last Updated:** 2024-01-01
**Maintainer:** Data Engineering Team
**Support:** ops-team@example.com


