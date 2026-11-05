from rest_framework import serializers

from gyms.models import Branch


class BranchSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name"]


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "address"]

    def validate_name(self, value):
        request = self.context.get("request")
        gym_id = getattr(getattr(request, "user", None), "gym_id", None)
        if gym_id is None:
            return value
        queryset = Branch.objects.filter(gym_id=gym_id, name=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("A branch with this name already exists in your gym.")
        return value
