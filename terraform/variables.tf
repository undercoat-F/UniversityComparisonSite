variable "ami_id" {
  type = string
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