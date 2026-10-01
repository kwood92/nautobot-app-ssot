"""Unit tests for the Proxmox VE DiffSync source adapter (DB-free)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from diffsync.exceptions import ObjectNotFound

from nautobot_ssot.integrations.proxmox.diffsync.adapters.adapter_proxmox import (
    ProxmoxDiffSync,
    bytes_to_gb,
    bytes_to_mb,
    mac_from_net_parts,
    parse_net_config,
)

from .proxmox_fixtures import json_fixture, real_path


def _default_config():
    """Build a stand-in SSOTProxmoxConfig for the adapter.

    Returns:
        SimpleNamespace: Object with the attributes the adapter reads.
    """
    return SimpleNamespace(
        name="Proxmox Test Config",
        sync_vlans=True,
        vlan_group=None,
        use_clusters=True,
        sync_lxc=True,
        sync_nodes_as_devices=True,
        sync_proxmox_tags=True,
        default_ssot_tag=SimpleNamespace(name="SSoT Synced from Proxmox"),
        default_vm_status_map={"running": "Active", "stopped": "Offline", "paused": "Suspended"},
        default_ip_status_map={"PREFERRED": "Active", "UNKNOWN": "Reserved"},
        default_node_interface_type_map=None,
        primary_ip_sort_by="Lowest",
        default_ignore_link_local=True,
        default_clustergroup_name="Proxmox VE Default Cluster Group",
        default_cluster_name="Proxmox VE Default Cluster",
        default_cluster_type=SimpleNamespace(name="Proxmox VE"),
        default_location=SimpleNamespace(name="Proxmox VE Default Location"),
        default_device_type=SimpleNamespace(model="Proxmox Node"),
        default_device_role=SimpleNamespace(name="Proxmox Node"),
    )


def _load_fixture_adapter():
    """Load an adapter from the JSON fixtures through a mocked client.

    Returns:
        tuple: The mocked client and the loaded ``ProxmoxDiffSync``.
    """
    client = MagicMock()
    client.get_cluster_status.return_value = json_fixture(f"{real_path}/cluster_status.json")
    client.get_nodes.return_value = json_fixture(f"{real_path}/nodes.json")
    client.get_resources.return_value = json_fixture(f"{real_path}/resources_vm.json")
    client.get_qemu_config.return_value = json_fixture(f"{real_path}/qemu_config.json")
    client.get_qemu_agent_interfaces.return_value = json_fixture(f"{real_path}/qemu_agent_interfaces.json")
    client.get_lxc_config.return_value = json_fixture(f"{real_path}/lxc_config.json")
    client.get_node_network.return_value = json_fixture(f"{real_path}/node_network.json")
    client.get_node_status.return_value = json_fixture(f"{real_path}/node_status.json")
    adapter = ProxmoxDiffSync(
        job=MagicMock(), sync=MagicMock(), client=client, config=_default_config(), cluster_filters=None
    )
    adapter.load()
    return client, adapter


def _load_single_qemu_vm(qemu_config, agent_interfaces=None, config=None, node_network=None):
    """Load an adapter whose only VM is the QEMU VM ``web01`` with the given config.

    Args:
        qemu_config (dict): The QEMU VM config returned by the client.
        agent_interfaces (list): Guest agent interfaces; empty means the agent is unavailable.
        config (SimpleNamespace): Stand-in SSOTProxmoxConfig; defaults to ``_default_config()``.
        node_network (list): Node network entries returned for every node.

    Returns:
        ProxmoxDiffSync: The loaded adapter.
    """
    client = MagicMock()
    client.get_cluster_status.return_value = json_fixture(f"{real_path}/cluster_status.json")
    client.get_nodes.return_value = json_fixture(f"{real_path}/nodes.json")
    client.get_resources.return_value = [json_fixture(f"{real_path}/resources_vm.json")[0]]
    client.get_qemu_config.return_value = qemu_config
    client.get_qemu_agent_interfaces.return_value = agent_interfaces or []
    client.get_node_network.return_value = node_network or []
    client.get_node_status.return_value = {}
    adapter = ProxmoxDiffSync(
        job=MagicMock(), sync=MagicMock(), client=client, config=config or _default_config(), cluster_filters=None
    )
    adapter.load()
    return adapter


class TestProxmoxAdapter(unittest.TestCase):
    """Test cases for ProxmoxDiffSync source loading."""

    def setUp(self):
        """Load the adapter from JSON fixtures through a mocked client."""
        self.client, self.adapter = _load_fixture_adapter()

    def test_cluster_and_group_loaded(self):
        """The cluster and its default ClusterGroup are loaded."""
        cluster = self.adapter.get("cluster", "pve-cluster")
        self.assertEqual(cluster.cluster_type__name, "Proxmox VE")
        self.assertEqual(cluster.cluster_group__name, "Proxmox VE Default Cluster Group")
        self.assertIsNotNone(self.adapter.get("clustergroup", "Proxmox VE Default Cluster Group"))

    def test_nodes_loaded_as_devices(self):
        """Each node is loaded as a Device with the default attributes."""
        for node in ("pve1", "pve2"):
            device = self.adapter.get("device", node)
            self.assertEqual(device.device_type__model, "Proxmox Node")
            self.assertEqual(device.role__name, "Proxmox Node")
            self.assertEqual(device.location__name, "Proxmox VE Default Location")
            self.assertEqual(device.status__name, "Active")
            self.assertEqual(device.clusters, [{"name": "pve-cluster"}])

    def test_node_hardware_custom_fields(self):
        """Node version, CPU, memory and primary IP are loaded."""
        device = self.adapter.get("device", "pve1")
        self.assertEqual(device.pve_version, "pve-manager/8.1.4/example")
        self.assertEqual(device.cpu_count, 16)
        self.assertEqual(device.memory_gb, 62)  # 67430219776 bytes -> GB
        self.assertEqual(device.primary_ip4__host, "10.0.0.1")

    def test_node_interfaces_loaded(self):
        """Node interfaces are loaded with their type, state and MTU."""
        eth0 = self.adapter.get("device_interface", {"name": "eth0", "device__name": "pve1"})
        self.assertEqual(eth0.type, "1000base-t")
        self.assertTrue(eth0.enabled)
        self.assertEqual(eth0.status__name, "Active")
        self.assertEqual(eth0.mtu, 1500)

        vmbr0 = self.adapter.get("device_interface", {"name": "vmbr0", "device__name": "pve1"})
        self.assertEqual(vmbr0.type, "bridge")

        bond0 = self.adapter.get("device_interface", {"name": "bond0", "device__name": "pve1"})
        self.assertEqual(bond0.type, "lag")
        self.assertEqual(bond0.mtu, 9000)

    def test_custom_ssot_tag_name(self):
        """A custom default_ssot_tag is used for the marker tag and applied to synced VMs."""
        config = _default_config()
        config.default_ssot_tag = SimpleNamespace(name="Synced From My PVE")
        adapter = ProxmoxDiffSync(
            job=MagicMock(),
            sync=MagicMock(),
            client=self.client,
            config=config,
            cluster_filters=None,
        )
        adapter.load()

        adapter.get("tag", "Synced From My PVE")
        vm = adapter.get("virtual_machine", {"name": "web01", "cluster__name": "pve-cluster"})
        tag_names = [t["name"] for t in vm.tags]
        self.assertIn("Synced From My PVE", tag_names)
        self.assertNotIn("SSoT Synced from Proxmox", tag_names)

    def test_node_interface_default_type_map_fallback(self):
        """Without a custom type map, eth interfaces map to 1000base-t."""
        eth0 = self.adapter.get("device_interface", {"name": "eth0", "device__name": "pve1"})
        self.assertEqual(eth0.type, "1000base-t")

    def test_node_interface_custom_type_map(self):
        """A custom type map overrides the built-in interface types."""
        config = _default_config()
        config.default_node_interface_type_map = {
            "eth": "10gbase-t",
            "bond": "lag",
            "bridge": "bridge",
            "vlan": "virtual",
        }
        adapter = ProxmoxDiffSync(
            job=MagicMock(),
            sync=MagicMock(),
            client=self.client,
            config=config,
            cluster_filters=None,
        )
        adapter.load()

        eth0 = adapter.get("device_interface", {"name": "eth0", "device__name": "pve1"})
        self.assertEqual(eth0.type, "10gbase-t")
        eth1 = adapter.get("device_interface", {"name": "eth1", "device__name": "pve1"})
        self.assertEqual(eth1.type, "10gbase-t")
        bond0 = adapter.get("device_interface", {"name": "bond0", "device__name": "pve1"})
        self.assertEqual(bond0.type, "lag")
        vmbr0 = adapter.get("device_interface", {"name": "vmbr0", "device__name": "pve1"})
        self.assertEqual(vmbr0.type, "bridge")

    def test_node_interface_topology(self):
        """Node interface bridge, LAG and VLAN parent links are loaded."""
        eth0 = self.adapter.get("device_interface", {"name": "eth0", "device__name": "pve1"})
        self.assertEqual(eth0.bridge__name, "vmbr0")
        eth1 = self.adapter.get("device_interface", {"name": "eth1", "device__name": "pve1"})
        self.assertEqual(eth1.lag__name, "bond0")
        vlan = self.adapter.get("device_interface", {"name": "vmbr0.10", "device__name": "pve1"})
        self.assertEqual(vlan.parent_interface__name, "vmbr0")

    def test_node_interface_ip(self):
        """A node interface IP and its Prefix are loaded."""
        ip = self.adapter.get("ip_address", "10.0.0.1__24__Active")
        self.assertEqual(ip.mask_length, 24)
        self.assertIn({"name": "vmbr0", "device__name": "pve1"}, ip.interfaces)
        prefix = self.adapter.get("prefix", "10.0.0.0__24__Global__Active")
        self.assertEqual(prefix.type, "network")

    def test_template_is_skipped(self):
        """VM templates are not loaded."""
        names = [vm.name for vm in self.adapter.get_all("virtual_machine")]
        self.assertNotIn("ubuntu-template", names)

    def test_qemu_vm_loaded(self):
        """A QEMU VM is loaded with its resources, host, tags and primary IP."""
        vm = self.adapter.get("virtual_machine", {"name": "web01", "cluster__name": "pve-cluster"})
        self.assertEqual(vm.vcpus, 4)
        self.assertEqual(vm.memory, 4096)  # 4 GiB -> MB
        self.assertEqual(vm.disk, 32)  # 32 GiB
        self.assertEqual(vm.status__name, "Active")
        self.assertEqual(vm.host_device, {"name": "pve1"})
        tag_names = [tag["name"] for tag in vm.tags]
        self.assertIn("prod", tag_names)
        self.assertIn("web", tag_names)
        self.assertIn("SSoT Synced from Proxmox", tag_names)
        # Primary IP comes from the guest agent; link-local IPv6 ignored.
        self.assertEqual(vm.primary_ip4__host, "10.0.10.50")
        self.assertIsNone(vm.primary_ip6__host)

    def test_lxc_vm_loaded(self):
        """An LXC container is loaded with its resources, host and primary IP."""
        vm = self.adapter.get("virtual_machine", {"name": "ct-dns", "cluster__name": "pve-cluster"})
        self.assertEqual(vm.vcpus, 1)
        self.assertEqual(vm.memory, 512)
        self.assertEqual(vm.disk, 8)
        self.assertEqual(vm.host_device, {"name": "pve2"})
        self.assertEqual(vm.primary_ip4__host, "10.0.20.10")

    def test_qemu_interface_and_ip(self):
        """A QEMU interface and its guest-agent IP are loaded; link-local IPv6 is skipped."""
        interface = self.adapter.get("interface", {"name": "net0", "virtual_machine__name": "web01"})
        self.assertEqual(interface.mac_address, "AA:BB:CC:DD:EE:01")
        ip = self.adapter.get("ip_address", "10.0.10.50__24__Active")
        self.assertEqual(ip.host, "10.0.10.50")
        self.assertEqual(ip.mask_length, 24)
        self.assertEqual(ip.vm_interfaces, [{"name": "net0", "virtual_machine__name": "web01"}])
        with self.assertRaises(Exception):  # diffsync ObjectNotFound
            self.adapter.get("ip_address", "fe80::a8bb:ccff:fedd:ee01__64__Active")

    def test_lxc_interface_and_ip_from_config(self):
        """An LXC interface, IP and Prefix are loaded from the container config."""
        interface = self.adapter.get("interface", {"name": "eth0", "virtual_machine__name": "ct-dns"})
        self.assertEqual(interface.mac_address, "AA:BB:CC:DD:EE:02")
        ip = self.adapter.get("ip_address", "10.0.20.10__24__Active")
        self.assertEqual(ip.mask_length, 24)
        prefix = self.adapter.get("prefix", "10.0.20.0__24__Global__Active")
        self.assertEqual(prefix.type, "network")

    def test_load_virtual_machines_skips_unknown_status(self):
        """A VM whose status is not in default_vm_status_map is skipped."""
        self.client.get_resources.return_value = [
            {
                "vmid": 999,
                "type": "qemu",
                "node": "pve1",
                "name": "weird-vm",
                "status": "unknownstatus",
                "maxcpu": 1,
                "maxmem": 1073741824,
                "maxdisk": 1073741824,
                "template": 0,
            }
        ]
        adapter = ProxmoxDiffSync(
            job=MagicMock(),
            sync=MagicMock(),
            client=self.client,
            config=_default_config(),
            cluster_filters=None,
        )
        adapter.load()
        names = [vm.name for vm in adapter.get_all("virtual_machine")]
        self.assertNotIn("weird-vm", names)

    def test_load_virtual_machines_skips_duplicate_name(self):
        """Only the first of two same-named VMs in a cluster is loaded."""
        self.client.get_resources.return_value = [
            {
                "vmid": 101,
                "type": "qemu",
                "node": "pve1",
                "name": "dup-vm",
                "status": "running",
                "maxcpu": 1,
                "maxmem": 1073741824,
                "maxdisk": 1073741824,
                "template": 0,
            },
            {
                "vmid": 102,
                "type": "qemu",
                "node": "pve1",
                "name": "dup-vm",
                "status": "running",
                "maxcpu": 2,
                "maxmem": 2147483648,
                "maxdisk": 2147483648,
                "template": 0,
            },
        ]
        adapter = ProxmoxDiffSync(
            job=MagicMock(),
            sync=MagicMock(),
            client=self.client,
            config=_default_config(),
            cluster_filters=None,
        )
        adapter.load()
        dup_vms = [vm for vm in adapter.get_all("virtual_machine") if vm.name == "dup-vm"]
        self.assertEqual(len(dup_vms), 1)
        self.assertEqual(dup_vms[0].vcpus, 1)

    def test_load_clusters_use_clusters_false_uses_default_cluster_name(self):
        """With use_clusters disabled, the default cluster name is used."""
        config = _default_config()
        config.use_clusters = False
        adapter = ProxmoxDiffSync(
            job=MagicMock(),
            sync=MagicMock(),
            client=self.client,
            config=config,
            cluster_filters=None,
        )
        adapter.load_clusters()
        self.assertEqual(adapter.cluster_name, config.default_cluster_name)
        self.assertIsNotNone(adapter.get("cluster", config.default_cluster_name))


class TestProxmoxAdapterVLANs(unittest.TestCase):
    """VLANs from VM/container ``netN`` settings and node VLAN interfaces."""

    def setUp(self):
        """Load the adapter from JSON fixtures through a mocked client."""
        self.client, self.adapter = _load_fixture_adapter()

    def test_qemu_tag_gives_access_mode_and_untagged_vlan(self):
        """A QEMU netN with tag=10 is an access interface on VLAN 10 in the config's VLAN group."""
        interface = self.adapter.get("interface", {"name": "net0", "virtual_machine__name": "web01"})
        self.assertEqual(interface.mode, "access")
        self.assertEqual(interface.untagged_vlan__vid, 10)
        self.assertEqual(interface.untagged_vlan__vlan_group__name, "Proxmox Test Config")
        self.assertTrue(self.adapter.get("vlan", {"vid": 10, "vlan_group__name": "Proxmox Test Config"}))

    def test_qemu_trunks_give_tagged_mode(self):
        """trunks=20;30 with tag=10 is a tagged interface: VLANs 20 and 30 tagged, 10 untagged."""
        adapter = _load_single_qemu_vm({"net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=10,trunks=20;30"})

        interface = adapter.get("interface", {"name": "net0", "virtual_machine__name": "web01"})
        self.assertEqual(interface.mode, "tagged")
        self.assertEqual(interface.untagged_vlan__vid, 10)
        self.assertEqual(
            interface.tagged_vlans,
            [
                {"vid": 20, "vlan_group__name": "Proxmox Test Config"},
                {"vid": 30, "vlan_group__name": "Proxmox Test Config"},
            ],
        )

    def test_lxc_tag_gives_access_mode(self):
        """An LXC netN with tag= is handled like a QEMU one."""
        self.client.get_lxc_config.return_value = {
            "net0": "name=eth0,bridge=vmbr0,hwaddr=AA:BB:CC:DD:EE:02,ip=10.0.20.10/24,tag=40,type=veth"
        }
        adapter = ProxmoxDiffSync(
            job=MagicMock(), sync=MagicMock(), client=self.client, config=_default_config(), cluster_filters=None
        )
        adapter.load()

        interface = adapter.get("interface", {"name": "eth0", "virtual_machine__name": "ct-dns"})
        self.assertEqual((interface.mode, interface.untagged_vlan__vid), ("access", 40))

    def test_sync_vlans_off_records_no_vlans(self):
        """With sync_vlans off, no VLANs are loaded and interfaces carry no VLAN settings."""
        config = _default_config()
        config.sync_vlans = False
        adapter = _load_single_qemu_vm(
            {"net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=10,trunks=20"}, config=config
        )

        interface = adapter.get("interface", {"name": "net0", "virtual_machine__name": "web01"})
        self.assertEqual((interface.mode, interface.untagged_vlan__vid, interface.tagged_vlans), ("", None, []))
        self.assertEqual(adapter.get_all("vlan"), [])
        self.assertEqual(adapter.get_all("vlangroup"), [])

    def test_node_vlan_interface_gets_its_vlan(self):
        """A node vlan interface is an access interface on its VLAN, from vlan-id or a name.vid suffix."""
        adapter = _load_single_qemu_vm(
            {},
            node_network=[
                {"iface": "eth0", "type": "eth", "active": 1},
                {"iface": "vlan50", "type": "vlan", "active": 1, "vlan-id": 50, "vlan-raw-device": "eth0"},
                {"iface": "eth0.60", "type": "vlan", "active": 1},
            ],
        )

        for name, vid in (("vlan50", 50), ("eth0.60", 60)):
            interface = adapter.get("device_interface", {"name": name, "device__name": "pve1"})
            self.assertEqual((interface.mode, interface.untagged_vlan__vid), ("access", vid))
            self.assertEqual(interface.untagged_vlan__vlan_group__name, "Proxmox Test Config")
        plain = adapter.get("device_interface", {"name": "eth0", "device__name": "pve1"})
        self.assertEqual((plain.mode, plain.untagged_vlan__vid), ("", None))


class TestProxmoxAdapterCloudInit(unittest.TestCase):
    """QEMU VM addresses from Proxmox VE cloud-init settings (``ipconfigN``)."""

    def _load(self, qemu_config, agent_interfaces=None):
        """Load an adapter whose only VM is ``web01`` with the given QEMU config.

        Args:
            qemu_config (dict): The QEMU VM config returned by the client.
            agent_interfaces (list): Guest agent interfaces; empty means the agent is unavailable.

        Returns:
            ProxmoxDiffSync: The loaded adapter.
        """
        return _load_single_qemu_vm(qemu_config, agent_interfaces)

    def test_cloudinit_address_used_without_agent(self):
        """With no guest agent data, ipconfig0's address is put on net0 and used as the primary IPv4."""
        adapter = self._load(
            {"net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0", "ipconfig0": "ip=10.0.10.60/24,gw=10.0.10.1"}
        )

        ip = adapter.get("ip_address", "10.0.10.60__24__Active")
        self.assertEqual(ip.vm_interfaces, [{"name": "net0", "virtual_machine__name": "web01"}])
        vm = adapter.get("virtual_machine", {"name": "web01", "cluster__name": "pve-cluster"})
        self.assertEqual(vm.primary_ip4__host, "10.0.10.60")

    def test_agent_addresses_take_precedence(self):
        """When the guest agent reports addresses for an interface, ipconfigN isn't used for it."""
        adapter = self._load(
            {"net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0", "ipconfig0": "ip=10.0.10.60/24"},
            agent_interfaces=json_fixture(f"{real_path}/qemu_agent_interfaces.json"),
        )

        self.assertTrue(adapter.get("ip_address", "10.0.10.50__24__Active"))
        with self.assertRaises(ObjectNotFound):
            adapter.get("ip_address", "10.0.10.60__24__Active")

    def test_ipconfig_maps_to_matching_net_index(self):
        """ipconfig1 belongs to net1; an ipconfigN without a netN is ignored."""
        adapter = self._load(
            {
                "net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0",
                "net1": "virtio=AA:BB:CC:DD:EE:03,bridge=vmbr1",
                "ipconfig1": "ip=192.0.2.10/24",
                "ipconfig5": "ip=198.51.100.10/24",
            }
        )

        ip = adapter.get("ip_address", "192.0.2.10__24__Active")
        self.assertEqual(ip.vm_interfaces, [{"name": "net1", "virtual_machine__name": "web01"}])
        with self.assertRaises(ObjectNotFound):
            adapter.get("ip_address", "198.51.100.10__24__Active")

    def test_dynamic_settings_record_nothing_and_static_ipv6_is_recorded(self):
        """ip=dhcp and ip6=auto give no address; a static ip6 is recorded."""
        adapter = self._load(
            {
                "net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0",
                "net1": "virtio=AA:BB:CC:DD:EE:03,bridge=vmbr1",
                "ipconfig0": "ip=dhcp,ip6=auto",
                "ipconfig1": "ip6=2001:db8::10/64",
            }
        )

        self.assertEqual([ip.host for ip in adapter.get_all("ip_address")], ["2001:db8::10"])

    def test_cloudinit_credentials_are_never_read(self):
        """ciuser, cipassword and sshkeys from the same config never reach synced data or the job log."""
        secrets = ("ci-user-marker", "ci-password-marker", "ssh-key-marker")
        adapter = self._load(
            {
                "net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0",
                "ipconfig0": "ip=10.0.10.60/24",
                "ciuser": secrets[0],
                "cipassword": secrets[1],
                "sshkeys": secrets[2],
            }
        )

        synced = repr(adapter.dict()) + repr(adapter.job.mock_calls)
        for value in secrets:
            self.assertNotIn(value, synced)


class TestProxmoxHelpers(unittest.TestCase):
    """Test cases for the Proxmox adapter helper functions."""

    def test_parse_net_config_qemu(self):
        """A QEMU net string is parsed and its MAC extracted."""
        parts = parse_net_config("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=10")
        self.assertEqual(parts["virtio"], "AA:BB:CC:DD:EE:01")
        self.assertEqual(parts["bridge"], "vmbr0")
        self.assertEqual(mac_from_net_parts(parts), "AA:BB:CC:DD:EE:01")

    def test_parse_net_config_lxc(self):
        """An LXC net string is parsed and its MAC extracted."""
        parts = parse_net_config("name=eth0,bridge=vmbr0,hwaddr=AA:BB:CC:DD:EE:02,ip=10.0.20.10/24")
        self.assertEqual(parts["name"], "eth0")
        self.assertEqual(parts["ip"], "10.0.20.10/24")
        self.assertEqual(mac_from_net_parts(parts), "AA:BB:CC:DD:EE:02")

    def test_byte_conversions(self):
        """Byte counts convert to MB/GB, and empty values return None."""
        self.assertEqual(bytes_to_mb(4294967296), 4096)
        self.assertEqual(bytes_to_gb(34359738368), 32)
        self.assertIsNone(bytes_to_mb(None))
        self.assertIsNone(bytes_to_gb(0))
