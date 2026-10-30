from accounts.permissions import require_staff_permission

# Memberships and payments are gated by the manage_payments staff permission;
# owners always pass. Built from the shared factory so the owner/admin rule
# lives in exactly one place.
CanManageMemberships = require_staff_permission("manage_payments")
