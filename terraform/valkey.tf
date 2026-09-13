resource"aws_elasticache_serverless_cache" "valkey" {
  name             = "universitycomparisoncache" # AWSコンソールで付けたキャッシュ名
  engine           = "valkey"                   # エンジン名 (valkey または redis)
  
  # 必要に応じてセキュリティグループIDなどを指定
   security_group_ids = [aws_security_group.valkey_sg.id]
}

resource "aws_security_group" "valkey_sg" {
  name        = "university-comparison-valkey-sg"
  description = "Security group for Valkey serverless cache"
  vpc_id      = var.vpc_id

  # インバウンド: crawler EC2 からの 6379 (Valkey/Redis) 通信のみ許可
  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.crawler.id] # EC2のSGをソースに指定
    description     = "Allow Valkey traffic from crawler EC2 instances"
  }

  # アウトバウンド: 全許可
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "UniversityComparison-Valkey-SG"
  }
}

# --- 2. 手動リソースとコードを結びつける import ブロック ---
import {
  to = aws_elasticache_serverless_cache.valkey
  id = "universitycomparisoncache" # AWSコンソール上のキャッシュ名(またはARN)
}

