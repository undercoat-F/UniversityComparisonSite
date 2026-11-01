# 検索 API の公開用 EC2（Caddy で HTTPS 公開する）。
# クローラー（Control Plane・Worker）とは分け、必要最低限の権限だけを持たせる。
# - IAM ロールは付けない（API は AWS の API を使わない）
# - .env には DOMAIN と、読み取り専用ロールの DB 接続情報だけを置く
# - 80/443 はこの EC2 だけに開ける（crawler のセキュリティグループは受信を SSH のみにする）

resource "aws_security_group" "web" {
  count = var.web_enabled ? 1 : 0

  name        = "${var.security_group_name}-web"
  description = "Public web server (Caddy + search API)"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_allowed_cidr]
  }

  ingress {
    description = "HTTP for Caddy ACME challenges and HTTPS redirects"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.web_allowed_cidr]
  }

  ingress {
    description = "HTTPS served by Caddy"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.web_allowed_cidr]
  }

  ingress {
    description = "HTTP/3 served by Caddy"
    from_port   = 443
    to_port     = 443
    protocol    = "udp"
    cidr_blocks = [var.web_allowed_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.security_group_name}-web"
  }
}

resource "aws_instance" "web" {
  count = var.web_enabled ? 1 : 0

  ami                    = var.ami_id
  instance_type          = var.web_instance_type
  key_name               = var.ec2_key_name
  user_data              = local.docker_bootstrap
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.web[0].id]

  # インスタンスメタデータは IMDSv2（トークン必須）だけにする
  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  tags = {
    "Name" = "UniversityComparison-web"
    "Role" = "web"
  }

  # controlplane と同じ理由で、作成後の user_data の変更は無視する
  lifecycle {
    ignore_changes = [user_data]
  }
}

# DNS の A レコードの向き先。停止・起動してもアドレスが変わらないよう固定する
resource "aws_eip" "web" {
  count = var.web_enabled ? 1 : 0

  domain   = "vpc"
  instance = aws_instance.web[0].id

  tags = {
    "Name" = "UniversityComparison-web"
  }
}

output "web_public_ip" {
  description = "Elastic IP of the web EC2 (point the domain's A record here), or null when disabled"
  value       = var.web_enabled ? aws_eip.web[0].public_ip : null
}
