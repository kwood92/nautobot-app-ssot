"""Tests for syncing several Proxmox VE instances, one SSOTProxmoxConfig each, into one Nautobot."""

from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Interface
from nautobot.virtualization.models import VirtualMachine

from nautobot_ssot.integrations.proxmox.constants import CONFIG_CUSTOM_FIELD_KEY, SSOT_TAG_NAME

from .proxmox_fixtures import (
    ProxmoxSyncTestMixin,
    _get_device_interface_dict,
    _get_node_device_dict,
    _get_virtual_machine_dict,
    create_default_proxmox_config,
)


class TestMultipleConfigs(ProxmoxSyncTestMixin, TestCase):
    """Each config's sync only changes the objects that config synced."""

    def setUp(self):
        """Create a second config alongside the default one."""
        super().setUp()
        self.other_config = create_default_proxmox_config(name="ProxmoxUnitTestConfigOther")

    def _sync_vms(self, config, *vm_names):
        """Sync the given VMs (in TestCluster) using the given config.

        Args:
            config (SSOTProxmoxConfig): Config to sync with.
            *vm_names (str): Names of the VMs the source reports.
        """
        source = self._source(config)
        self._seed_cluster(source)
        for name in vm_names:
            # Like the Proxmox adapter, always report the SSoT tag, so an unchanged VM has no diff.
            source.add(
                source.virtual_machine(**_get_virtual_machine_dict({"name": name, "tags": [{"name": SSOT_TAG_NAME}]}))
            )
        source.sync_to(self._nb_adapter(config))

    def _sync_node(self, config, node_name, *interface_names):
        """Sync one node Device with the given interfaces using the given config.

        Args:
            config (SSOTProxmoxConfig): Config to sync with.
            node_name (str): Name of the node Device the source reports.
            *interface_names (str): Names of the node's interfaces.
        """
        source = self._source(config)
        self._seed_cluster(source)
        device = source.device(**_get_node_device_dict({"name": node_name}))
        source.add(device)
        for name in interface_names:
            interface = source.device_interface(**_get_device_interface_dict({"name": name, "device__name": node_name}))
            source.add(interface)
            device.add_child(interface)
        source.sync_to(self._nb_adapter(config))

    def test_other_config_sync_keeps_vms(self):
        """Syncing a second config doesn't delete the VMs the first config synced."""
        self._sync_vms(self.config, "vm-a")
        self._sync_vms(self.other_config, "vm-b")

        self.assertTrue(VirtualMachine.objects.filter(name="vm-a").exists())
        self.assertTrue(VirtualMachine.objects.filter(name="vm-b").exists())

    def test_other_config_sync_keeps_node_interfaces(self):
        """Syncing a second config doesn't delete the first config's node interfaces."""
        self._sync_node(self.config, "pve-a", "eth0")
        self._sync_node(self.other_config, "pve-b", "eth0")

        self.assertTrue(Interface.objects.filter(device__name="pve-a", name="eth0").exists())
        self.assertTrue(Interface.objects.filter(device__name="pve-b", name="eth0").exists())

    def _make_unowned(self, vm_name):
        """Clear a VM's owning config, as for a VM synced before ownership was recorded.

        Args:
            vm_name (str): Name of the VM.
        """
        vm = VirtualMachine.objects.get(name=vm_name)
        vm.cf[CONFIG_CUSTOM_FIELD_KEY] = None
        vm.validated_save()

    def test_unowned_vm_is_not_deleted(self):
        """A VM synced before ownership was recorded isn't deleted by a config that doesn't report it."""
        self._sync_vms(self.config, "legacy-vm")
        self._make_unowned("legacy-vm")

        self._sync_vms(self.other_config, "vm-b")

        self.assertTrue(VirtualMachine.objects.filter(name="legacy-vm").exists())

    def test_unowned_vm_is_adopted_by_the_config_that_reports_it(self):
        """An unowned VM that a config reports becomes that config's, so it's deleted once it's gone."""
        self._sync_vms(self.config, "legacy-vm")
        self._make_unowned("legacy-vm")

        self._sync_vms(self.config, "legacy-vm")
        self.assertEqual(VirtualMachine.objects.get(name="legacy-vm").cf[CONFIG_CUSTOM_FIELD_KEY], str(self.config.pk))

        self._sync_vms(self.config)
        self.assertFalse(VirtualMachine.objects.filter(name="legacy-vm").exists())
