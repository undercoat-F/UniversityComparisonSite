resource "aws_sqs_queue" "crawl_tasks_dlq" {
  name                      = "crawl-tasks-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600 # 14日(最大値、失敗メッセージを調査する猶予)

  tags = {
    "Name" = "UniversityComparison"
  }
}

resource "aws_sqs_queue" "crawl_tasks" {
  name                        = "crawl-tasks.fifo"
  fifo_queue                  = true
  content_based_deduplication = false # 重複排除はRedis(DedupStore)側で行うため無効化
  visibility_timeout_seconds  = 120   # ETL_WORKER_TIMEOUT_SEC と揃える
  message_retention_seconds   = 345600 # 4日

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.crawl_tasks_dlq.arn
    maxReceiveCount      = 5
  })

  tags = {
    "Name" = "UniversityComparison"
  }
}

data "aws_iam_policy_document" "crawl_queue_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "crawl_queue" {
  name               = "university-comparison-crawl-queue"
  assume_role_policy = data.aws_iam_policy_document.crawl_queue_assume_role.json
}

data "aws_iam_policy_document" "crawl_queue_access" {
  statement {
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueUrl",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [
      aws_sqs_queue.crawl_tasks.arn,
      aws_sqs_queue.crawl_tasks_dlq.arn,
    ]
  }
}

resource "aws_iam_policy" "crawl_queue_access" {
  name   = "university-comparison-crawl-queue-access"
  policy = data.aws_iam_policy_document.crawl_queue_access.json
}

resource "aws_iam_role_policy_attachment" "crawl_queue_access" {
  role       = aws_iam_role.crawl_queue.name
  policy_arn = aws_iam_policy.crawl_queue_access.arn
}

resource "aws_iam_instance_profile" "crawl_queue" {
  name = "university-comparison-crawl-queue"
  role = aws_iam_role.crawl_queue.name
}

output "crawl_tasks_queue_url" {
  value = aws_sqs_queue.crawl_tasks.url
}

output "crawl_queue_instance_profile_name" {
  value = aws_iam_instance_profile.crawl_queue.name
}
