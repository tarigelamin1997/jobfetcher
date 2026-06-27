# ses.tf — SES sender identity for the daily digest.
#
# WHAT: verifies the sender email identity.
# WHY:  v0 sends one daily email from `var.sender_email`. Creating the identity here
#       triggers AWS's verification email; the address is only usable once confirmed.
# SO-WHAT: the notifier (build-plan Step 6) sends from a verified identity.
#
# SANDBOX NOTE: a fresh SES account starts in SANDBOX — you can only send TO and FROM
#   *verified* identities (fine for v0: one sender + one recipient). Moving to
#   production sending (arbitrary recipients) requires a support request; that is
#   intentionally NOT automated in Terraform (it's an account-level, human-gated ask).
#   For v0, also verify the recipient address manually (or via a second identity).

resource "aws_ses_email_identity" "sender" {
  email = var.sender_email
}
