variable "ami_id" {
  type = string
}

variable "ec2_key_name" {
  type        = string
  description = "Name of the existing EC2 key pair attached to controlplane and worker instances."
}

variable "controlplane_count" {
  type        = number
  description = "Number of producer/control-plane instances. Keep this at 1 to avoid duplicate task production."
  default     = 1

  validation {
    condition     = var.controlplane_count == 1
    error_message = "controlplane_count must be 1 because the producer is intended to run once."
  }
}

variable "worker_count" {
  type        = number
  description = "Number of worker instances. Increase this value to add workers."
  default     = 2

  validation {
    condition     = var.worker_count >= 1 && var.worker_count == floor(var.worker_count)
    error_message = "worker_count must be a positive whole number."
  }
}

variable "subnet_id" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "security_group_name" {
  type = string
}

variable "security_group_description" {
  type = string
}

variable "ssh_allowed_cidr" {
  type = string
}

variable "web_allowed_cidr" {
  type        = string
  description = "CIDR allowed to reach the Caddy HTTP and HTTPS listeners"
  default     = "0.0.0.0/0"
}

variable "benchmark_enabled" {
  type        = bool
  description = "Whether to create the isolated benchmark mock server infrastructure"
  default     = false
}

variable "benchmark_instance_type" {
  type        = string
  description = "EC2 instance type for the benchmark mock server"
  default     = "t3.medium"
}

variable "benchmark_root_volume_size_gb" {
  type        = number
  description = "Encrypted root EBS volume size for the benchmark EC2 operating system"
  default     = 30

  validation {
    condition     = var.benchmark_root_volume_size_gb >= 20
    error_message = "benchmark_root_volume_size_gb must be at least 20 GB."
  }
}

variable "benchmark_data_volume_size_gb" {
  type        = number
  description = "Encrypted persistent EBS volume size for benchmark results"
  default     = 100

  validation {
    condition     = var.benchmark_data_volume_size_gb >= 10
    error_message = "benchmark_data_volume_size_gb must be at least 10 GB."
  }
}

variable "benchmark_domain_suffix" {
  type        = string
  description = "Private Route 53 zone and wildcard domain suffix used by mock scenarios"
  default     = "bench.internal"
}