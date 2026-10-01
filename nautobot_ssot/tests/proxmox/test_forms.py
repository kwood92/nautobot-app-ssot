"""Proxmox VE integration form tests."""

from nautobot.apps.testing import TestCase
from nautobot.dcim.models import DeviceType, Location, LocationType, Manufacturer
from nautobot.extras.choices import (
    SecretsGroupAccessTypeChoices,
    SecretsGroupSecretTypeChoices,
)
from nautobot.extras.models import (
    ExternalIntegration,
    Role,
    Secret,
    SecretsGroup,
    SecretsGroupAssociation,
    Status,
    Tag,
)
from nautobot.virtualization.models import ClusterType

from nautobot_ssot.integrations.proxmox.forms import SSOTProxmoxConfigForm


class SSOTProxmoxConfigFormTestCase(TestCase):
    """Tests for the SSOTProxmoxConfigForm."""

    @classmethod
    def setUpTestData(cls):
        """Set up data shared across tests."""
        cls.good_secrets_group = cls._build_secrets_group("ProxmoxFormGoodSG", with_token=True)
        cls.bad_secrets_group = cls._build_secrets_group("ProxmoxFormBadSG", with_token=False)
        cls.integration = ExternalIntegration.objects.create(
            name="ProxmoxFormUnitTestInstance",
            remote_url="https://placeholder.local:8006",
            secrets_group=cls.good_secrets_group,
            verify_ssl=False,
            timeout=30,
        )
        cls.integration_missing_token = ExternalIntegration.objects.create(
            name="ProxmoxFormUnitTestInstanceMissingToken",
            remote_url="https://placeholder.local:8006",
            secrets_group=cls.bad_secrets_group,
            verify_ssl=False,
            timeout=30,
        )
        cls.ssot_tag = Tag.objects.get_or_create(name="ProxmoxFormUnitTestTag")[0]
        # Not built into Nautobot; normally created by nautobot_database_ready_callback, which isn't run here.
        Status.objects.get_or_create(name="Suspended")
        Status.objects.get_or_create(name="Reserved")
        cls.cluster_type = ClusterType.objects.get_or_create(name="ProxmoxFormUnitTestClusterType")[0]
        location_type, _ = LocationType.objects.get_or_create(name="ProxmoxFormUnitTestLocationType")
        cls.location = Location.objects.get_or_create(
            name="ProxmoxFormUnitTestLocation",
            defaults={"location_type": location_type, "status": Status.objects.get_or_create(name="Active")[0]},
        )[0]
        manufacturer, _ = Manufacturer.objects.get_or_create(name="ProxmoxFormUnitTestManufacturer")
        cls.device_type = DeviceType.objects.get_or_create(
            manufacturer=manufacturer, model="ProxmoxFormUnitTestDeviceType"
        )[0]
        cls.device_role = Role.objects.get_or_create(name="ProxmoxFormUnitTestRole")[0]
        cls.base_form_data = {
            "name": "ProxmoxFormUnitTestConfig",
            "description": "",
            "proxmox_instance": cls.integration.pk,
            "enable_sync_to_nautobot": True,
            "use_clusters": True,
            "sync_lxc": True,
            "sync_nodes_as_devices": True,
            "sync_proxmox_tags": True,
            "default_ssot_tag": cls.ssot_tag.pk,
            "default_vm_status_map": '{"running": "Active", "stopped": "Offline", "paused": "Suspended"}',
            "default_ip_status_map": '{"PREFERRED": "Active", "UNKNOWN": "Reserved"}',
            "default_node_interface_type_map": "{}",
            "primary_ip_sort_by": "Lowest",
            "default_ignore_link_local": True,
            "job_enabled": False,
            "default_clustergroup_name": "Proxmox VE Default Cluster Group",
            "default_cluster_name": "Proxmox VE Default Cluster",
            "default_cluster_type": cls.cluster_type.pk,
            "default_location": cls.location.pk,
            "default_device_type": cls.device_type.pk,
            "default_device_role": cls.device_role.pk,
        }

    @staticmethod
    def _build_secrets_group(name, with_token):
        """Create a Secrets Group with a REST Username secret and, optionally, a REST Token secret.

        Args:
            name (str): Secrets Group name, also used as the Secret name prefix.
            with_token (bool): Whether to add the REST Token secret.

        Returns:
            SecretsGroup: The created or existing group.
        """
        secrets_group, _ = SecretsGroup.objects.get_or_create(name=name)
        token_id, _ = Secret.objects.get_or_create(
            name=f"{name}-id",
            defaults={"provider": "environment-variable", "parameters": {"variable": "NAUTOBOT_SSOT_PROXMOX_TOKEN_ID"}},
        )
        SecretsGroupAssociation.objects.get_or_create(
            secrets_group=secrets_group,
            access_type=SecretsGroupAccessTypeChoices.TYPE_REST,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
            defaults={"secret": token_id},
        )
        if with_token:
            token_secret, _ = Secret.objects.get_or_create(
                name=f"{name}-secret",
                defaults={
                    "provider": "environment-variable",
                    "parameters": {"variable": "NAUTOBOT_SSOT_PROXMOX_TOKEN_SECRET"},
                },
            )
            SecretsGroupAssociation.objects.get_or_create(
                secrets_group=secrets_group,
                access_type=SecretsGroupAccessTypeChoices.TYPE_REST,
                secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
                defaults={"secret": token_secret},
            )
        return secrets_group

    def test_saving_does_not_change_the_external_integration(self):
        """The config form only links an ExternalIntegration; it never edits it, even if asked to."""
        form_data = dict(self.base_form_data)
        # Values a crafted POST could carry for the integration's settings.
        form_data.update(
            {
                "proxmox_secrets_group": self.bad_secrets_group.pk,
                "proxmox_remote_url": "https://attacker.example:8006",
                "proxmox_verify_ssl": False,
                "proxmox_timeout": 45,
            }
        )
        form = SSOTProxmoxConfigForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.integration.refresh_from_db()
        self.assertEqual(self.integration.remote_url, "https://placeholder.local:8006")
        self.assertEqual(self.integration.secrets_group, self.good_secrets_group)
        self.assertEqual(self.integration.timeout, 30)

    def test_linking_integration_without_token_secret_is_rejected(self):
        """Linking an ExternalIntegration whose Secrets Group lacks a REST Token fails validation."""
        form_data = dict(self.base_form_data)
        form_data["proxmox_instance"] = self.integration_missing_token.pk
        form = SSOTProxmoxConfigForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("proxmox_instance", form.errors)
        self.assertIn("Token", form.errors["proxmox_instance"][0])

    def test_secrets_and_secrets_group_are_never_created_by_the_form(self):
        """Saving the form creates no Secret or SecretsGroup objects."""
        secret_count_before = Secret.objects.count()
        secrets_group_count_before = SecretsGroup.objects.count()
        form = SSOTProxmoxConfigForm(data=dict(self.base_form_data))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(Secret.objects.count(), secret_count_before)
        self.assertEqual(SecretsGroup.objects.count(), secrets_group_count_before)
