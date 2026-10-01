resource "aws_s3_bucket" "app" {
  bucket = "${var.project_name}-${var.environment}-${random_id.bucket.hex}"

  tags = {
    Name        = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "random_id" "bucket" {
  byte_length = 4
}
