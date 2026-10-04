resource "aws_security_group" "benchmark" {
  count = var.benchmark_enabled ? 1 : 0

  name        = "${var.security_group_name}-benchmark"
  description = "Private access to the benchmark mock server"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTPS from crawler instances"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.crawler.id]
  }

  ingress {
    description = "SSH from the configured operator CIDR"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_allowed_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.security_group_name}-benchmark"
    Role = "benchmark"
  }
}

resource "aws_instance" "benchmark" {
  count = var.benchmark_enabled ? 1 : 0

  ami                         = var.ami_id
  instance_type               = var.benchmark_instance_type
  key_name                    = var.ec2_key_name
  user_data                   = <<-EOF
    #!/bin/bash
    set -euo pipefail

    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y git python3 python3-pip python3-venv e2fsprogs

    volume_serial="$(printf '%s' "${aws_ebs_volume.benchmark_data[0].id}" | tr -d '-')"
    for attempt in $(seq 1 120); do
      device="$(lsblk -dn -o NAME,SERIAL | awk -v serial="$volume_serial" '$2 == serial { print "/dev/" $1; exit }')"
      if [ -b "$device" ]; then
        break
      fi
      sleep 1
    done
    if [ ! -b "$device" ]; then
      echo "Benchmark data volume was not found" >&2
      exit 1
    fi

    if ! blkid "$device" >/dev/null 2>&1; then
      mkfs.ext4 -F "$device"
    fi
    mkdir -p /var/lib/benchmark
    volume_uuid="$(blkid -s UUID -o value "$device")"
    if ! grep -q "^UUID=$volume_uuid " /etc/fstab; then
      echo "UUID=$volume_uuid /var/lib/benchmark ext4 defaults,nofail 0 2" >> /etc/fstab
    fi
    mount /var/lib/benchmark
    chown ubuntu:ubuntu /var/lib/benchmark
  EOF
  vpc_security_group_ids      = [aws_security_group.benchmark[0].id]
  subnet_id                   = var.subnet_id
  associate_public_ip_address = true

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.benchmark_root_volume_size_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "UniversityComparison-benchmark-mock"
    Role = "benchmark"
  }
}

data "aws_subnet" "benchmark" {
  count = var.benchmark_enabled ? 1 : 0

  id = var.subnet_id
}

resource "aws_ebs_volume" "benchmark_data" {
  count = var.benchmark_enabled ? 1 : 0

  availability_zone = data.aws_subnet.benchmark[0].availability_zone
  type              = "gp3"
  size              = var.benchmark_data_volume_size_gb
  encrypted         = true

  tags = {
    Name = "UniversityComparison-benchmark-results"
    Role = "benchmark"
  }
}

resource "aws_volume_attachment" "benchmark_data" {
  count = var.benchmark_enabled ? 1 : 0

  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.benchmark_data[0].id
  instance_id = aws_instance.benchmark[0].id
}

resource "aws_route53_zone" "benchmark" {
  count = var.benchmark_enabled ? 1 : 0

  name    = var.benchmark_domain_suffix
  comment = "Private DNS for the benchmark mock server"

  vpc {
    vpc_id = var.vpc_id
  }

  tags = {
    Name = "UniversityComparison-benchmark"
    Role = "benchmark"
  }
}

resource "aws_route53_record" "benchmark_wildcard" {
  count = var.benchmark_enabled ? 1 : 0

  zone_id = aws_route53_zone.benchmark[0].zone_id
  name    = "*.${var.benchmark_domain_suffix}"
  type    = "A"
  ttl     = 30
  records = [aws_instance.benchmark[0].private_ip]
}

output "benchmark_instance_id" {
  description = "EC2 instance ID for the benchmark mock server, or null when disabled"
  value       = var.benchmark_enabled ? aws_instance.benchmark[0].id : null
}

output "benchmark_private_ip" {
  description = "Private IP receiving benchmark mock traffic, or null when disabled"
  value       = var.benchmark_enabled ? aws_instance.benchmark[0].private_ip : null
}

output "benchmark_public_ip" {
  description = "Public IP for SSH administration, or null when disabled"
  value       = var.benchmark_enabled ? aws_instance.benchmark[0].public_ip : null
}

output "benchmark_data_volume_id" {
  description = "Persistent EBS volume for benchmark results, or null when disabled"
  value       = var.benchmark_enabled ? aws_ebs_volume.benchmark_data[0].id : null
}

output "benchmark_wildcard_domain" {
  description = "Wildcard private DNS name used by benchmark scenarios, or null when disabled"
  value       = var.benchmark_enabled ? "*.${var.benchmark_domain_suffix}" : null
}