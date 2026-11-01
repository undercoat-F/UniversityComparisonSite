terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

locals {
  docker_bootstrap = <<-EOF
    #!/bin/bash
    set -e

    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y docker.io docker-compose-v2
    systemctl enable --now docker
    usermod -aG docker ubuntu
  EOF
}

resource "aws_instance" "controlplane" {
  count = var.controlplane_count

  ami           = var.ami_id
  instance_type = "t3.micro"
  key_name      = var.ec2_key_name
  user_data     = local.docker_bootstrap
  vpc_security_group_ids = [
    aws_security_group.crawler.id
  ]
  iam_instance_profile = aws_iam_instance_profile.crawl_queue.name

  subnet_id = var.subnet_id
  tags = {
    "Name" = "UniversityComparison-controlplane-${count.index + 1}"
    "Role" = "controlplane"
  }

  # user_data は初回起動時にしか実行されない。既存インスタンスに後から差分が出ると
  # 停止→再起動（パブリックIPの変更）になるため、作成後の変更は無視する
  lifecycle {
    ignore_changes = [user_data]
  }
}

resource "aws_instance" "worker" {
  count = var.worker_count

  ami           = var.ami_id
  instance_type = "t3.micro"
  key_name      = var.ec2_key_name
  user_data     = local.docker_bootstrap
  vpc_security_group_ids = [
    aws_security_group.crawler.id
  ]
  iam_instance_profile = aws_iam_instance_profile.crawl_queue.name

  subnet_id = var.subnet_id
  tags = {
    "Name"     = "UniversityComparison-worker-${count.index + 1}"
    "Role"     = "worker"
    "WorkerId" = "worker-${count.index + 1}"
  }

  # controlplane と同じ理由で、作成後の user_data の変更は無視する
  lifecycle {
    ignore_changes = [user_data]
  }
}

moved {
  from = aws_instance.crawler
  to   = aws_instance.controlplane[0]
}

output "controlplane_instance_ids" {
  value = aws_instance.controlplane[*].id
}

output "worker_instance_ids" {
  value = aws_instance.worker[*].id
}

output "worker_private_ips" {
  value = aws_instance.worker[*].private_ip
}

# クローラー（Control Plane・Worker）は外向きの通信だけで動くため、受信は SSH のみ許可する。
# 80/443 は公開用の EC2（web.tf）だけに開ける
resource "aws_security_group" "crawler" {
  name        = var.security_group_name
  description = var.security_group_description
  vpc_id      = var.vpc_id

  ingress = [
    {
      cidr_blocks = [
        var.ssh_allowed_cidr,
      ]
      description      = null
      from_port        = 22
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "tcp"
      security_groups  = []
      self             = false
      to_port          = 22
    },
  ]

  egress = [
    {
      cidr_blocks = [
        "0.0.0.0/0",
      ]
      description      = null
      from_port        = 0
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "-1"
      security_groups  = []
      self             = false
      to_port          = 0
    },
  ]

  tags = {}

}
