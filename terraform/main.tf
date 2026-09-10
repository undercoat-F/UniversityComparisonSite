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

resource "aws_instance" "controlplane" {
  count = var.controlplane_count

  ami           = var.ami_id
  instance_type = "t3.micro"
  vpc_security_group_ids = [
    aws_security_group.crawler.id
  ]
  iam_instance_profile = aws_iam_instance_profile.crawl_queue.name

  subnet_id = var.subnet_id
  tags = {
    "Name" = "UniversityComparison-controlplane-${count.index + 1}"
    "Role" = "controlplane"
  }
}

resource "aws_instance" "worker" {
  count = var.worker_count

  ami           = var.ami_id
  instance_type = "t3.micro"
  vpc_security_group_ids = [
    aws_security_group.crawler.id
  ]
  iam_instance_profile = aws_iam_instance_profile.crawl_queue.name

  subnet_id = var.subnet_id
  tags = {
    "Name"      = "UniversityComparison-worker-${count.index + 1}"
    "Role"      = "worker"
    "WorkerId"  = "worker-${count.index + 1}"
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
    {
      cidr_blocks = [
        var.web_allowed_cidr,
      ]
      description      = "HTTP for Caddy ACME challenges and HTTPS redirects"
      from_port        = 80
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "tcp"
      security_groups  = []
      self             = false
      to_port          = 80
    },
    {
      cidr_blocks = [
        var.web_allowed_cidr,
      ]
      description      = "HTTPS served by Caddy"
      from_port        = 443
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "tcp"
      security_groups  = []
      self             = false
      to_port          = 443
    },
    {
      cidr_blocks = [
        var.web_allowed_cidr,
      ]
      description      = "HTTP/3 served by Caddy"
      from_port        = 443
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "udp"
      security_groups  = []
      self             = false
      to_port          = 443
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