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

resource "aws_instance" "crawler" {
  ami           = var.ami_id
  instance_type = "t3.micro"
  vpc_security_group_ids = [
    aws_security_group.crawler.id
  ]

  subnet_id = var.subnet_id
  tags = {
    "Name" = "UniversityComparison"
  }
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