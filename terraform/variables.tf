variable "aws_region" {
  description = "AWS region for the infrastructure"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used in resource tags"
  type        = string
  default     = "cloud-api-health-monitor"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}
