from .models import Notification, UserProfile


def user_panel(request):
    if not request.user.is_authenticated:
        return {"finv_version": "4.1"}

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    unread_count = 0
    if request.user.has_perm("inventory.view_auditlog"):
        unread_count = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
    return {
        "current_profile": profile,
        "unread_notifications_count": unread_count,
        "finv_version": "4.1",
    }
