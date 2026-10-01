"""DB-backed tests for syncing VLANs from Proxmox VE."""

from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Interface
from nautobot.extras.models import Status
from nautobot.ipam.models import VLAN, VLANGroup
from nautobot.virtualization.models import VMInterface

from nautobot_ssot.integrations.proxmox.constants import SSOT_TAG_NAME, get_vlan_group_name

from .proxmox_fixtures import (
    ProxmoxSyncTestMixin,
    _get_device_interface_dict,
    _get_node_device_dict,
    _get_virtual_machine_dict,
    _get_vm_interface_dict,
    create_default_proxmox_config,
)


class TestVLANSync(ProxmoxSyncTestMixin, TestCase):
    """VLANs and interface VLAN assignments are synced into the config's VLAN Group."""

    def _sync_vm(self, config=None, vm_name="web01", **vlan_attrs):
        """Sync one VM with interface net0 carrying the given VLAN attributes.

        Args:
            config (SSOTProxmoxConfig): Config to sync with; defaults to ``self.config``.
            vm_name (str): Name of the VM the source reports.
            **vlan_attrs (Any): ``mode``/``untagged_vlan__*``/``tagged_vlans`` for net0. ``vids`` lists
                the VLAN IDs the source reports.

        Returns:
            Diff: The diff that was synced.
        """
        config = config or self.config
        vids = vlan_attrs.pop("vids", [])
        source = self._source(config)
        self._seed_cluster(source)
        if vids:
            group_name = get_vlan_group_name(config)
            group = source.vlangroup(name=group_name)
            source.add(group)
            for vid in vids:
                vlan = source.vlan(vid=vid, vlan_group__name=group_name, status__name="Active")
                source.add(vlan)
                group.add_child(vlan)
        vm = source.virtual_machine(**_get_virtual_machine_dict({"name": vm_name, "tags": [{"name": SSOT_TAG_NAME}]}))
        interface = source.interface(
            **_get_vm_interface_dict({"name": "net0", "virtual_machine__name": vm_name, **vlan_attrs})
        )
        source.add(vm)
        source.add(interface)
        vm.add_child(interface)
        return source.sync_to(self._nb_adapter(config))

    def test_vlan_and_interface_assignment_created(self):
        """A tagged VM interface gets its VLAN, created in a VLAN Group named after the config."""
        self._sync_vm(mode="access", untagged_vlan__vid=10, untagged_vlan__vlan_group__name=self.config.name, vids=[10])

        vlan = VLAN.objects.get(vid=10, vlan_group__name=self.config.name)
        self.assertEqual(vlan.name, "VLAN 10")
        interface = VMInterface.objects.get(name="net0", virtual_machine__name="web01")
        self.assertEqual((interface.mode, interface.untagged_vlan), ("access", vlan))
        self.assertTrue(VLANGroup.objects.filter(name=self.config.name).exists())

    def _access(self, vid, config=None):
        """Return the source attributes for an access interface on the given VLAN.

        Args:
            vid (int): The VLAN ID.
            config (SSOTProxmoxConfig): Config whose VLAN Group holds the VLAN; defaults to ``self.config``.

        Returns:
            dict: Keyword arguments for ``_sync_vm``.
        """
        group = (config or self.config).name
        return {"mode": "access", "untagged_vlan__vid": vid, "untagged_vlan__vlan_group__name": group, "vids": [vid]}

    def test_resync_changes_nothing(self):
        """Syncing the same VLAN data twice produces no changes the second time."""
        self._sync_vm(**self._access(10))
        diff = self._sync_vm(**self._access(10))
        summary = diff.summary()
        self.assertEqual((summary["create"], summary["update"], summary["delete"]), (0, 0, 0))

    def test_tagged_mode_and_tagged_vlans(self):
        """A trunk interface gets mode tagged with its tagged VLANs and untagged VLAN."""
        group = self.config.name
        self._sync_vm(
            mode="tagged",
            untagged_vlan__vid=10,
            untagged_vlan__vlan_group__name=group,
            tagged_vlans=[{"vid": 20, "vlan_group__name": group}, {"vid": 30, "vlan_group__name": group}],
            vids=[10, 20, 30],
        )

        interface = VMInterface.objects.get(name="net0", virtual_machine__name="web01")
        self.assertEqual(interface.mode, "tagged")
        self.assertEqual(interface.untagged_vlan.vid, 10)
        self.assertEqual(sorted(vlan.vid for vlan in interface.tagged_vlans.all()), [20, 30])

    def test_renamed_vlan_keeps_its_name(self):
        """A VLAN renamed in Nautobot isn't renamed back by the next sync."""
        self._sync_vm(**self._access(10))
        VLAN.objects.filter(vid=10, vlan_group__name=self.config.name).update(name="Servers")
        self._sync_vm(**self._access(10))
        self.assertEqual(VLAN.objects.get(vid=10, vlan_group__name=self.config.name).name, "Servers")

    def test_vlan_no_longer_reported_is_kept(self):
        """A VLAN the source stops reporting stays in Nautobot."""
        self._sync_vm(**self._access(10))
        self._sync_vm()
        self.assertTrue(VLAN.objects.filter(vid=10, vlan_group__name=self.config.name).exists())

    def test_same_vid_on_two_configs_stays_separate(self):
        """Two configs reporting VLAN 10 each get their own VLAN 10, in their own VLAN Group."""
        other = create_default_proxmox_config(name="ProxmoxUnitTestConfigOther")
        self._sync_vm(**self._access(10))
        self._sync_vm(config=other, vm_name="web02", **self._access(10, config=other))

        self.assertEqual(VLAN.objects.filter(vid=10).count(), 2)
        self.assertEqual(
            set(VLAN.objects.filter(vid=10).values_list("vlan_group__name", flat=True)), {self.config.name, other.name}
        )

    def test_sync_vlans_off_leaves_manual_vlans_alone(self):
        """With sync_vlans off, a VLAN set by hand on a synced interface survives a sync that updates it."""
        self._sync_vm()
        manual_group = VLANGroup.objects.create(name="Manually managed")
        manual_vlan = VLAN.objects.create(
            vid=99, name="Manual", vlan_group=manual_group, status=Status.objects.get(name="Active")
        )
        interface = VMInterface.objects.get(name="net0", virtual_machine__name="web01")
        interface.mode = "access"
        interface.untagged_vlan = manual_vlan
        interface.validated_save()

        self.config.sync_vlans = False
        self.config.validated_save()
        self._sync_vm(mac_address="AA:BB:CC:DD:EE:99")

        interface.refresh_from_db()
        self.assertEqual(interface.mac_address, "AA:BB:CC:DD:EE:99")
        self.assertEqual((interface.mode, interface.untagged_vlan), ("access", manual_vlan))

    def test_node_vlan_interface_gets_its_vlan(self):
        """A node VLAN interface is written as an access interface on its VLAN."""
        group = self.config.name
        source = self._source()
        self._seed_cluster(source)
        vlan_group = source.vlangroup(name=group)
        vlan = source.vlan(vid=50, vlan_group__name=group, status__name="Active")
        source.add(vlan_group)
        source.add(vlan)
        vlan_group.add_child(vlan)
        device = source.device(**_get_node_device_dict({"name": "pve1"}))
        interface = source.device_interface(
            **_get_device_interface_dict(
                {
                    "name": "vlan50",
                    "device__name": "pve1",
                    "type": "virtual",
                    "mode": "access",
                    "untagged_vlan__vid": 50,
                    "untagged_vlan__vlan_group__name": group,
                }
            )
        )
        source.add(device)
        source.add(interface)
        device.add_child(interface)
        source.sync_to(self._nb_adapter())

        nb_interface = Interface.objects.get(device__name="pve1", name="vlan50")
        self.assertEqual((nb_interface.mode, nb_interface.untagged_vlan.vid), ("access", 50))

    def test_configured_vlan_group_is_used(self):
        """When the config names a VLAN Group, VLANs are synced into it instead of one named after the config."""
        shared = VLANGroup.objects.create(name="Shared VLANs")
        self.config.vlan_group = shared
        self.config.validated_save()

        self._sync_vm(mode="access", untagged_vlan__vid=10, untagged_vlan__vlan_group__name="Shared VLANs", vids=[10])

        self.assertTrue(VLAN.objects.filter(vid=10, vlan_group=shared).exists())
        self.assertFalse(VLANGroup.objects.filter(name=self.config.name).exists())
