// ============================================================================
// Event-Driven Data Processing Pipeline - Terraform Configuration
// ============================================================================
// This configuration deploys a complete event-driven data processing pipeline
// on AWS with automated daily report generation.

terraform {
  required_version = ">= 1.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment for remote state management
  # backend "s3" {
  #   bucket         = "terraform-state-bucket"
  #   key            = "data-pipeline/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-locks"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "DataPipeline"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

// ============================================================================
// VARIABLES
// ============================================================================

variable "aws_region" {
  description = "AWS region for resource deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
  
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod"
  }
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "data-pipeline"
}

variable "raw_data_retention_days" {
  description = "Number of days to retain raw data in S3"
  type        = number
  default     = 90
}

variable "processed_data_retention_days" {
  description = "Number of days to keep processed data before archival"
  type        = number
  default     = 365
}

variable "lambda_timeout_seconds" {
  description = "Lambda function timeout in seconds"
  type        = number
  default     = 60
  
  validation {
    condition     = var.lambda_timeout_seconds >= 3 && var.lambda_timeout_seconds <= 900
    error_message = "Lambda timeout must be between 3 and 900 seconds"
  }
}

variable "lambda_memory_mb" {
  description = "Lambda function memory allocation in MB"
  type        = number
  default     = 256
  
  validation {
    condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 10240
    error_message = "Lambda memory must be between 128 and 10240 MB"
  }
}

variable "rds_instance_class" {
  description = "RDS instance type"
  type        = string
  default     = "db.t3.small"
}

variable "rds_allocated_storage" {
  description = "RDS allocated storage in GB"
  type        = number
  default     = 20
}

variable "enable_enhanced_monitoring" {
  description = "Enable RDS enhanced monitoring"
  type        = bool
  default     = false
}

variable "daily_report_time_utc" {
  description = "Time (HH:MM) in UTC to generate daily reports"
  type        = string
  default     = "08:00"
  
  validation {
    condition     = can(regex("^([0-1][0-9]|2[0-3]):[0-5][0-9]$", var.daily_report_time_utc))
    error_message = "daily_report_time_utc must be in HH:MM format (00:00 - 23:59)"
  }
}

variable "alert_email" {
  description = "Email address for alert notifications"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags to apply to resources"
  type        = map(string)
  default     = {}
}

// ============================================================================
// DATA SOURCES
// ============================================================================

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

// ============================================================================
// LOCAL VALUES
// ============================================================================

locals {
  common_tags = merge(
    var.tags,
    {
      Environment = var.environment
      Project     = var.project_name
    }
  )
  
  raw_bucket_name       = "${var.project_name}-raw-${data.aws_caller_identity.current.account_id}"
  processed_bucket_name = "${var.project_name}-processed-${data.aws_caller_identity.current.account_id}"
  reports_bucket_name   = "${var.project_name}-reports-${data.aws_caller_identity.current.account_id}"
  
  report_hour = tonumber(split(":", var.daily_report_time_utc)[0])
  report_min  = tonumber(split(":", var.daily_report_time_utc)[1])
}

// ============================================================================
// S3 BUCKETS
// ============================================================================

// Raw data ingestion bucket
resource "aws_s3_bucket" "raw_data" {
  bucket = local.raw_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id
  
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id

  rule {
    id     = "delete-old-raw-data"
    status = "Enabled"

    filter {}

    expiration {
      days = var.raw_data_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_notification" "raw_data_events" {
  bucket      = aws_s3_bucket.raw_data.id
  eventbridge = true
}

// Processed data bucket
resource "aws_s3_bucket" "processed_data" {
  bucket = local.processed_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "processed_data" {
  bucket = aws_s3_bucket.processed_data.id
  
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "processed_data" {
  bucket = aws_s3_bucket.processed_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "processed_data" {
  bucket = aws_s3_bucket.processed_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "processed_data" {
  bucket = aws_s3_bucket.processed_data.id

  rule {
    id     = "archive-old-processed-data"
    status = "Enabled"

    filter {}

    transition {
      days          = var.processed_data_retention_days
      storage_class = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

// Reports bucket
resource "aws_s3_bucket" "reports" {
  bucket = local.reports_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "archive-old-reports"
    status = "Enabled"

    filter {}

    transition {
      days          = 365
      storage_class = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

// ============================================================================
// NETWORKING (VPC)
// ============================================================================

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.common_tags, { Name = "${var.project_name}-vpc" })
}

resource "aws_subnet" "private_1" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]
  tags              = merge(local.common_tags, { Name = "${var.project_name}-private-subnet-1" })
}

resource "aws_subnet" "private_2" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = data.aws_availability_zones.available.names[1]
  tags              = merge(local.common_tags, { Name = "${var.project_name}-private-subnet-2" })
}

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda-sg"
  description = "Security group for Lambda functions"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-lambda-sg"
  })
}

/*
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Security group for RDS database"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${var.project_name}-rds-sg" })

  ingress {
    description = "PostgreSQL from Lambda"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
*/

// ============================================================================
// RDS DATABASE - DISABLED (NOT FREE TIER COMPATIBLE)
// ============================================================================
// Note: RDS exceeds free tier limits. Use DynamoDB for data storage instead.
// Uncomment below and upgrade AWS account if you need SQL database.

/*
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = [aws_subnet.private_1.id, aws_subnet.private_2.id]
  tags       = merge(local.common_tags, { Name = "${var.project_name}-db-subnet-group" })
}

resource "random_password" "rds_password" {
  length  = 16
  special = true
  # Exclude characters that require escaping in PostgreSQL connection strings
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project_name}-db"
  engine         = "postgres"
  engine_version = "15.3"
  instance_class = var.rds_instance_class

  allocated_storage     = var.rds_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true
  multi_az              = var.environment == "prod" ? true : false

  db_name  = "datapipeline"
  username = "postgres"
  password = random_password.rds_password.result

  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.rds.id]
  publicly_accessible             = false
  copy_tags_to_snapshot           = true
  backup_retention_period         = 1
  backup_window                   = "02:00-03:00"
  maintenance_window              = "sun:03:00-sun:04:00"
  enabled_cloudwatch_logs_exports = ["postgresql"]
  
  skip_final_snapshot       = true

  deletion_protection = false

  tags = merge(local.common_tags, { Name = "${var.project_name}-db" })

  depends_on = [aws_security_group.rds]
}
*/

// ============================================================================
// DYNAMODB TABLES
// ============================================================================

resource "aws_dynamodb_table" "event_metadata" {
  name             = "${var.project_name}-event-metadata"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "event_id"
  range_key        = "timestamp"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "event_id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "N"
  }

  attribute {
    name = "source"
    type = "S"
  }

  global_secondary_index {
    name            = "source-timestamp-index"
    hash_key        = "source"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  ttl {
    attribute_name = "expiration_time"
    enabled        = true
  }

  tags = merge(local.common_tags, { Name = "${var.project_name}-event-metadata" })
}

resource "aws_dynamodb_table" "report_history" {
  name           = "${var.project_name}-report-history"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "report_date"
  range_key      = "report_id"

  attribute {
    name = "report_date"
    type = "S"
  }

  attribute {
    name = "report_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, { Name = "${var.project_name}-report-history" })
}

// ============================================================================
// IAM ROLES AND POLICIES
// ============================================================================

// Lambda execution role for data processing
resource "aws_iam_role" "lambda_processing" {
  name               = "${var.project_name}-lambda-processing-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "lambda_processing_policy" {
  name   = "${var.project_name}-lambda-processing-policy"
  role   = aws_iam_role.lambda_processing.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*"
      },
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = [
          "${aws_s3_bucket.raw_data.arn}/*",
          "${aws_s3_bucket.processed_data.arn}/*"
        ]
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem"
        ]
        Resource = aws_dynamodb_table.event_metadata.arn
      },
        # Resource = aws_secretsmanager_secret.rds_credentials.arn
      
    ]
  })
}

// Lambda execution role for report generation
resource "aws_iam_role" "lambda_reporting" {
  name               = "${var.project_name}-lambda-reporting-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_processing_vpc" {
  role       = aws_iam_role.lambda_processing.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_reporting_vpc" {
  role       = aws_iam_role.lambda_reporting.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_reporting_policy" {
  name   = "${var.project_name}-lambda-reporting-policy"
  role   = aws_iam_role.lambda_reporting.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*"
      },
      {
        Sid    = "S3ReportsAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.reports.arn,
          "${aws_s3_bucket.reports.arn}/*"
        ]
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:GetItem"
        ]
        Resource = [
          aws_dynamodb_table.event_metadata.arn,
          aws_dynamodb_table.report_history.arn
        ]
      },
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = aws_sns_topic.alerts.arn
      }
    ]
  })
}

// ============================================================================
// SECRETS MANAGER - DISABLED (RDS REMOVED)
// ============================================================================

/*
resource "aws_secretsmanager_secret" "rds_credentials" {
  name_prefix             = "${var.project_name}-rds-credentials-"
  recovery_window_in_days = 7
  tags                    = local.common_tags
}


resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = aws_db_instance.main.username
    password = random_password.rds_password.result
    host     = aws_db_instance.main.endpoint
    port     = 5432
    database = aws_db_instance.main.db_name
  })
}
*/

// ============================================================================
// SNS TOPICS
// ============================================================================

resource "aws_sns_topic" "alerts" {
  name              = "${var.project_name}-alerts"
  kms_master_key_id = "alias/aws/sns"
  tags              = local.common_tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

// ============================================================================
// SQS QUEUE
// ============================================================================

resource "aws_sqs_queue" "processing_dlq" {
  name                      = "${var.project_name}-processing-dlq"
  message_retention_seconds = 1209600  // 14 days
  tags                      = local.common_tags
}

resource "aws_sqs_queue" "processing" {
  name                       = "${var.project_name}-processing"
  visibility_timeout_seconds = var.lambda_timeout_seconds + 60
  message_retention_seconds  = 345600  // 4 days
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.processing_dlq.arn
    maxReceiveCount     = 3
  })
  tags = local.common_tags
}

// ============================================================================
// EVENTBRIDGE
// ============================================================================

// Rule for S3 events
resource "aws_cloudwatch_event_rule" "s3_raw_data" {
  name        = "${var.project_name}-s3-raw-data"
  description = "Trigger processing when data uploaded to raw bucket"
  
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.raw_data.id]
      }
    }
  })
  
  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "s3_to_sqs" {
  rule      = aws_cloudwatch_event_rule.s3_raw_data.name
  target_id = "SendToSQS"
  arn       = aws_sqs_queue.processing.arn
  
  dead_letter_config {
    arn = aws_sqs_queue.processing_dlq.arn
  }
}

// Rule for daily report generation
resource "aws_cloudwatch_event_rule" "daily_report" {
  name                = "${var.project_name}-daily-report"
  description         = "Trigger daily report generation"
  schedule_expression = "cron(${local.report_min} ${local.report_hour} * * ? *)"
  tags                = local.common_tags
}

resource "aws_cloudwatch_event_target" "daily_report_lambda" {
  rule      = aws_cloudwatch_event_rule.daily_report.name
  target_id = "ReportLambda"
  arn       = aws_lambda_function.report_generator.arn
}

resource "aws_lambda_permission" "allow_eventbridge_report" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.report_generator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_report.arn
}

// ============================================================================
// LAMBDA FUNCTIONS
// ============================================================================

resource "aws_lambda_function" "data_processor" {
  filename         = "lambda_placeholder.zip"
  function_name    = "${var.project_name}-data-processor"
  role             = aws_iam_role.lambda_processing.arn
  handler          = "index.lambda_handler"
  runtime          = "python3.11"
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  vpc_config {
    subnet_ids         = [aws_subnet.private_1.id, aws_subnet.private_2.id]
     security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      RAW_BUCKET          = aws_s3_bucket.raw_data.id
      PROCESSED_BUCKET    = aws_s3_bucket.processed_data.id
      EVENT_TABLE         = aws_dynamodb_table.event_metadata.name
      ALERTS_TOPIC_ARN    = aws_sns_topic.alerts.arn
      # DB_SECRET_ARN       = aws_secretsmanager_secret.rds_credentials.arn
      ENVIRONMENT         = var.environment
    }
  }

  tags = merge(local.common_tags, { Name = "${var.project_name}-data-processor" })

  depends_on = [aws_iam_role_policy.lambda_processing_policy]
}

resource "aws_lambda_function" "report_generator" {
  filename         = "lambda_placeholder.zip"
  function_name    = "${var.project_name}-report-generator"
  role             = aws_iam_role.lambda_reporting.arn
  handler          = "index.lambda_handler"
  runtime          = "python3.11"
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  vpc_config {
    subnet_ids         = [aws_subnet.private_1.id, aws_subnet.private_2.id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      REPORTS_BUCKET      = aws_s3_bucket.reports.id
      EVENT_TABLE         = aws_dynamodb_table.event_metadata.name
      REPORT_HISTORY_TABLE = aws_dynamodb_table.report_history.name
      # DB_SECRET_ARN       = aws_secretsmanager_secret.rds_credentials.arn
      ALERTS_TOPIC_ARN    = aws_sns_topic.alerts.arn
      ENVIRONMENT         = var.environment
    }
  }

  tags = merge(local.common_tags, { Name = "${var.project_name}-report-generator" })

  depends_on = [aws_iam_role_policy.lambda_reporting_policy]
}

// ============================================================================
// CLOUDWATCH
// ============================================================================

resource "aws_cloudwatch_log_group" "data_processor" {
  name              = "/aws/lambda/${aws_lambda_function.data_processor.function_name}"
  retention_in_days = 7
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "report_generator" {
  name              = "/aws/lambda/${aws_lambda_function.report_generator.function_name}"
  retention_in_days = 7
  tags              = local.common_tags
}

resource "aws_kms_key" "logs" {
  description             = "KMS key for CloudWatch logs encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  tags                    = local.common_tags
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.project_name}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

// Lambda metrics
resource "aws_cloudwatch_metric_alarm" "data_processor_errors" {
  alarm_name          = "${var.project_name}-processor-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Alert when data processor errors exceed threshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.data_processor.function_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "data_processor_duration" {
  alarm_name          = "${var.project_name}-processor-duration"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Average"
  threshold           = 30000  // 30 seconds in milliseconds
  alarm_description   = "Alert when processing duration is abnormally high"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.data_processor.function_name
  }

  tags = local.common_tags
}


// RDS metrics - DISABLED (RDS removed)
/*
resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.project_name}-rds-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Alert when RDS CPU utilization is high"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  tags = local.common_tags
}
*/

// DLQ monitoring
resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  alarm_name          = "${var.project_name}-dlq-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 5
  alarm_description   = "Alert when messages accumulate in DLQ"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    QueueName = aws_sqs_queue.processing_dlq.name
  }

  tags = local.common_tags
}

// ============================================================================
// OUTPUTS
// ============================================================================

output "raw_bucket" {
  description = "Raw data ingestion bucket name"
  value       = aws_s3_bucket.raw_data.id
}

output "processed_bucket" {
  description = "Processed data bucket name"
  value       = aws_s3_bucket.processed_data.id
}

output "reports_bucket" {
  description = "Reports bucket name"
  value       = aws_s3_bucket.reports.id
}

/*
output "rds_endpoint" {
  description = "RDS database endpoint"
  value       = aws_db_instance.main.endpoint
  sensitive   = true
}

output "rds_database" {
  description = "RDS database name"
  value       = aws_db_instance.main.db_name
}
*/

output "data_processor_lambda_arn" {
  description = "Data processor Lambda function ARN"
  value       = aws_lambda_function.data_processor.arn
}

output "report_generator_lambda_arn" {
  description = "Report generator Lambda function ARN"
  value       = aws_lambda_function.report_generator.arn
}

output "event_metadata_table" {
  description = "Event metadata DynamoDB table name"
  value       = aws_dynamodb_table.event_metadata.name
}

output "report_history_table" {
  description = "Report history DynamoDB table name"
  value       = aws_dynamodb_table.report_history.name
}

output "alerts_topic_arn" {
  description = "SNS topic ARN for alerts"
  value       = aws_sns_topic.alerts.arn
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

