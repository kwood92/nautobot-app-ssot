"""Forms implementation for SSOT Proxmox VE."""

from django import forms
from nautobot.apps.forms import JSONField, StaticSelect2
from nautobot.dcim.models import DeviceType, Location
from nautobot.extras.forms import NautobotFilterForm, NautobotModelForm
from nautobot.extras.models import Role, Tag
from nautobot.virtualization.models import ClusterType

from .choices import PrimaryIpSortByChoices
from .constants import (
    CLUSTER_TYPE_NAME,
    NODE_DEVICE_ROLE_NAME,
    NODE_DEVICE_TYPE_NAME,
    NODE_LOCATION_NAME,
    SSOT_TAG_NAME,
)
from .models import SSOTProxmoxConfig


class SSOTProxmoxConfigForm(NautobotModelForm):  # pylint: disable=too-many-ancestors
    """SSOTProxmoxConfig creation/edit form."""

    default_vm_status_map = JSONField(
        required=True,
        label="Virtual Machine Status Map",
        help_text="Maps Proxmox VE Virtual Machine statuses to Nautobot statuses.",
    )
    default_ip_status_map = JSONField(
        required=True,
        label="Virtual Machine IP Status Map",
        help_text="Maps Virtual Machine IP statuses to Nautobot statuses.",
    )
    default_node_interface_type_map = JSONField(
        required=False,
        label="Node Interface Type Map",
        help_text="Maps Proxmox VE node interface types (eth, bond, bridge, vlan, ...) to Nautobot interface types.",
    )
    primary_ip_sort_by = forms.ChoiceField(
        choices=PrimaryIpSortByChoices,
        required=True,
        label="Primary IP Sort Logic",
        widget=StaticSelect2(),
    )

    class Meta:
        """Meta attributes for the SSOTProxmoxConfigForm class."""

        model = SSOTProxmoxConfig
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        """Prefill a new config with the default objects created by signals.py."""
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:  # pylint: disable=protected-access
            # The UUID pk is set at instantiation, so `pk` can't detect a new instance.
            # Prefill the defaults created by signals.py; .first() keeps the form rendering if one is missing.
            self.fields["default_ssot_tag"].initial = Tag.objects.filter(name=SSOT_TAG_NAME).first()
            self.fields["default_cluster_type"].initial = ClusterType.objects.filter(name=CLUSTER_TYPE_NAME).first()
            self.fields["default_location"].initial = Location.objects.filter(name=NODE_LOCATION_NAME).first()
            self.fields["default_device_type"].initial = DeviceType.objects.filter(model=NODE_DEVICE_TYPE_NAME).first()
            self.fields["default_device_role"].initial = Role.objects.filter(name=NODE_DEVICE_ROLE_NAME).first()


class SSOTProxmoxConfigFilterForm(NautobotFilterForm):  # pylint: disable=too-many-ancestors
    """Filter form for SSOTProxmoxConfig filter searches."""

    model = SSOTProxmoxConfig

    class Meta:
        """Meta attributes for the SSOTProxmoxConfigFilterForm class."""

        model = SSOTProxmoxConfig
        fields = "__all__"
