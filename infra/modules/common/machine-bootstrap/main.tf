# The cloud-neutral half of the cloud machine: a cloud-init document that turns a fresh Ubuntu 24.04 machine on any
# cloud into the same Docker Compose stack a laptop runs with `make full`. Each cloud's `machine` module only has to
# boot Ubuntu with this as user data. Nothing secret goes in here: user data is readable by anyone who can describe
# the instance.
locals {
  settings = {
    STACK           = var.stack
    GIT_URL         = var.git_url
    GIT_REF         = var.git_ref
    AUTO_STOP_HOURS = tostring(var.auto_stop_hours)
    OWNER           = var.ssh_user
  }

  # Everything the stacks publish, reached from the laptop as http://localhost:<port> through `ssh caseforge`.
  # Docker publishes these on the machine's interfaces, so each cloud's firewall must admit SSH only.
  forwarded_ports = {
    app        = 8000
    keycloak   = 8080
    temporal   = 8233
    flink      = 8081
    grafana    = 3000
    prometheus = 9090
    jaeger     = 16686
    marquez    = 3001
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    ssh_user       = var.ssh_user
    ssh_public_key = trimspace(var.ssh_public_key)
    settings       = join("\n", [for k in sort(keys(local.settings)) : "${k}=${local.settings[k]}"])
    helper         = base64encode(file("${path.module}/files/caseforge.sh"))
    service        = base64encode(templatefile("${path.module}/files/caseforge.service.tftpl", { ssh_user = var.ssh_user }))
    autostop       = base64encode(file("${path.module}/files/caseforge-autostop.service"))
  })
}
