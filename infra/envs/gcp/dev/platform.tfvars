# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
platform = {
  name        = "shared"
  environment = "dev"
  network = {
    cidr = "10.60.0.0/16"
  }
  cluster = {
    binary_authorization = false
  }
}
