from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from memberships.models import MembershipPayment
from notifications.tasks import send_payment_receipt


@receiver(post_save, sender=MembershipPayment)
def queue_payment_receipt_after_commit(sender, instance, created, **kwargs):
    if not created:
        return
    payment_id = instance.pk
    gym_id = instance.gym_id
    transaction.on_commit(lambda: send_payment_receipt.delay(payment_id, gym_id))
